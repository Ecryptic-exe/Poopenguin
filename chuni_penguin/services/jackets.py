"""Bounded public artwork downloads for the existing local-image renderers."""

import asyncio
import json
import os
import tempfile
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from chuni_penguin.constants import ASSETS_DIR

PUBLIC_ART_HOSTS = frozenset(
    {
        "chunithm-net-eng.com",
        "new.chunithm-net.com",
        "chunithm.sega.jp",
        "dp4p6x0xfi5o9.cloudfront.net",
        "chunithm.beerpsi.cc",
        "maimaidx-eng.com",
        "maimaidx.jp",
        "mimixd.app",
    }
)


def public_art_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        return False
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and parts.hostname in PUBLIC_ART_HOSTS
            and parts.port in (None, 443)
            and not parts.username
            and not parts.password
            and not parts.query
            and not parts.fragment
        )
    except ValueError:
        return False


class JacketCache:
    def __init__(self, *, directory=None, transport=None, seeds=None):
        self.directory = (
            Path(directory) if directory is not None else ASSETS_DIR / "jackets"
        )
        self.transport = transport
        self.seed_path = (
            seeds or Path(__file__).resolve().parents[1] / "database/seeds/songs.json"
        )
        self._seeds = None
        self._locks = {}
        self._missing = {}
        self._limit = asyncio.Semaphore(6)

    def candidates(self, song):
        if self._seeds is None:
            rows = json.loads(self.seed_path.read_text(encoding="utf-8"))
            self._seeds = {row["id"]: row.get("jackets", []) for row in rows}
        urls = [song.jacket_url, *self._seeds.get(song.id, ())]
        return list(dict.fromkeys(url for url in urls if public_art_url(url)))

    @staticmethod
    def _valid(path):
        try:
            with Image.open(path) as image:
                if image.width > 4096 or image.height > 4096:
                    return False
                image.load()
            return True
        except (OSError, ValueError, Image.DecompressionBombError):
            return False

    def _save(self, path, data):
        with Image.open(BytesIO(data)) as image:
            if image.width > 4096 or image.height > 4096:
                raise ValueError("artwork_dimensions_exceeded")
            image.load()
            converted = image.convert("RGB")
        try:
            converted.thumbnail((512, 512), Image.Resampling.LANCZOS)
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(
                prefix="jacket-", suffix=".tmp", dir=self.directory
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    converted.save(stream, format="WEBP", quality=92)
                    stream.flush()
                Path(name).replace(path)
            finally:
                Path(name).unlink(missing_ok=True)
        finally:
            converted.close()

    async def _load(self, song):
        path = self.directory / f"{song.id}.webp"
        if await asyncio.to_thread(self._valid, path):
            return "cached"
        if self._missing.get(song.id, 0) > time.monotonic():
            return "missing"
        async with self._limit:
            async with httpx.AsyncClient(
                timeout=5, follow_redirects=False, transport=self.transport
            ) as client:
                for url in self.candidates(song):
                    try:
                        # A fresh public client sends no player cookies or tokens.
                        for _ in range(3):
                            async with client.stream("GET", url) as response:
                                if response.is_redirect:
                                    location = str(
                                        response.url.join(
                                            response.headers.get("location", "")
                                        )
                                    )
                                    if not public_art_url(location):
                                        break
                                    url = location
                                    continue
                                if response.status_code != 200:
                                    break
                                raw = bytearray()
                                async for chunk in response.aiter_bytes():
                                    raw.extend(chunk)
                                    if len(raw) > 5 * 1024 * 1024:
                                        raise ValueError("artwork_size_exceeded")
                                await asyncio.to_thread(self._save, path, raw)
                                self._missing.pop(song.id, None)
                                return "downloaded"
                            break
                    except (
                        httpx.HTTPError,
                        OSError,
                        ValueError,
                        UnidentifiedImageError,
                        Image.DecompressionBombError,
                    ):
                        continue
        self._missing[song.id] = time.monotonic() + 300
        if len(self._missing) > 4096:
            self._missing = {
                key: until
                for key, until in self._missing.items()
                if until > time.monotonic()
            }
        return "missing"

    async def ensure(self, records):
        songs = {
            record.song.id: record.song
            for record in records
            if type(record.song.id) is int and record.song.id >= 0
        }
        songs = dict(list(songs.items())[:120])

        async def one(song):
            # Serialize a song's cache fill without sharing task ownership. Cancelling
            # one B50 request must not cancel another player's pending download.
            lock, users = self._locks.get(song.id, (asyncio.Lock(), 0))
            self._locks[song.id] = (lock, users + 1)
            try:
                async with lock:
                    return await self._load(song)
            finally:
                _, users = self._locks[song.id]
                if users == 1:
                    self._locks.pop(song.id)
                else:
                    self._locks[song.id] = (lock, users - 1)

        tasks = [asyncio.create_task(one(song)) for song in songs.values()]
        try:
            async with asyncio.timeout(25):
                states = await asyncio.gather(*tasks)
        except (TimeoutError, asyncio.CancelledError) as error:
            # Complete or cancel the downloads before the renderer reads files.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if isinstance(error, asyncio.CancelledError):
                raise
            states = [
                "cached" if self._valid(self.directory / f"{key}.webp") else "missing"
                for key in songs
            ]
        return {
            "requested": len(songs),
            **{key: states.count(key) for key in ("cached", "downloaded", "missing")},
        }
