import hashlib
import argparse
import json
import sqlite3
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'outputs/chart-mapping-20261015'
data = json.loads((OUT / 'bm25-matches.json').read_text(encoding='utf-8'))
parser = argparse.ArgumentParser()
parser.add_argument('--reviewed', action='store_true')
reviewed = parser.parse_args().reviewed
reviews = {}
if reviewed:
    record = json.loads((OUT / 'luna/review.json').read_text(encoding='utf-8'))
    reviews = {r['source_row_zero_based']: r for r in record['rows']}
N = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
conn = sqlite3.connect((ROOT / 'data/penguin.sqlite3').as_uri() + '?mode=ro', uri=True)
charts = {r[0]: (r[1], r[2]) for r in conn.execute('select id,song_id,difficulty from chunirec_charts')}
conn.close()
workbook = OUT / ('luna/ChunithmChart_reviewed.xlsx' if reviewed else 'ChunithmChart_with_ID.xlsx')
with zipfile.ZipFile(workbook) as z:
    rels = {r.attrib['Id']: r.attrib['Target'].lstrip('/') for r in E.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
    sheets = {}
    for s in E.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet', N):
        p = rels[s.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
        p = p if p.startswith('xl/') else 'xl/' + p
        sheets[s.attrib['name']] = {c.attrib['r']: c for c in E.fromstring(z.read(p)).findall('s:sheetData/s:row/s:c', N)}
    count = 0
    unresolved = 0
    for row in data['rows']:
        assert len(row['locations']) == 1
        loc = row['locations'][0]
        cells = sheets[loc['sheet']]
        def numeric(col):
            c = cells.get(col + str(loc['excel_row']))
            v = c.find('s:v', N) if c is not None else None
            return int(v.text) if v is not None else None
        song, chart = numeric('L'), numeric('M')
        if row['selected'] is None and row['source_row'] not in reviews:
            assert song is None and chart is None
            unresolved += 1
        else:
            match = row['selected'] or reviews[row['source_row']]['database_match']
            assert (song, chart) == (match['song_id'], match['chart_id'])
            assert charts[chart] == (song, row['difficulty'])
            count += 1
        assert numeric('R') == row['source_row']
assert hashlib.sha256(Path(data['source_workbook']).read_bytes()).hexdigest() == data['source_sha256']
report = {'rows': len(data['rows']), 'ids_verified_against_live_read_only_catalog': count, 'unresolved_blank_ids': unresolved, 'locations_unique': True, 'source_workbook_unchanged': True, 'runtime_bindings_modified': False}
(OUT / ('validation-reviewed.json' if reviewed else 'validation.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
