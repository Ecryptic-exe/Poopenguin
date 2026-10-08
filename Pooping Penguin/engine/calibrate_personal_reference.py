"""Offline two-anchor calibration; no HTTP, plots or LLM."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

import analyze_chunirec_numeric as core
import compare_chunirec_rating_reference as reference
import plot_single_numeric_preview as preview

REFERENCE_TITLE = 'XL TECHNO -More Dance Remix-'
MEDIUM_TITLE = '月の光'
ROOT = Path(__file__).resolve().parent.parent
EPS = 1e-6


def collect_anchor(manifest, groups, source_hashes, expected_title, minimum):
    if manifest['title'] != expected_title or manifest['difficulty'] != 'MASTER':
        raise ValueError('calibration requires the explicitly selected reference MASTER')
    formula = reference.extract_formula((ROOT/'assets/wiki_rating_source.html').read_bytes())
    score_intervals = preview.score_intervals(formula)
    raw, population, _, _, _ = preview.build_data(groups, score_intervals, minimum, None, None, .65, 2)
    constant = manifest['chart']['const']
    start = float(reference.dec(constant)+reference.dec('1.00'))
    stop = float(reference.dec(constant)+reference.dec('2.15'))
    index = {(r['cls'], r['line_id']): r for r in population}
    rows = []
    for group in raw:
        if not group['eligible'] or not start-EPS <= group['rating'] <= stop+EPS:
            continue
        p10, p70 = index[group['cls'], 'P10'], index[group['cls'], 'P70']
        rows.append({'cls': group['cls'], 'rating': group['rating'], 'players': group['players'],
            'published_group_mean_score': group['average_score'],
            'spread_score': p10['estimated_band_mean_score']-p70['estimated_band_mean_score'],
            'spread_lower_score': max(0, p10['band_mean_lower_bound_score']-p70['band_mean_upper_bound_score']),
            'tail_score': p10['estimated_band_mean_score']-group['average_score'],
            'tail_lower_score': max(0, p10['band_mean_lower_bound_score']-group['average_score'])})
    if len(rows) < 3:
        raise ValueError('insufficient eligible reference buckets')
    return {'reference': {'title': manifest['title'], 'difficulty': manifest['difficulty'], 'constant': constant,
        'constant_confirmed': manifest['chart']['const_confirmed'], 'source_url': manifest['source_url'],
        'statistics_generated_at': manifest.get('statistics_generated_at')},
        'rating_scope': [start, stop], 'calibration_rows': rows,
        'medians': {name: median(r[name] for r in rows) for name in
            ('spread_score', 'spread_lower_score', 'tail_score', 'tail_lower_score')},
        'source_groups': groups, 'source_sha256': source_hashes}


def calibrate(manifest, groups, source_hashes, medium_manifest, medium_groups,
              medium_source_hashes, minimum=100, score_step=500):
    if type(minimum) is not int or minimum < 1 or type(score_step) is not int or score_step < 1:
        raise ValueError('invalid calibration population or rounding step')
    large = collect_anchor(manifest, groups, source_hashes, REFERENCE_TITLE, minimum)
    medium = collect_anchor(medium_manifest, medium_groups, medium_source_hashes, MEDIUM_TITLE, minimum)
    def down(value):
        return float(max(score_step, math.floor(value/score_step)*score_step))
    def up(value):
        return float(math.ceil(value/score_step)*score_step)
    tiers = {}
    for name, metric in (('personal_spread', 'spread'), ('relative_specialization', 'tail')):
        tiers[name] = {
            'large': {'estimate_threshold': up(large['medians'][metric+'_score']),
                      'evidence_threshold': down(medium['medians'][metric+'_score'])},
            'medium': {'estimate_threshold': down(medium['medians'][metric+'_score']),
                       'evidence_threshold': down(medium['medians'][metric+'_lower_score'])}}
        if not (0 < tiers[name]['medium']['evidence_threshold'] <= tiers[name]['medium']['estimate_threshold']
                < tiers[name]['large']['estimate_threshold']):
            raise ValueError('reference thresholds are not ordered')
        for magnitude, anchor in (('large', large), ('medium', medium)):
            threshold = tiers[name][magnitude]
            run = []
            found = False
            for row in anchor['calibration_rows']:
                qualifies = row[metric+'_score'] >= threshold['estimate_threshold']-EPS and row[metric+'_lower_score'] >= threshold['evidence_threshold']-EPS
                if not qualifies or (run and row['cls'] != run[-1]['cls']+1):
                    run = []
                if qualifies:
                    run.append(row)
                if len(run) >= 3 and run[-1]['rating']-run[0]['rating'] >= .2-EPS:
                    found = True
            if not found:
                raise ValueError('reference has no continuous detectable interval')
    return {'schema_version': 2, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'decision_provider': 'python', 'llm_analysis_used': False,
        'reference': large['reference'], 'references': {'large': large, 'medium': medium},
        'method': 'Independent width and upper-vs-mean proxies. Magnitude: XL median rounded up, Moon median rounded down. Detectability: large uses Moon estimated median, medium uses Moon identification lower-bound median; each rounded down to the score step.',
        'evidence_semantics': 'Clear means estimated magnitude AND a detectable gap supported by identification bounds; it does not assert the large/medium magnitude threshold is proven by those bounds.',
        'min_players': minimum, 'score_rounding_step': score_step,
        'default_thresholds': {'spread_threshold': tiers['personal_spread']['large']['estimate_threshold'],
                               'tail_threshold': tiers['relative_specialization']['large']['estimate_threshold']},
        'tier_thresholds': tiers,
        'formula_sha256': core.sha256(ROOT/'assets/wiki_rating_source.html'),
        'population_module_sha256': core.sha256(ROOT/'engine/population_band_numeric.py'),
        'sample_identity': 'public aggregate chart statistics; catalogue player scores are not population samples',
        'cause_identification': 'unknown; these proxies cannot identify keyboard, stamina or chart patterns'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='Checksum-verified XL MASTER snapshot')
    parser.add_argument('--medium-input', required=True, type=Path, help='Checksum-verified Moon MASTER snapshot')
    parser.add_argument('--output', required=True, type=Path, help='A new calibration JSON file')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('calibration output already exists; preserve the previous reference')
    manifest, _, groups, hashes = core.load_input(args.input.resolve())
    medium_manifest, _, medium_groups, medium_hashes = core.load_input(args.medium_input.resolve())
    result = calibrate(manifest, groups, hashes, medium_manifest, medium_groups, medium_hashes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output.resolve()), 'tier_thresholds': result['tier_thresholds'],
                      'source_fetches': 0, 'images_generated': 0}))


if __name__ == '__main__':
    main()
