"""Isolated one-chart worker. Nothing iterates over the catalogue."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

from .catalog import normalize
from .config import ROOT, REFERENCE_DEFAULTS
from .provider import find_song, SourceError

SOURCE_FILES = ('manifest.json', 'selected_stats.json', 'embedded_stats.txt',
                'rating_score_table.csv', 'rating_rank_counts.csv', 'source.html', 'music.js')


def produce(args):
    sys.path.insert(0, str(ROOT/'engine'))
    import analyze_chunirec_numeric as core
    import collect_chunirec_rating_table as collector
    import plot_single_numeric_preview as preview
    folder = args.job_dir.resolve()
    snapshot = folder/'source_snapshot'
    source_fetches = 0
    if args.seed_snapshot:
        original, _, _, _ = core.load_input(args.seed_snapshot.resolve())
        if normalize(original['title']) != normalize(args.title) or original['difficulty'] != args.difficulty:
            raise SourceError('快照的曲名／難度與所選譜面不一致。')
        snapshot.mkdir()
        for name in SOURCE_FILES:
            shutil.copyfile(args.seed_snapshot/name, snapshot/name)
    else:
        if args.offline:
            raise SourceError('離線模式沒有這張譜面的快取。')
        title, url = find_song(args.title, folder)
        collector.run(SimpleNamespace(title=title, difficulty=args.difficulty, url=url, output=snapshot))
        source_fetches = 1+len(json.loads((snapshot/'manifest.json').read_text(encoding='utf-8'))['fetches'])
    manifest, _, groups, hashes = core.load_input(snapshot)
    if normalize(manifest['title']) != normalize(args.title) or manifest['difficulty'] != args.difficulty:
        raise SourceError('來源曲名／難度與所選譜面不一致。')
    if not any(g['players'] >= args.min_players and g['average_score'] is not None for g in groups):
        raise SourceError(f'這張譜面沒有符合每組 {args.min_players} 人門檻的統計。')
    if float(manifest['chart']['const']) <= 5:
        raise SourceError('這張譜面的定數不在目前公式模型支援範圍。')
    os.environ['RATING_BOT_FONT_FILE'] = str(ROOT/'assets/NotoSansCJKjp-Regular.otf')
    plot_dir = folder/'plot'
    try:
        result = preview.run(SimpleNamespace(input=snapshot, wiki_file=ROOT/'assets/wiki_rating_source.html',
            min_players=args.min_players, view_min=None, view_max=None, axis_mode='auto' if args.view_mode == 'full' else 'focus',
            smooth_fraction=.65, robust_iterations=2, output=plot_dir, spread_threshold=args.spread_threshold,
            tail_threshold=args.tail_threshold, eating_rating_margin=args.eating_rating_margin,
            signal_min_groups=args.signal_min_groups, signal_min_span=args.signal_min_span))
    except preview.NoEligibleGroupsError as error:
        core.save_json(folder/'group_quality.json', error.group_quality)
        raise SourceError(str(error)) from error
    plot = json.loads((plot_dir/'preview_manifest.json').read_text(encoding='utf-8'))
    judgments = json.loads((plot_dir/'numeric_signals.json').read_text(encoding='utf-8'))
    summary = {key: judgments[key] for key in ('schema_version', 'decision_provider', 'llm_analysis_used',
        'rating_scope', 'policy', 'personal_reference', 'personal_spread', 'relative_specialization', 'eating_central', 'eating_familiar', 'compact_lines')}
    if not all(core.sha256(plot_dir/name) == digest for name,digest in plot['output_sha256'].items()):
        raise ValueError('plot checksum mismatch')
    return {'status': 'complete', 'title': manifest['title'], 'difficulty': manifest['difficulty'],
        'source_url': manifest['source_url'], 'constant': manifest['chart']['const'],
        'constant_confirmed': manifest['chart']['const_confirmed'],
        'snapshot': str(snapshot), 'image': result['image'], 'image_sha256': core.sha256(Path(result['image'])),
        'plot_manifest': str(plot_dir/'preview_manifest.json'), 'focus_range': plot['view_range'],
        'view_mode': args.view_mode, 'x_limits': plot['focus']['x_limits'], 'y_limits': plot['focus']['y_limits'],
        'snapshot_sha256': hashes,
        'numeric_summary': summary, 'signals_file': str(plot_dir/'numeric_signals.json'),
        'group_quality_file': str(plot_dir/'group_quality.json'),
        'excluded_group_count': plot['excluded_group_count'],
        'images_generated': 1, 'source_fetches': source_fetches, 'llm_analysis_used': False,
        'exact_band_means_available': False, 'axis_label': 'BEST 枠平均',
        'source_region': None, 'source_game_version': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-dir', type=Path, required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--difficulty', required=True)
    parser.add_argument('--min-players', type=int, default=100)
    parser.add_argument('--seed-snapshot', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--view-mode', choices=('focus', 'full'), default='focus')
    parser.add_argument('--spread-threshold', type=float, default=REFERENCE_DEFAULTS['spread_threshold'])
    parser.add_argument('--tail-threshold', type=float, default=REFERENCE_DEFAULTS['tail_threshold'])
    parser.add_argument('--eating-rating-margin', type=float, default=0)
    parser.add_argument('--signal-min-groups', type=int, default=3)
    parser.add_argument('--signal-min-span', type=float, default=.2)
    args = parser.parse_args()
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            result = produce(args)
    except (Exception, SystemExit) as error:
        (args.job_dir/'worker_error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        result = {'status': 'failed', 'error': str(error) if isinstance(error, SourceError)
                  else '來源抓取或圖表處理失敗；詳細原因已保存。', 'images_generated': 0}
    (args.job_dir/'worker_log.txt').write_text(buffer.getvalue(), encoding='utf-8')
    (args.job_dir/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
