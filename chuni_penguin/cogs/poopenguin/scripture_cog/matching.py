# ruff: noqa: RUF001 -- Perlica preserves original fullwidth punctuation.
"""Local, whole-message lookup of edition-specific quotation records."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import chain, pairwise
from pathlib import Path
from typing import Callable

from .retrieval import BM25, CJK, WORDS

MIN_WORD_CHARACTERS = 6
MAX_QUOTE_LENGTH = 1800
TERMINALS = "。.!?"
QUOTE_PAIRS = {"「": "」", "『": "』", '"': '"', "“": "”", "‘": "’", "＂": "＂"}
COMMON_PHRASES = (
    "我不知道",
    "我知道了",
    "我明白了",
    "這是甚麼意思",
    "這是什麼意思",
    "這是甚麼意思呢",
    "這是什麼意思呢",
    "你們在做甚麼",
    "你們在做什麼",
    "你要做甚麼",
    "你要做什麼",
)
COMMON_ENGLISH_CHAT = (
    "thank you",
    "thank you very much",
    "I don't know",
    "what are you doing",
    "I will come",
)
VERSE = re.compile(r"[1-9][0-9]*[a-z]?(?:[–-](?:[1-9][0-9]*:)?[1-9][0-9]*[a-z]?)?")
VERSE_BREAKS = re.compile(r"""[。！？!?…；;：:，、,.「」『』“”"]+""")
PUNCTUATION = str.maketrans({"、": ",", "。": ".", "‘": "'", "’": "'"})

MATCH_MODES = ("strict", "variants", "bm25")
DEFAULT_MATCH_THRESHOLD = 0.85
SETTING_NAMES = {
    "channel_ids",
    "editions",
    "blocked_phrases",
    "match_mode",
    "match_threshold",
    "aliases",
}


def _fold_width(value: str) -> str:
    # Full/half-width variants are compatible. Other compatibility characters
    # (such as ① and ㎏) are deliberately kept distinct.
    chars = []
    for char in value:
        decomposition = unicodedata.decomposition(char).split()
        if decomposition and decomposition[0] in ("<wide>", "<narrow>"):
            chars.extend(chr(int(codepoint, 16)) for codepoint in decomposition[1:])
        else:
            chars.append(char)
    return unicodedata.normalize("NFC", "".join(chars))


def normalize(value: str) -> str:
    text = _fold_width(value).strip()
    for _ in range(2):
        text = text.rstrip(TERMINALS).rstrip()
        if len(text) < 2 or QUOTE_PAIRS.get(text[0]) != text[-1]:
            break
        if text[0] in text[1:-1] or text[-1] in text[1:-1]:
            break
        text = text[1:-1].strip()

    text = text.translate(PUNCTUATION).rstrip(TERMINALS).rstrip()
    # Commas, list separators, colons and semicolons can be omitted in prose.
    # Preserve separators within numbers, ASCII word boundaries and symbols.
    text = re.sub(r"(?<!\d)[,;:]|[,;:](?!\d)", " ", text)
    # Internal sentence stops remain significant, including decimal points.
    return _join_words(text)


def _join_words(text: str) -> str:
    parts = text.split()
    if not parts:
        return ""
    joined = [parts[0]]
    for left, right in pairwise(parts):
        if (
            left[-1].isalnum()
            and right[0].isalnum()
            and not (CJK.fullmatch(left[-1]) and CJK.fullmatch(right[0]))
        ):
            joined.append(" ")
        joined.append(right)
    return "".join(joined)


def normalize_bm25(value: str) -> str:
    text = "".join(
        char
        for char in _fold_width(value)
        if char != "~" and not unicodedata.category(char).startswith("P")
    )
    return _join_words(text).casefold()


def _word_windows(verse: str, count: int):
    words = tuple(WORDS.finditer(verse))
    for start, end in zip(words, words[count - 1 :], strict=False):
        yield verse[start.start() : end.end()]


def validate_match_threshold(value: object) -> float:
    if (type(value) is not int and type(value) is not float) or not 0 <= value <= 1:
        detail = "match_threshold must be a number from 0 to 1"
        raise ValueError(detail)
    return float(value)


def variant_key(key: str) -> str:
    return key.replace("裏", "裡").replace("甚麼", "什麼")


def has_minimum_length(key: str) -> bool:
    return sum(char.isalnum() for char in key) >= MIN_WORD_CHARACTERS


@dataclass(frozen=True, slots=True)
class Quote:
    text: str
    reference: str
    edition: str = ""

    @property
    def citation(self) -> str:
        return f"{self.reference}（{self.edition}）" if self.edition else self.reference


@dataclass(frozen=True, slots=True)
class Match:
    quote: Quote
    score: float


@dataclass(frozen=True, slots=True)
class Settings:
    channel_ids: frozenset[int]
    edition_paths: tuple[Path, ...]
    blocked_phrases: tuple[str, ...]
    match_mode: str
    aliases: dict[str, str]
    match_threshold: float = DEFAULT_MATCH_THRESHOLD


def parse_channel_id(channel: str) -> int:
    if (
        not isinstance(channel, str)
        or not re.fullmatch(r"[1-9][0-9]{0,19}", channel)
        or int(channel) >= 2**64
    ):
        detail = "channel_ids must be positive decimal Discord IDs written as strings"
        raise ValueError(detail)
    return int(channel)


def load_settings(path: Path) -> Settings:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.keys() - SETTING_NAMES:
        detail = "settings.json contains an unrecognized setting"
        raise ValueError(detail)

    channels = config.get("channel_ids", [])
    if not isinstance(channels, list):
        detail = "channel_ids must be a list of decimal Discord IDs written as strings"
        raise ValueError(detail)  # noqa: TRY004 -- preserve the settings validation contract.
    channel_ids = frozenset(parse_channel_id(channel) for channel in channels)

    editions = config.get("editions")
    if (
        not isinstance(editions, list)
        or not editions
        or any(
            not isinstance(edition, str) or not edition.strip() for edition in editions
        )
    ):
        detail = "editions must be a nonempty list of local JSONL filenames"
        raise ValueError(detail)

    blocked = config.get("blocked_phrases", [])
    if not isinstance(blocked, list) or any(
        not isinstance(phrase, str) for phrase in blocked
    ):
        detail = "blocked_phrases must be a list of strings"
        raise ValueError(detail)

    match_mode = config.get("match_mode", "strict")
    if match_mode not in MATCH_MODES:
        detail = 'match_mode must be "strict", "variants" or "bm25"'
        raise ValueError(detail)

    aliases = config.get("aliases", {})
    if not isinstance(aliases, dict) or any(
        not isinstance(target, str) for target in aliases.values()
    ):
        detail = (
            "aliases must be a JSON object mapping whole messages to source quotations"
        )
        raise ValueError(detail)

    return Settings(
        channel_ids,
        tuple(path.parent / edition for edition in editions),
        tuple(blocked),
        match_mode,
        aliases,
        validate_match_threshold(
            config.get("match_threshold", DEFAULT_MATCH_THRESHOLD)
        ),
    )


class QuoteIndex:
    def __init__(
        self,
        entries: dict[str, Quote],
        variants: dict[str, Quote | None] | None = None,
        aliases: dict[str, Quote] | None = None,
        *,
        normalized: Callable[[str], str] = normalize,
        blocked: frozenset[str] = frozenset(),
        retrieval: BM25 | None = None,
        threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> None:
        self._entries = entries
        self._variants = variants
        self._aliases = aliases or {}
        self._normalize = normalized
        self._blocked = blocked
        self._retrieval = retrieval
        self._threshold = threshold

    @classmethod
    def from_files(
        cls,
        paths: tuple[Path, ...] | list[Path],
        blocked_phrases: tuple[str, ...] | list[str] = (),
        *,
        match_mode: str = "strict",
        aliases: dict[str, str] | None = None,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> QuoteIndex:
        if match_mode not in MATCH_MODES:
            detail = 'match_mode must be "strict", "variants" or "bm25"'
            raise ValueError(detail)
        threshold = validate_match_threshold(match_threshold)
        use_bm25 = match_mode == "bm25"
        use_variants = match_mode != "strict"
        normalized = normalize_bm25 if use_bm25 else normalize
        phrases = (*COMMON_PHRASES, *blocked_phrases)
        if use_bm25:
            phrases += COMMON_ENGLISH_CHAT
        blocked = {normalized(phrase) for phrase in phrases}
        if use_variants:
            blocked = {variant_key(key) for key in blocked}
        entries = {}
        for path in paths:
            with path.open(encoding="utf-8-sig") as source:
                for line_no, line in enumerate(source, 1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                        book, chapter, verse, text = (
                            row[key] for key in ("book", "chapter", "verse", "text")
                        )
                        edition = row.get("edition", "")
                        if (
                            not isinstance(book, str)
                            or not (0 < len(book.strip()) <= 80)
                            or not book.isprintable()
                            or type(chapter) is not int
                            or not (1 <= chapter <= 9999)
                            or not isinstance(verse, str)
                            or len(verse) > 32
                            or not VERSE.fullmatch(verse)
                            or not isinstance(text, str)
                            or not (0 < len(text) <= MAX_QUOTE_LENGTH)
                            or not text.isprintable()
                            or not isinstance(edition, str)
                            or len(edition) > 60
                            or (edition and not edition.isprintable())
                        ):
                            raise ValueError  # noqa: TRY301 -- use the same line-numbered error for invalid records
                    except (KeyError, TypeError, ValueError):
                        detail = f"{path.name}:{line_no}: invalid quotation record"
                        raise ValueError(detail) from None

                    key = normalized(text)
                    blocked_key = variant_key(key) if use_variants else key
                    if blocked_key in blocked or not has_minimum_length(key):
                        continue
                    reference = f"{book.strip()} {chapter}:{verse.replace('-', '–')}"
                    entries.setdefault(
                        key, Quote(text.strip(), reference, edition.strip())
                    )
        if not use_variants:
            return cls(entries)

        variants: dict[str, Quote | None] = {}
        for key, quote in entries.items():
            folded = variant_key(key)
            first = variants.setdefault(folded, quote)
            if first is not None and first.reference != quote.reference:
                # A spelling variant must not select between different citations.
                variants[folded] = None

        resolved_aliases: dict[str, Quote] = {}
        for number, (phrase, source) in enumerate((aliases or {}).items(), 1):
            key = variant_key(normalized(phrase))
            source_key = normalized(source)
            if key in blocked or variant_key(source_key) in blocked:
                continue
            if len(phrase) > 2000 or not has_minimum_length(key):
                detail = f"aliases entry {number}: trigger length is invalid"
                raise ValueError(detail)
            quote = entries.get(source_key)
            if quote is None:
                detail = f"aliases entry {number}: target must be an indexed source quotation"
                raise ValueError(detail)
            if resolved_aliases.setdefault(key, quote) != quote:
                detail = f"aliases entry {number}: trigger duplicates an alias with a different target"
                raise ValueError(detail)
        retrieval = (
            BM25(tuple(key for key, quote in variants.items() if quote is not None))
            if use_bm25
            else None
        )
        return cls(
            entries,
            variants,
            resolved_aliases,
            normalized=normalized,
            blocked=frozenset(blocked),
            retrieval=retrieval,
            threshold=threshold,
        )

    @property
    def fuzzy_enabled(self) -> bool:
        return self._retrieval is not None

    def candidate(self, message: str) -> Match | None:
        key = self._normalize(message)
        exact = self._entries.get(key)
        if exact is not None:
            return Match(exact, 1.0)
        if self._variants is None:
            return None

        folded = variant_key(key)
        quote = self._aliases.get(folded) or self._variants.get(folded)
        if quote is not None:
            return Match(quote, 1.0)
        if (
            self._retrieval is None
            or folded in self._variants
            or folded in self._blocked
            or not has_minimum_length(folded)
            or len(folded) > MAX_QUOTE_LENGTH
        ):
            return None

        english_words = 0 if CJK.search(folded) else len(WORDS.findall(folded))
        if english_words == 1:
            return None

        best = None
        contains = (
            re.compile(rf"(?<!\w){re.escape(folded)}(?!\w)") if english_words else None
        )
        matcher = SequenceMatcher(None, autojunk=False)
        for candidate in self._retrieval.candidates(folded):
            quote = self._variants[candidate]
            if quote is None:
                continue
            if contains.search(candidate) if contains else folded in candidate:
                return Match(quote, 1.0)
            # The index contains full verses. Compare a chat message with the
            # verse and its clauses, then cite the full verse in either case.
            score = best.score if best else 0.0
            parts = {
                variant_key(normalize_bm25(part))
                for part in VERSE_BREAKS.split(quote.text)
            }
            windows = _word_windows(candidate, english_words) if english_words else ()
            for part in chain(sorted(parts), (candidate,), windows):
                if not part:
                    continue
                matcher.set_seqs(folded, part)
                if matcher.quick_ratio() > score:
                    score = max(score, matcher.ratio())
            if best is None or score > best.score:
                best = Match(quote, score)
        return best

    def lookup(self, message: str) -> Quote | None:
        match = self.candidate(message)
        return (
            match.quote
            if match is not None and match.score >= self._threshold
            else None
        )
