from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DEFAULTS = json.loads((ROOT/'assets/personal_spread_reference.json').read_text(encoding='utf-8'))['default_thresholds']


@dataclass
class Settings:
    root: Path = ROOT
    token: str = field(default='', repr=False)
    guild_id: int | None = None
    min_players: int = 100
    max_workers: int = 1
    queue_limit: int = 8
    cache_ttl: int = 86400
    worker_timeout: int = 180
    offline: bool = False
    cache_dir: Path | None = None
    spread_threshold: float = REFERENCE_DEFAULTS['spread_threshold']
    tail_threshold: float = REFERENCE_DEFAULTS['tail_threshold']
    eating_rating_margin: float = 0.0
    signal_min_groups: int = 3
    signal_min_span: float = .2

    @classmethod
    def load(cls):
        load_dotenv(ROOT/'.env', override=False, encoding='utf-8-sig')
        guild = os.getenv('DISCORD_GUILD_ID', '').strip()
        return cls(token=(os.getenv('DISCORD_TOKEN') or os.getenv('DISCORD_BOT_TOKEN') or os.getenv('BOT_TOKEN') or os.getenv('TOKEN') or '').strip(),
            guild_id=int(guild) if guild else None,
            min_players=int(os.getenv('MIN_PLAYERS', '100')),
            max_workers=int(os.getenv('MAX_CHART_WORKERS', '1')),
            queue_limit=int(os.getenv('CHART_QUEUE_LIMIT', '8')),
            cache_ttl=int(os.getenv('CACHE_TTL_SECONDS', '86400')),
            worker_timeout=int(os.getenv('CHART_TIMEOUT_SECONDS', '180')),
            spread_threshold=float(os.getenv('SPREAD_SCORE_THRESHOLD', str(REFERENCE_DEFAULTS['spread_threshold']))),
            tail_threshold=float(os.getenv('TAIL_SCORE_THRESHOLD', str(REFERENCE_DEFAULTS['tail_threshold']))),
            eating_rating_margin=float(os.getenv('EATING_RATING_MARGIN', '0')),
            signal_min_groups=int(os.getenv('SIGNAL_MIN_GROUPS', '3')),
            signal_min_span=float(os.getenv('SIGNAL_MIN_RATING_SPAN', '0.2')))

    def validate(self):
        if (self.min_players < 1 or self.max_workers < 1 or self.queue_limit < self.max_workers
                or self.cache_ttl < 0 or self.worker_timeout < 10):
            raise ValueError('invalid worker, cache or population settings')
        if (any(not math.isfinite(v) or v <= 0 for v in
                (self.spread_threshold, self.tail_threshold, self.signal_min_span))
                or not math.isfinite(self.eating_rating_margin) or self.eating_rating_margin < 0
                or type(self.signal_min_groups) is not int or self.signal_min_groups < 2):
            raise ValueError('invalid numeric signal settings')
        required = [self.root/'assets/wiki_rating_source.html', self.root/'assets/NotoSansCJKjp-Regular.otf',
                    self.root/'engine/plot_single_numeric_preview.py', self.root/'engine/collect_chunirec_rating_table.py',
                    self.root/'engine/numeric_signals.py', self.root/'assets/personal_spread_reference.json']
        if any(not p.is_file() for p in required):
            raise ValueError('missing bundled engine/formula/font assets')

    @property
    def cache(self):
        return self.cache_dir or self.root/'cache'

    def engine_fingerprint(self):
        policy = {'min_players': self.min_players, 'spread_threshold': self.spread_threshold,
            'tail_threshold': self.tail_threshold, 'eating_rating_margin': self.eating_rating_margin,
            'signal_min_groups': self.signal_min_groups, 'signal_min_span': self.signal_min_span}
        digest = hashlib.sha256(json.dumps(policy, sort_keys=True).encode())
        for path in sorted((self.root/'engine').glob('*.py'))+sorted((self.root/'assets').glob('*')):
            if path.is_file():
                digest.update(path.name.encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()


def ensure_catalog(root=ROOT):
    from .catalog import build_catalog, Catalog
    destination = root/'data/catalog.json'
    source_name = os.getenv('CATALOG_SOURCE', '').strip()
    candidates = [root/source_name] if source_name else list(root.glob('chunithm-player-data_*.json'))
    if len(candidates) > 1:
        raise ValueError('multiple player exports; set CATALOG_SOURCE to the chosen filename')
    if candidates:
        source = candidates[0]
        if not source.is_file():
            raise ValueError('catalogue source file missing')
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        stale = not destination.is_file()
        if not stale:
            import json
            stale = json.loads(destination.read_text(encoding='utf-8')).get('source_sha256') != source_hash
        if stale:
            build_catalog(source, destination)
    if not destination.is_file():
        raise ValueError('provide a player export or an extracted data/catalog.json')
    return Catalog.load(destination, root/'data/aliases.json')
