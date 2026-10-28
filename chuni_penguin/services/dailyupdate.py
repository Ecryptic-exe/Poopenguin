"""Daily CHUNITHM-NET baselines and observations, without stored credentials."""

import asyncio
import hashlib
import math
import os
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import msgspec

from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
from chuni_penguin.adapters.errors import NetworkError
from chuni_penguin.constants import ASSETS_DIR
from chuni_penguin.types import ComboLamp, Difficulty, Rank, RatingType

if TYPE_CHECKING:
    from chuni_penguin.context import PenguinContext
    from chuni_penguin.services.accounts import AccountService
    from chuni_penguin.types import PersonalBest, Profile

HK = ZoneInfo("Asia/Hong_Kong")
HISTORY_DAYS = 30
REQUEST_DELAY = 0.35
SNAPSHOT_DIR = ASSETS_DIR.parent / "data" / "dailyupdate"
JACKET_DIR = ASSETS_DIR / "jackets"
FONT_PATH = ASSETS_DIR / "dailyupdate" / "NotoSansCJKjp-Regular.otf"
RANKS = ("S", "S+", "SS", "SS+", "SSS", "SSS+", "MAX")


def current_time() -> datetime:
    return datetime.now(HK)


def window_start(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        msg = "Snapshot times must include a timezone."
        raise ValueError(msg)
    local = moment.astimezone(HK)
    day = (local - timedelta(hours=4)).date()
    return datetime.combine(day, time(4), HK)


class ChartState(msgspec.Struct, frozen=True, kw_only=True):
    song_id: int
    title: str
    difficulty: str
    level: str
    score: int
    rank: str
    lamp: str
    clear_lamp: str
    jacket_url: str | None = None

    @property
    def key(self) -> str:
        return f"{self.song_id}:{self.difficulty}"


class Snapshot(msgspec.Struct, frozen=True, kw_only=True):
    captured_at: datetime
    window_start: datetime
    window_end: datetime
    player_name: str
    level: int
    rating: float
    total_score: int
    over_power: float
    over_power_percentage: float
    rank_counts: dict[str, int]
    lamp_counts: dict[str, int]
    charts: dict[str, ChartState]


class WindowSnapshots(msgspec.Struct, frozen=True, kw_only=True):
    baseline: Snapshot
    latest: Snapshot


class History(msgspec.Struct, kw_only=True):
    schema_version: int = 2
    windows: dict[str, WindowSnapshots] = msgspec.field(default_factory=dict)


class LegacyHistory(msgspec.Struct, kw_only=True):
    schema_version: int = 1
    snapshots: list[Snapshot] = msgspec.field(default_factory=list)


class HistoryVersion(msgspec.Struct):
    schema_version: int = 1


class ExpiredSession(msgspec.Struct, kw_only=True):
    cookie_digest: str
    marked_at: datetime


class SnapshotStorageError(Exception):
    """A damaged history must never be silently replaced with a new one."""


class MissingSnapshotError(Exception):
    """The requested historical comparison cannot be reconstructed."""


class SnapshotStore:
    def __init__(self, directory: Path = SNAPSHOT_DIR):
        self.directory = directory

    def _path(self, user_id: int) -> Path:
        if type(user_id) is not int or user_id <= 0:
            msg = "Invalid Discord user ID."
            raise ValueError(msg)
        return self.directory / f"{user_id}.json"

    @staticmethod
    def _validate_snapshot(snapshot: Snapshot) -> None:
        if (
            snapshot.captured_at.tzinfo is None
            or snapshot.window_start != window_start(snapshot.captured_at)
            or snapshot.window_end != snapshot.window_start + timedelta(days=1)
            or any(key != chart.key for key, chart in snapshot.charts.items())
            or set(snapshot.rank_counts) != set(RANKS)
            or set(snapshot.lamp_counts) != {"FC", "AJ"}
        ):
            msg = "Invalid snapshot window, counts, or chart identity."
            raise SnapshotStorageError(msg)

    @staticmethod
    def _retained(history: History, now: datetime) -> History:
        cutoff = (
            (window_start(now) - timedelta(days=HISTORY_DAYS - 1)).date().isoformat()
        )
        return History(
            windows={
                key: value
                for key, value in sorted(history.windows.items())
                if key >= cutoff
            }
        )

    def load(self, user_id: int, *, now: datetime | None = None) -> History:
        try:
            data = self._path(user_id).read_bytes()
        except FileNotFoundError:
            return History()
        try:
            version = msgspec.json.decode(data, type=HistoryVersion).schema_version
            if version == 1:
                legacy = msgspec.json.decode(data, type=LegacyHistory)
                history = History()
                for snapshot in sorted(
                    legacy.snapshots, key=lambda item: item.captured_at
                ):
                    self._validate_snapshot(snapshot)
                    key = snapshot.window_start.date().isoformat()
                    saved = history.windows.get(key)
                    history.windows[key] = WindowSnapshots(
                        baseline=saved.baseline if saved else snapshot,
                        latest=snapshot,
                    )
            elif version == 2:
                history = msgspec.json.decode(data, type=History)
            else:
                history = None
        except (msgspec.DecodeError, ValueError, TypeError) as error:
            msg = "Invalid dailyupdate history."
            raise SnapshotStorageError(msg) from error
        if history is None:
            msg = "Unsupported snapshot schema."
            raise SnapshotStorageError(msg)
        for key, saved in history.windows.items():
            self._validate_snapshot(saved.baseline)
            self._validate_snapshot(saved.latest)
            if (
                key != saved.baseline.window_start.date().isoformat()
                or saved.latest.window_start != saved.baseline.window_start
                or saved.latest.captured_at < saved.baseline.captured_at
            ):
                msg = "Invalid baseline/latest pair."
                raise SnapshotStorageError(msg)
        # Migration/expiry are read-only here. A failed fetch must not rewrite
        # even an old-format file; the next successful save persists version 2.
        return self._retained(history, now or current_time())

    @staticmethod
    def _write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            prefix=f"{path.stem}-", suffix=".tmp", dir=path.parent
        )
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def save(
        self, user_id: int, history: History, snapshot: Snapshot
    ) -> WindowSnapshots:
        self._validate_snapshot(snapshot)
        key = snapshot.window_start.date().isoformat()
        existing = history.windows.get(key)
        # Once created, a window's baseline is immutable. Only latest advances.
        saved = WindowSnapshots(
            baseline=existing.baseline if existing else snapshot,
            latest=(
                existing.latest
                if existing and existing.latest.captured_at > snapshot.captured_at
                else snapshot
            ),
        )
        windows = {**history.windows, key: saved}
        retained = self._retained(History(windows=windows), snapshot.captured_at)
        self._write(self._path(user_id), msgspec.json.encode(retained))
        return saved

    def _session_path(self, user_id: int) -> Path:
        return self.directory / "sessions" / self._path(user_id).name

    def session_expired(self, user_id: int, cookie: str) -> bool:
        try:
            state = msgspec.json.decode(
                self._session_path(user_id).read_bytes(), type=ExpiredSession
            )
        except (FileNotFoundError, msgspec.DecodeError):
            return False
        return state.cookie_digest == hashlib.sha256(cookie.encode()).hexdigest()

    def mark_session_expired(self, user_id: int, cookie: str) -> None:
        # Advisory metadata is separate from snapshots. Only a digest is stored;
        # a successful /login changes the cookie and permits another fetch.
        state = ExpiredSession(
            cookie_digest=hashlib.sha256(cookie.encode()).hexdigest(),
            marked_at=current_time(),
        )
        self._write(self._session_path(user_id), msgspec.json.encode(state))

    def clear_session_expired(self, user_id: int) -> None:
        self._session_path(user_id).unlink(missing_ok=True)


def historical_pair(history: History, day: date) -> tuple[Snapshot, Snapshot]:
    saved = history.windows.get(day.isoformat())
    if saved is None:
        msg = "The requested window's baseline/latest pair is missing."
        raise MissingSnapshotError(msg)
    return saved.baseline, saved.latest


def make_snapshot(
    profile: "Profile", records: list["PersonalBest"], moment: datetime
) -> Snapshot:
    rating = next(
        (
            item.value
            for item in profile.rating_systems
            if item.type == RatingType.in_game
        ),
        None,
    )
    if (
        profile.level is None
        or profile.over_power is None
        or rating is None
        or not all(
            math.isfinite(value)
            for value in (
                rating,
                profile.over_power.value,
                profile.over_power.percentage,
            )
        )
    ):
        msg = "CHUNITHM-NET returned incomplete player data."
        raise NetworkError(msg)
    charts = {}
    rank_counts = dict.fromkeys(RANKS, 0)
    lamp_counts = {"FC": 0, "AJ": 0}
    total_score = 0
    for record in records:
        if not 0 <= record.score <= 1_010_000:
            msg = "CHUNITHM-NET returned an invalid score."
            raise NetworkError(msg)
        chart = ChartState(
            song_id=record.song.id,
            title=record.song.title,
            difficulty=record.chart.difficulty.short(),
            level=record.chart.level or "?",
            score=record.score,
            rank="MAX"
            if record.score == 1_010_000
            else str(Rank.from_score(record.score)),
            lamp=record.combo_lamp.short(),
            clear_lamp=record.clear_lamp.short(),
            jacket_url=record.song.jacket_url,
        )
        if chart.key in charts:
            msg = "CHUNITHM-NET returned duplicate chart identities."
            raise NetworkError(msg)
        charts[chart.key] = chart
        if record.chart.difficulty == Difficulty.worlds_end:
            continue
        total_score += record.score
        for rank in Rank:
            if rank.value >= Rank.s.value and record.score >= rank.min_score:
                rank_counts[str(rank)] += 1
        rank_counts["MAX"] += record.score == 1_010_000
        lamp_counts["FC"] += record.combo_lamp.value >= ComboLamp.full_combo.value
        lamp_counts["AJ"] += record.combo_lamp.value >= ComboLamp.all_justice.value
    boundary = window_start(moment)
    return Snapshot(
        captured_at=moment.astimezone(HK),
        window_start=boundary,
        window_end=boundary + timedelta(days=1),
        player_name=profile.username,
        level=profile.level + (profile.reincarnation_stars or 0) * 100,
        rating=rating,
        total_score=total_score,
        over_power=profile.over_power.value,
        over_power_percentage=profile.over_power.percentage,
        rank_counts=rank_counts,
        lamp_counts=lamp_counts,
        charts=charts,
    )


async def fetch_snapshot(
    accounts: "AccountService", ctx: "PenguinContext | int"
) -> Snapshot:
    # Force the same /login CHUNITHM-NET session even if the user also linked
    # another provider. The account manager refreshes/persists cookies itself.
    user_id = ctx if isinstance(ctx, int) else ctx.author.id
    async with accounts.network(ctx, user_id, chunithm_net=True) as client:
        if not isinstance(client, ChunithmNetAdapter):
            msg = "CHUNITHM-NET is required for dailyupdate."
            raise NetworkError(msg)
        await client.get_minimal_profile()  # Establish the authenticated form token.
        records = await client.get_all_personal_bests(
            request_delay=REQUEST_DELAY, validate_pages=True
        )
        await asyncio.sleep(REQUEST_DELAY)
        profile = await client.get_minimal_profile()
    return make_snapshot(profile, records, current_time())


def changed_charts(
    previous: Snapshot, current: Snapshot
) -> list[tuple[ChartState | None, ChartState]]:
    changes = []
    for key, new in current.charts.items():
        old = previous.charts.get(key)
        if old is None:
            if new.score == 0 and new.lamp == "NONE" and new.clear_lamp == "FAILED":
                continue
        elif (old.score, old.rank, old.lamp, old.clear_lamp) == (
            new.score,
            new.rank,
            new.lamp,
            new.clear_lamp,
        ):
            continue
        changes.append((old, new))
    return sorted(
        changes,
        key=lambda pair: (
            -pair[1].score + (pair[0].score if pair[0] else 0),
            pair[1].title,
            pair[1].difficulty,
        ),
    )


def has_changes(previous: Snapshot, current: Snapshot) -> bool:
    return bool(changed_charts(previous, current)) or any(
        getattr(previous, field) != getattr(current, field)
        for field in (
            "player_name",
            "level",
            "rating",
            "total_score",
            "over_power",
            "over_power_percentage",
            "rank_counts",
            "lamp_counts",
        )
    )
