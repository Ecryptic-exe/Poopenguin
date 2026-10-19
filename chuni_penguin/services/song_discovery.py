"""Rank human song abbreviations without changing canonical identities."""

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from chuni_penguin.database import Song


def search_key(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def compact(value):
    return "".join(character for character in search_key(value) if character.isalnum())


def abbreviation_score(query, value):
    query, value = search_key(query), search_key(value)
    if query == value:
        return 100.0
    short, full = compact(query), compact(value)
    if not short or not full:
        return 0.0
    if short == full:
        return 99.0
    score = float(fuzz.WRatio(query, value))
    words = re.findall(r"[a-z0-9]+", re.sub(r"['’]s\b", "", value))
    initials = "".join(word[0] for word in words)
    if len(short) >= 2 and len(words) >= 2 and short == initials:
        score = max(score, 96.0)
    if len(short) >= 2 and full.startswith(short):
        score = max(score, 92.0)
    return score


@dataclass(frozen=True)
class SongCandidate:
    song: Song
    score: float
    method: str
    variants: tuple[Song, ...] = ()

    @property
    def songs(self) -> tuple[Song, ...]:
        return (self.song, *self.variants)


@dataclass(frozen=True)
class DiscoveryResult:
    query: str
    candidates: tuple[SongCandidate, ...]
    needs_choice: bool
    exact_alias: bool


class SongDiscoveryService:
    def __init__(self, database, aliases):
        self.database, self.aliases = database, aliases

    async def search(
        self,
        query,
        *,
        guild_id=None,
        ignore_exact=False,
        exclude_song_id=None,
        excluded_song_ids=(),
    ):
        query = search_key(query)
        if not query or len(query) > 100:
            raise ValueError("請輸入 1–100 字的歌曲名稱或簡寫。")
        cached = (
            [
                *self.aliases.alias_cache.get(0, []),
                *self.aliases.alias_cache.get(guild_id, []),
            ]
            if guild_id
            else list(self.aliases.alias_cache.get(0, []))
        )
        ranked = {}
        title_scores = {}
        target_scores = {}
        exact_alias = False
        for entry in cached:
            exact = search_key(entry.alias) == query
            if ignore_exact and exact and entry.id is not None:
                continue
            score = abbreviation_score(query, entry.alias)
            method = (
                "alias"
                if entry.id is not None
                else "title"
                if search_key(entry.alias) == search_key(entry.title)
                else "artist"
            )
            source_scores = title_scores if method == "title" else target_scores
            source_key = entry.title if method == "title" else entry.song_id
            source_previous = source_scores.get(source_key)
            if source_previous is None or score > source_previous[0]:
                source_scores[source_key] = (score, method)
            previous = ranked.get(entry.title)
            if (
                previous is None
                or score > previous[0]
                or (score == previous[0] and method == "title")
            ):
                ranked[entry.title] = (score, method)
            exact_alias |= exact and entry.id is not None
        ranked = sorted(ranked.items(), key=lambda item: (-item[1][0], item[0]))
        if not ranked or ranked[0][1][0] < 45:
            return DiscoveryResult(query, (), False, False)
        best = ranked[0][1][0]
        ranked = [
            (title, score_method)
            for title, score_method in ranked
            if score_method[0] >= (45 if ignore_exact else max(45, best - 12))
        ][: 25 if ignore_exact else 10]
        if best == 100 and not ignore_exact:
            ranked = [
                (title, score_method)
                for title, score_method in ranked
                if score_method[0] == 100
            ]
        scores = dict(ranked)
        async with self.database.mapping_read_sessionmaker() as session:
            statement = (
                select(Song)
                .where(Song.title.in_(scores))
                .options(selectinload(Song.aliases), selectinload(Song.charts))
            )
            excluded = set(excluded_song_ids)
            if exclude_song_id is not None:
                excluded.add(exclude_song_id)
            if excluded:
                statement = statement.where(Song.id.not_in(excluded))
            songs = list(await session.scalars(statement))
        families = {}
        for song in songs:
            families.setdefault(
                (search_key(song.title), search_key(song.artist)), []
            ).append(song)
        grouped = []
        for family in families.values():
            normal = [song for song in family if song.genre != "WORLD'S END"]
            # Multiple ordinary records may represent distinct editions. Do not
            # infer they are interchangeable merely from matching title/artist.
            batches = [family] if len(normal) <= 1 else [[song] for song in family]
            for members in batches:
                matched = []
                for song in members:
                    metrics = [
                        value
                        for value in (
                            title_scores.get(song.title),
                            target_scores.get(song.id),
                        )
                        if value is not None
                    ]
                    if metrics:
                        matched.append(
                            (
                                song,
                                *max(
                                    metrics,
                                    key=lambda value: (value[0], value[1] == "title"),
                                ),
                            )
                        )
                if not matched:
                    continue
                best_match = max(matched, key=lambda row: (row[1], row[2] == "title"))
                score, method = best_match[1:]
                ordered = sorted(
                    members,
                    key=lambda song: (
                        song.genre == "WORLD'S END",
                        not song.available,
                        song.removed,
                        song.id,
                    ),
                )
                representative = ordered[0]
                if method == "alias":
                    # A specific WE alias must still open that exact record.
                    representative = next(
                        song
                        for song in ordered
                        if any(
                            row[0].id == song.id and row[1:] == (score, method)
                            for row in matched
                        )
                    )
                grouped.append(
                    SongCandidate(
                        representative,
                        score,
                        method,
                        tuple(song for song in ordered if song.id != representative.id),
                    )
                )
        candidates = tuple(
            sorted(
                grouped,
                key=lambda row: (-row.score, row.song.id),
            )
        )
        if best == 100 and not ignore_exact:
            candidates = tuple(
                candidate for candidate in candidates if candidate.score == 100
            )
        if candidates:
            candidates = tuple(
                c for c in candidates if c.score >= max(45, candidates[0].score - 12)
            )[:25]
        # Multiple exact aliases/titles and near ties require a real selection.
        clear = len(candidates) == 1 or (
            candidates[0].score - candidates[1].score >= 10
        )
        needs_choice = bool(candidates) and (
            ignore_exact or not clear or candidates[0].score < 90
        )
        return DiscoveryResult(query, candidates, needs_choice, exact_alias)
