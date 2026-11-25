"""User-authorized reads and imports. Never uploads scores or guesses song IDs."""

import asyncio
import json
import math
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select

from chuni_penguin.database import Chart, PersonalBest, PlayerSource, Song
from chuni_penguin.services.native_scores import PartialScoreSync, read_native_scores


class SourceError(Exception):
    pass


class InvalidSourceCredential(SourceError):
    """The source rejected the submitted credential."""


class CredentialVault:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(Fernet.generate_key())
        try:
            self.cipher = Fernet(path.read_bytes())
        except (ValueError, OSError):
            raise SourceError(
                "無法讀取憑證金鑰；請還原原金鑰，勿刪除後重新產生。"
            ) from None

    def seal(self, token: str) -> str:
        return self.cipher.encrypt(token.encode()).decode()

    def open(self, encrypted: str) -> str:
        try:
            return self.cipher.decrypt(encrypted.encode()).decode()
        except InvalidToken:
            raise SourceError("憑證金鑰不符，請重新綁定此來源。") from None


def clean_records(rows, *, chunilib=False):
    if not isinstance(rows, list) or len(rows) > 20000:
        raise SourceError("成績資料格式不正確。")
    difficulties = {
        "Basic": "BAS",
        "Advanced": "ADV",
        "Expert": "EXP",
        "Master": "MAS",
        "Ultima": "ULT",
    }
    records = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise SourceError("成績列格式不正確。")
        value = raw.get("score")
        if type(value) is not int or not 0 <= value <= 1010000:
            raise SourceError("成績分數超出範圍。")
        played = raw.get("is_played")
        if played is False or (value == 0 and played is not True):
            continue
        native = raw.get("idx") if chunilib else raw.get("id")
        diff = raw.get("difficulty") if chunilib else raw.get("diff")
        diff = difficulties.get(diff, diff)
        if (
            not isinstance(native, (str, int))
            or isinstance(native, bool)
            or not str(native)
            or len(str(native)) > 128
            or diff not in ("BAS", "ADV", "EXP", "MAS", "ULT")
        ):
            raise SourceError("成績缺少來源譜面 ID 或有效難度。")
        title = raw.get("title")
        if not isinstance(title, str) or not title or len(title) > 500:
            raise SourceError("成績曲名格式不正確。")
        row = {"id": str(native), "diff": diff, "score": value, "title": title}
        # Keep only this allowlist. No cookies, names, arbitrary JSON or tokens.
        for name in ("is_clear", "is_fullcombo", "is_alljustice", "updated_at"):
            v = raw.get(name)
            if name == "updated_at" and isinstance(v, str) and len(v) < 64:
                row[name] = v
            elif name != "updated_at" and type(v) is bool:
                row[name] = v
        if chunilib:
            row["is_fullcombo"] = raw.get("isFullCombo") is True
            row["is_alljustice"] = raw.get("isAllJustice") is True
        constant = raw.get("const")
        if (
            not chunilib
            and raw.get("is_const_unknown") is False
            and type(constant) in (int, float)
            and math.isfinite(constant)
            and 0 < constant < 20
        ):
            row["source_const"] = constant
        key = (row["id"], diff)
        if key not in records or value > records[key]["score"]:
            records[key] = row
    return list(records.values())


class PlayerSourceService:
    def __init__(self, database, *, vault_path=Path("data/.vault.key"), transport=None):
        self.database = database
        self.vault_path = vault_path
        self.transport = transport
        self.locks = defaultdict(asyncio.Lock)
        self._vault = None
        self._active_native_syncs: set[tuple[int, str]] = set()

    @property
    def vault(self):
        if self._vault is None:
            self._vault = CredentialVault(self.vault_path)
        return self._vault

    async def get(self, user_id, source):
        async with self.database.read_sessionmaker() as session:
            return await session.get(PlayerSource, (user_id, source))

    def native_sync_active(self, user_id: int) -> bool:
        return any(owner == user_id for owner, _ in self._active_native_syncs)

    async def sync_native(self, user_id: int, source: str, network):
        key = (user_id, source)
        if key in self._active_native_syncs:
            msg = "A sync is already in progress for this source. Wait for it to finish."
            raise SourceError(msg)
        self._active_native_syncs.add(key)

        async def save_status(status):
            async def write(db):
                row = await db.get(PlayerSource, key)
                if row is None:
                    row = PlayerSource(discord_id=user_id, source=source)
                    db.add(row)
                row.status = status
                if status == "synced":
                    row.synced_at = datetime.now(UTC).isoformat()

            await self.database.writer.execute_fn(write)

        try:
            async with network as client:
                _, recent, bests = await read_native_scores(client)
        except asyncio.CancelledError:
            await save_status("failed; partial cache possible")
            raise
        except PartialScoreSync:
            await save_status("partial; recent scores read")
            raise
        except Exception:
            await save_status("failed")
            raise
        else:
            await save_status("synced")
            return client.NAME, recent, bests
        finally:
            self._active_native_syncs.discard(key)

    async def _fetch(self, token, region):
        if region not in ("jp", "jp2"):
            raise SourceError("Chunirec 官方目前列出的 region 為 jp / jp2。")
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=20, follow_redirects=False
            ) as client:
                output = []
                for endpoint in ("profile", "showall"):
                    async with client.stream(
                        "GET",
                        f"https://api.chunirec.net/2.0/records/{endpoint}.json",
                        params={"region": region, "token": token},
                    ) as response:
                        if response.status_code != 200:
                            if response.status_code == 401:
                                msg = "憑證無效。請重新連結帳號。"
                                raise InvalidSourceCredential(msg)
                            messages = {
                                403: "來源拒絕存取。",
                                404: "此區域沒有玩家資料。",
                                429: "來源請求額度已用完，請稍後再試。",
                            }
                            raise SourceError(
                                messages.get(
                                    response.status_code, "Chunirec 暫時無法讀取。"
                                )
                            )
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 8 * 1024 * 1024:
                                raise SourceError("來源資料過大。")
                        output.append(json.loads(data))
        except (httpx.HTTPError, ValueError):
            # Request URLs contain token. Never propagate HTTP exceptions.
            raise SourceError("Chunirec 讀取失敗；未更新成績。") from None
        profile, records = output
        if (
            not isinstance(profile, dict)
            or type(profile.get("user_id")) is not int
            or not isinstance(records, dict)
        ):
            raise SourceError("Chunirec 回應格式不符；未更新成績。")
        rating = profile.get("rating")
        if rating is not None and (
            type(rating) not in (int, float)
            or not math.isfinite(rating)
            or not 0 <= rating < 30
        ):
            raise SourceError("Chunirec Rating 格式不符。")
        return {
            "account_id": str(profile["user_id"]),
            "rating": rating,
            "records": clean_records(records.get("records")),
            "history_supported": False,
        }

    async def sync_chunirec(self, user_id, *, token=None, region="jp2"):
        source = f"chunirec:{region}"
        async with self.locks[user_id]:
            old = await self.get(user_id, source)
            using_saved_token = token is None
            if token is None:
                if old is None or old.credential is None:
                    raise SourceError(
                        "請先用 /account link 綁定自己的 Chunirec Token。"
                    )
                token = self.vault.open(old.credential)
            try:
                payload = await self._fetch(token, region)
                if (
                    old is not None
                    and old.payload
                    and json.loads(old.payload)["account_id"] != payload["account_id"]
                ):
                    raise SourceError("Token 持有者已變更，請先解除綁定。")
                await self._save(user_id, source, payload, self.vault.seal(token))
            except SourceError as error:
                if old is not None and using_saved_token:
                    status = (
                        "failed; invalid credential"
                        if isinstance(error, InvalidSourceCredential)
                        else "failed; previous snapshot retained"
                    )
                    await self.database.writer.execute_fn(
                        lambda session: self._status(
                            session,
                            user_id,
                            source,
                            status,
                        )
                    )
                raise
            return payload

    async def _status(self, session, user_id, source, status):
        row = await session.get(PlayerSource, (user_id, source))
        if row is not None:
            row.status = status

    async def import_chunilib(self, user_id, raw: bytes):
        if len(raw) > 5 * 1024 * 1024:
            raise SourceError("匯出檔不可超過 5 MiB。")
        try:
            obj = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeError):
            raise SourceError("匯出檔不是有效 JSON。") from None
        if not isinstance(obj, dict):
            raise SourceError("需要 CHUNITHM player-data 匯出檔。")
        payload = {
            "records": clean_records(obj.get("score"), chunilib=True),
            "history_supported": False,
            "rating": None,
            "account_id": "user-supplied-export",
        }
        if not payload["records"]:
            raise SourceError("匯出檔沒有已遊玩成績。")
        async with self.locks[user_id]:
            await self._save(user_id, "chunilib:export", payload, None)
        return payload

    async def _save(self, user_id, source, payload, credential):
        # A's canonical IDs and explicit Chunirec ID column are authoritative.
        # Chunilib idx is a different namespace: leave unbound until reviewed.
        async with self.database.read_sessionmaker() as session:
            mapping = {}
            if source.startswith("chunirec:"):
                pairs = (
                    await session.execute(
                        select(Song.chunirec_id, Chart.difficulty, Song.id).join(Chart)
                    )
                ).all()
                for external_id, difficulty, canonical_id in pairs:
                    key = (external_id, difficulty)
                    mapping.setdefault(key, []).append(canonical_id)
        mapped = []
        for record in payload["records"]:
            matches = mapping.get((record["id"], record["diff"]), [])
            record["song_id"] = matches[0] if len(matches) == 1 else None
            if record["song_id"] is not None:
                mapped.append(record)
        payload["mapped"] = len(mapped)
        payload["unmapped"] = len(payload["records"]) - len(mapped)
        now = datetime.now(UTC).isoformat()

        async def write(session):
            await session.execute(
                delete(PersonalBest).where(
                    PersonalBest.discord_id == user_id, PersonalBest.network == source
                )
            )
            for record in mapped:
                session.add(
                    PersonalBest(
                        discord_id=user_id,
                        network=source,
                        song_id=record["song_id"],
                        difficulty=record["diff"],
                        score=record["score"],
                        clear_lamp=1 if record.get("is_clear") else 0,
                        combo_lamp=2
                        if record.get("is_alljustice")
                        else (1 if record.get("is_fullcombo") else 0),
                    )
                )
            await session.merge(
                PlayerSource(
                    discord_id=user_id,
                    source=source,
                    credential=credential,
                    payload=json.dumps(payload, ensure_ascii=False),
                    synced_at=now,
                    status="synced",
                )
            )

        # Shield queue completion while holding the lock: unlink cannot race a canceled sync.
        task = self.database.writer.execute_fn(write)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def unlink(self, user_id, source):
        async with self.locks[user_id]:

            async def write(session):
                await session.execute(
                    delete(PlayerSource).where(
                        PlayerSource.discord_id == user_id,
                        PlayerSource.source == source,
                    )
                )
                await session.execute(
                    delete(PersonalBest).where(
                        PersonalBest.discord_id == user_id,
                        PersonalBest.network == source,
                    )
                )

            await self.database.writer.execute_fn(write)

    async def records(self, user_id, source):
        row = await self.get(user_id, source)
        if row is None or row.payload is None:
            raise SourceError("此來源尚無成績，請從 /account 同步或匯入。")
        return json.loads(row.payload), row
