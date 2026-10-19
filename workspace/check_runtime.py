"""Sanitized local health checks; never output bot logs or player records."""
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parents[1]
record = json.loads((root / 'data/runtime/bot-process.json').read_text(encoding='utf-8-sig'))
log = Path(record['stdout']).read_text(encoding='utf-8', errors='replace')
err = Path(record['stderr']).read_text(encoding='utf-8', errors='replace')
startup = None
for line in log.splitlines():
    try:
        row = json.loads(line)
        if row.get('event') == 'Extension startup finished':
            startup = row.get('startup')
    except (ValueError, TypeError):
        pass
c = sqlite3.connect((root / 'data/penguin.sqlite3').as_uri() + '?mode=ro', uri=True)
integrity = c.execute('pragma integrity_check').fetchone()[0]
fk = c.execute('pragma foreign_key_check').fetchall()
counts = {t: c.execute(f'select count(*) from {t}').fetchone()[0] for t in ['chunirec_songs', 'chunirec_charts']}
c.close()
report = {
    'checked_at': datetime.now().astimezone().isoformat(),
    'process_id': record['process_id'],
    'gateway_connected': bool(re.search(r'connected to Gateway', log, re.I)),
    'guild_prefixes_loaded': 'Loaded guild prefixes' in log,
    'startup_complete': bool(startup and startup.get('complete')),
    'loaded_extensions': sum(x.get('state') == 'loaded' for x in (startup or {}).get('extensions', {}).values()),
    'stderr_empty': not err.strip(),
    'runtime_errors': len(re.findall(r'"level":\s*"error"|Traceback \(most recent call last\)', log + err)),
    'database_integrity': integrity, 'foreign_key_errors': len(fk),
    'public_catalog_counts': counts, 'new_required_env_settings': [],
    'schema_migration_performed': False,
    'registration_report': 'reports/command-registration-20261015.json',
}
(root / 'reports/embed-redesign-live-20261015.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
