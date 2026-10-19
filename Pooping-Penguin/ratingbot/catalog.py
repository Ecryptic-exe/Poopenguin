"""Metadata-only catalogue and BM25 search. No network or chart generation."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

DIFFICULTIES = ('BASIC', 'ADVANCED', 'EXPERT', 'MASTER', 'ULTIMA')
DIFF_CODES = dict(zip(('BAS', 'ADV', 'EXP', 'MAS', 'ULT'), DIFFICULTIES))


def normalize(text):
    text = unicodedata.normalize('NFKD', unicodedata.normalize('NFKC', str(text)))
    text = ''.join(c for c in text if not unicodedata.combining(c)).casefold()
    return ' '.join(text.replace('’', "'").replace('‘', "'").replace('−', '-').split())


def tokenize(text):
    # Word tokens plus character n-grams: Japanese fragments do not depend on spaces.
    runs = re.findall(r'[a-z0-9]+|[^\W_a-z0-9]+', normalize(text), re.UNICODE)
    tokens = []
    for run in runs:
        tokens.append('w:'+run)
        tokens.extend('c:'+c for c in run)
        for n in (2, 3):
            tokens.extend(f'g{n}:'+run[i:i+n] for i in range(len(run)-n+1))
    return tokens


def difficulty_name(text):
    name = str(text).upper()
    name = DIFF_CODES.get(name, name)
    if name not in DIFFICULTIES:
        raise ValueError('unsupported difficulty')
    return name


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def build_catalog(source, destination):
    original = source.read_bytes()
    player = json.loads(original.decode('utf-8-sig'))
    if not isinstance(player, dict) or not isinstance(player.get('score'), list):
        raise ValueError('expected player export with score list')
    songs, chart_keys, rows = {}, set(), 0
    for section in ('score', 'best', 'new'):
        values = player.get(section, [])
        if not isinstance(values, list):
            raise ValueError('player record section is not a list')
        for row in values:
            rows += 1
            if not isinstance(row, dict):
                raise ValueError('malformed record')
            song_id, title = str(row['idx']), row['title']
            diff = difficulty_name(row['difficulty'])
            if not song_id.isascii() or not song_id.isdecimal() or not isinstance(title, str) or not title.strip():
                raise ValueError('invalid song identity')
            title = title.strip()
            song = songs.setdefault(song_id, {'song_id': song_id, 'id_namespace': 'chunilib_player_export',
                'title': title, 'aliases': [], 'difficulties': [], 'region': None, 'game_version': None})
            if song['title'] != title:
                raise ValueError(f'conflicting titles for source ID {song_id}')
            if diff not in song['difficulties']:
                song['difficulties'].append(diff)
            chart_keys.add((song_id, diff))
    if not songs:
        raise ValueError('empty song catalogue')
    for song in songs.values():
        song['difficulties'].sort(key=DIFFICULTIES.index)
    result = {'schema_version': 1, 'purpose': 'song search metadata only',
        'source_file': source.name, 'source_sha256': hashlib.sha256(original).hexdigest(),
        'source_sections': ['score', 'best', 'new'], 'source_rows': rows,
        'songs_count': len(songs), 'charts_count': len(chart_keys),
        'duplicate_chart_rows_removed': rows-len(chart_keys), 'network_requests': 0, 'charts_generated': 0,
        'personal_scores_included': False, 'player_profile_included': False,
        'songs': sorted(songs.values(), key=lambda s: int(s['song_id']))}
    save_json(destination, result)
    return result


@dataclass(frozen=True)
class Song:
    song_id: str
    title: str
    difficulties: tuple[str, ...]
    aliases: tuple[str, ...] = ()


class Catalog:
    def __init__(self, metadata, aliases=None):
        if metadata.get('schema_version') != 1:
            raise ValueError('unsupported catalogue schema')
        aliases = aliases or {}
        self.metadata = metadata
        self.songs = []
        self.by_id = {}
        self.exact = defaultdict(list)
        self.postings = defaultdict(dict)
        self.lengths = []
        for row in metadata['songs']:
            extra = aliases.get(str(row['song_id']), [])
            if not isinstance(extra, list) or any(not isinstance(x, str) for x in extra):
                raise ValueError('aliases must be lists of strings keyed by source song ID')
            names = tuple(dict.fromkeys(row.get('aliases', [])+extra))
            song = Song(str(row['song_id']), row['title'], tuple(row['difficulties']), names)
            if song.song_id in self.by_id or any(d not in DIFFICULTIES for d in song.difficulties):
                raise ValueError('invalid duplicate song or difficulty')
            index = len(self.songs)
            self.songs.append(song)
            self.by_id[song.song_id] = song
            for name in (song.title,)+song.aliases:
                key = normalize(name)
                if song not in self.exact[key]:
                    self.exact[key].append(song)
            terms = Counter(t for name in (song.title,)+song.aliases for t in tokenize(name))
            self.lengths.append(sum(terms.values()))
            for term, frequency in terms.items():
                self.postings[term][index] = frequency
        self.average_length = sum(self.lengths)/max(1, len(self.lengths))

    @classmethod
    def load(cls, path, aliases_path=None):
        metadata = json.loads(path.read_text(encoding='utf-8'))
        aliases = json.loads(aliases_path.read_text(encoding='utf-8')) if aliases_path and aliases_path.is_file() else {}
        return cls(metadata, aliases)

    def exact_matches(self, query, difficulty=None):
        if query.startswith('id:'):
            song = self.by_id.get(query[3:])
            matches = [song] if song else []
        else:
            matches = self.exact.get(normalize(query), [])
        return [s for s in matches if difficulty is None or difficulty in s.difficulties]

    def search(self, query, difficulty=None, limit=25):
        if difficulty is not None:
            difficulty = difficulty_name(difficulty)
        if not query.strip():
            return [{'song': s, 'score': 0.0, 'exact': False} for s in self.songs
                    if difficulty is None or difficulty in s.difficulties][:limit]
        exact = self.exact_matches(query, difficulty)
        scores = defaultdict(float)
        n, k1, b = len(self.songs), 1.5, .75
        for term in set(tokenize(query)):
            posting = self.postings.get(term, {})
            idf = math.log1p((n-len(posting)+.5)/(len(posting)+.5))
            # Very common single characters assist short queries without dominating phrases.
            weight = .15 if term.startswith('c:') else 1.0
            for index, frequency in posting.items():
                norm = k1*(1-b+b*self.lengths[index]/self.average_length)
                scores[index] += weight*idf*frequency*(k1+1)/(frequency+norm)
        exact_ids = {s.song_id for s in exact}
        candidates = [i for i in scores if difficulty is None or difficulty in self.songs[i].difficulties]
        candidates.extend(i for i,s in enumerate(self.songs) if s.song_id in exact_ids and i not in scores)
        candidates.sort(key=lambda i: (self.songs[i].song_id not in exact_ids, -scores[i],
                                       normalize(self.songs[i].title), int(self.songs[i].song_id)))
        return [{'song': self.songs[i], 'score': scores[i], 'exact': self.songs[i].song_id in exact_ids}
                for i in candidates[:limit]]
