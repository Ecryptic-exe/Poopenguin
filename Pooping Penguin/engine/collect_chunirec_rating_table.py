"""Fetch one ChuniDB song and export its published BEST-average/score data.

Only requests + BeautifulSoup + JSON parsing. Does not execute remote JavaScript,
calculate chart ratings, call models, or change the tag pipeline.
"""
import argparse
import csv
import hashlib
import json
import re
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DIFFICULTIES = {
    'BASIC': ('BAS', 'B'), 'ADVANCED': ('ADV', 'A'),
    'EXPERT': ('EXP', 'E'), 'MASTER': ('MAS', 'M'), 'ULTIMA': ('ULT', 'U'),
}


def stamp():
    return datetime.now(ZoneInfo('Asia/Taipei')).isoformat()


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def fetch(session, url, path):
    started = time.monotonic()
    with session.get(url, timeout=(8, 15), stream=True) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(65536):
            if time.monotonic() - started > 40:
                raise TimeoutError('response exceeded total 40-second deadline')
            data.extend(chunk)
            if len(data) > 4 * 1024 * 1024:
                raise ValueError('response exceeded 4 MiB limit')
        content = bytes(data)
        path.write_bytes(content)
        return content, {'url': url, 'final_url': response.url,
                         'http_status': response.status_code, 'retrieved_at': stamp(),
                         'content_type': response.headers.get('Content-Type'),
                         'raw_file': str(path.resolve()), 'sha256': checksum(content)}


def parse_stats(text):
    """Read B{JSON}'A{JSON}'... from #urec_pdx without eval or JS execution."""
    decoder = json.JSONDecoder()
    result, ranges = {}, {}
    pos = 0
    while pos < len(text):
        while pos < len(text) and text[pos] in "'\r\n\t ":
            pos += 1
        if pos == len(text):
            break
        code = text[pos]
        if code not in 'BAEMU' or code in result:
            raise ValueError(f'unexpected or duplicate difficulty prefix at offset {pos}')
        start = pos
        payload, pos = decoder.raw_decode(text, pos + 1)
        if not isinstance(payload, dict) or 'avg_score' not in payload:
            raise ValueError('published statistics are missing avg_score')
        result[code] = payload
        ranges[code] = {'start': start, 'end': pos}
    return result, ranges


def chart_info(soup, code):
    for marker in soup.find_all('span', class_=code):
        cell = marker.parent
        row = cell.parent
        cells = row.find_all('div', recursive=False)
        if len(cells) == 4 and cells[0].get_text(strip=True) == code:
            values = [c.get_text(' ', strip=True) for c in cells]
            return {'difficulty_code': code, 'level': values[1], 'const': values[2],
                    'const_confirmed': cells[2].select_one('.unknown-const') is None,
                    'notes': values[3], 'evidence_locator': f'chart info row: {code}'}
    raise ValueError(f'explicit chart info row not found: {code}')


def run(args):
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', args.title).strip(' .') or 'song'
    folder = args.output or ROOT / 'table_probes' / f'chunirec_{safe_title}_{args.difficulty}' / datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y%m%d_%H%M%S')
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {'task': 'single_song_published_rating_score_table', 'source_url': args.url,
                'requested_title': args.title, 'difficulty': args.difficulty,
                'started_at': stamp(), 'fetches': [], 'errors': [], 'llm_used': False,
                'tag_pipeline_changed': False, 'statistics_generated_at': None}
    save_json(folder / 'manifest.json', manifest)
    try:
        with requests.Session() as session:
            session.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; ChuniTableCollector/1.0)',
                                    'Accept-Language': 'ja-JP,ja;q=0.9,en;q=0.5'})
            session.cookies.set('lang', 'ja_JP', domain='db.chunirec.net')
            content, provenance = fetch(session, args.url, folder / 'source.html')
            manifest['fetches'].append(provenance)
            soup = BeautifulSoup(content.decode('utf-8'), 'html.parser')
            titles = [h.get_text(strip=True) for h in soup.select('h3[lang="ja"]')]
            if args.title not in titles:
                raise ValueError('requested song title not found in the explicit song heading')
            code, prefix = DIFFICULTIES[args.difficulty]
            chart = chart_info(soup, code)
            container = soup.select_one('script#urec_pdx[type="text/plain"]')
            if container is None:
                raise ValueError('published #urec_pdx statistics block not found')
            raw_text = container.get_text()
            (folder / 'embedded_stats.txt').write_text(raw_text, encoding='utf-8')
            all_stats, ranges = parse_stats(raw_text)
            if prefix not in all_stats:
                raise ValueError('requested difficulty has no published statistics')
            stats = all_stats[prefix]
            save_json(folder / 'selected_stats.json', stats)
            label = soup.select_one('#chart_area_avg_score')
            heading = label.parent.find_previous_sibling('p') if label else None
            published_label = heading.get_text(strip=True) if heading else None
            manifest.update(title=args.title, chart=chart, statistic_label=published_label,
                            statistics_locator=f'#urec_pdx prefix {prefix}',
                            statistics_text_range=ranges[prefix], total_players=stats.get('players'))
            # Save the renderer as evidence of labels/bucket definitions; never run it.
            script = next((s for s in soup.find_all('script', src=True)
                           if urlsplit(s['src']).path.endswith('/music.js')), None)
            if script:
                js_url = urljoin(provenance['final_url'], script['src'])
                if urlsplit(js_url).netloc != urlsplit(provenance['final_url']).netloc:
                    raise ValueError('renderer URL unexpectedly changed host')
                try:
                    _, js_provenance = fetch(session, js_url, folder / 'music.js')
                    manifest['fetches'].append(js_provenance)
                except (requests.RequestException, ValueError, TimeoutError) as exc:
                    manifest['errors'].append({'url': js_url, 'stage': 'renderer', 'reason': str(exc)})
        rows, labels = [], set()
        groups = {g['cls']: g for g in stats.get('byr', [])}
        for pair in stats['avg_score']:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError('unexpected avg_score record shape')
            rating, score = pair
            if not isinstance(rating, str) or not re.fullmatch(r'\d+\.\d+', rating) or rating in labels:
                raise ValueError('invalid or duplicate published BEST-average label')
            if score is not None and (type(score) is not int or not 0 <= score <= 1010000):
                raise ValueError('invalid published average score')
            labels.add(rating)
            cls = Decimal(rating) * 10
            group = groups.get(int(cls)) if cls == cls.to_integral_value() else None
            rows.append({'title': args.title, 'difficulty': args.difficulty,
                         'level': chart['level'], 'const': chart['const'], 'const_confirmed': chart['const_confirmed'], 'notes': chart['notes'],
                         'best_average_rating': rating, 'average_score': score,
                         'group_players': group['players'] if group else None,
                         'source_url': provenance['final_url'], 'retrieved_at': provenance['retrieved_at'],
                         'source_locator': f'#urec_pdx {prefix}.avg_score[{len(rows)}]'})
        if not rows:
            raise ValueError('requested difficulty has no avg_score points')
        csv_path = folder / 'rating_score_table.csv'
        with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        with csv_path.open(encoding='utf-8-sig', newline='') as handle:
            reread = list(csv.DictReader(handle))
        if len(reread) != len(rows) or [r['best_average_rating'] for r in reread] != [r[0] for r in stats['avg_score']]:
            raise ValueError('CSV round-trip mismatch')
        for row, pair in zip(reread, stats['avg_score']):
            if row['average_score'] != (str(pair[1]) if pair[1] is not None else ''):
                raise ValueError('CSV score differs from the published value')
        # Published rank counts remain counts; no percentages or scores inferred.
        label_by_class = {int(Decimal(p[0]) * 10): p[0] for p in stats['avg_score']}
        rank_rows = []
        for index, group in enumerate(stats.get('byr', [])):
            row = {'title': args.title, 'difficulty': args.difficulty,
                   'rating_class_raw': group['cls'],
                   'best_average_rating': label_by_class.get(group['cls'], ''),
                   'players': group['players'], **group['rank'],
                   'source_url': provenance['final_url'], 'retrieved_at': provenance['retrieved_at'],
                   'source_locator': f'#urec_pdx {prefix}.byr[{index}]'}
            rank_rows.append(row)
        if rank_rows:
            rank_path = folder / 'rating_rank_counts.csv'
            with rank_path.open('w', encoding='utf-8-sig', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rank_rows[0]))
                writer.writeheader(); writer.writerows(rank_rows)
            manifest.update(rank_csv_file=str(rank_path.resolve()), rank_csv_rows=len(rank_rows),
                            rank_csv_sha256=checksum(rank_path.read_bytes()))
        manifest.update(status='complete', completed_at=stamp(), rows=len(rows),
                        csv_file=str(csv_path.resolve()), csv_sha256=checksum(csv_path.read_bytes()),
                        csv_roundtrip_valid=True, missing_scores=sum(p[1] is None for p in stats['avg_score']),
                        interpretation='Published BEST-slot-average groups versus mean score on this chart; not a score-to-chart-rating formula.')
        save_json(folder / 'manifest.json', manifest)
        print(json.dumps({'status': 'complete', 'folder': str(folder.resolve()), 'rows': len(rows),
                          'difficulty': args.difficulty, 'total_players': stats.get('players'),
                          'statistic_label': published_label, 'const_confirmed': chart['const_confirmed']}, ensure_ascii=False))
    except Exception as exc:
        manifest.update(status='failed', completed_at=stamp())
        manifest['errors'].append({'url': args.url, 'stage': 'collection', 'reason': f'{type(exc).__name__}: {exc}'})
        save_json(folder / 'manifest.json', manifest)
        print(json.dumps({'status': 'failed', 'folder': str(folder.resolve()), 'reason': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='https://db.chunirec.net/music/Air/e65756cc3f647986')
    parser.add_argument('--title', default='Air')
    parser.add_argument('--difficulty', choices=DIFFICULTIES, default='ULTIMA')
    parser.add_argument('--output', type=Path)
    run(parser.parse_args())
