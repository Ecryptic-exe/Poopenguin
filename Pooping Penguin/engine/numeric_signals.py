"""Deterministic signals from raw grouped estimates and identification bounds.

No network, LLM, LOWESS input, fabricated players, or percentile point values.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from dataclasses import asdict, dataclass
from statistics import median

import compare_chunirec_rating_reference as reference

EPS = 1e-6
RATING_EPS = 1e-9
REFERENCE_PATH = Path(__file__).resolve().parent.parent/'assets/personal_spread_reference.json'
PERSONAL_REFERENCE = json.loads(REFERENCE_PATH.read_text(encoding='utf-8'))
REFERENCE_DEFAULTS = PERSONAL_REFERENCE['default_thresholds']
POSITIVE = {'clear', 'tendency'}
BAND_RANGES = {'P5': (0, 10), 'P10': (0, 20), 'P30': (20, 40),
               'P50': (40, 60), 'P70': (60, 80)}


@dataclass(frozen=True)
class Policy:
    spread_threshold: float = REFERENCE_DEFAULTS['spread_threshold']
    tail_threshold: float = REFERENCE_DEFAULTS['tail_threshold']
    eating_rating_margin: float = 0.0
    min_groups: int = 3
    min_span: float = .2

    def validate(self):
        if (any(not math.isfinite(v) or v <= 0 for v in
                (self.spread_threshold, self.tail_threshold, self.min_span))
                or not math.isfinite(self.eating_rating_margin) or self.eating_rating_margin < 0
                or type(self.min_groups) is not int or self.min_groups < 2):
            raise ValueError('invalid numeric signal policy')
        return self


def finite(value):
    return value is not None and math.isfinite(float(value))


def band_values(row):
    if row is None:
        return None
    values = [row.get(k) for k in ('estimated_band_mean_score',
        'band_mean_lower_bound_score', 'band_mean_upper_bound_score')]
    if not all(finite(v) for v in values):
        return None
    estimate, lower, upper = map(float, values)
    if not 0-EPS <= lower <= estimate+EPS or not estimate-EPS <= upper <= 1010000+EPS:
        raise ValueError('invalid band identification bounds')
    return {'estimate': estimate, 'lower': lower, 'upper': upper}


def gate(estimate=None, lower=None, upper=None, threshold=0, unit='score_points'):
    if not all(finite(v) for v in (estimate, lower, upper)):
        return {'status': 'unavailable', 'estimate': None, 'lower': None, 'upper': None,
                'threshold': threshold, 'unit': unit}
    tolerance = RATING_EPS if unit == 'rating' else EPS
    status = 'clear' if lower >= threshold-tolerance else 'tendency' if estimate >= threshold-tolerance else 'below_threshold'
    return {'status': status, 'estimate': estimate, 'lower': lower, 'upper': upper,
            'threshold': threshold, 'unit': unit}


def tier_limits(key, policy):
    field = 'spread_threshold' if key == 'personal_spread' else 'tail_threshold'
    threshold = getattr(policy, field)
    if threshold == REFERENCE_DEFAULTS[field]:
        return PERSONAL_REFERENCE['tier_thresholds'][key]
    # A custom threshold is a single explicit gate, without silently deriving tiers.
    return {'large': {'estimate_threshold': threshold, 'evidence_threshold': threshold}}


def tier_gate(value, limits):
    if value['status'] == 'unavailable':
        return {**value, 'threshold': limits['estimate_threshold'],
                'evidence_threshold': limits['evidence_threshold'],
                'magnitude_supported_by_bounds': False}
    supported = value['estimate'] >= limits['estimate_threshold']-EPS
    clear = supported and value['lower'] >= limits['evidence_threshold']-EPS
    return {**value, 'status': 'clear' if clear else 'tendency' if supported else 'below_threshold',
            'threshold': limits['estimate_threshold'], 'evidence_threshold': limits['evidence_threshold'],
            'magnitude_supported_by_bounds': value['lower'] >= limits['estimate_threshold']-EPS}


def classify_gap(value, key, policy):
    tiers = {name: tier_gate(value, limits) for name, limits in tier_limits(key, policy).items()}
    magnitude = next((name for name in ('large', 'medium') if name in tiers and tiers[name]['status'] in POSITIVE), None)
    selected = tiers[magnitude or 'large']
    return {**selected, 'estimated_magnitude': magnitude, 'tier_gates': tiers}


def summarize_gap(rows, key, policy):
    tiers = {}
    for magnitude in tier_limits(key, policy):
        metric = lambda row, magnitude=magnitude: row[key]['tier_gates'][magnitude]
        signal = summarize(rows, metric, policy)
        signal['magnitude_bound_intervals'] = intervals(rows, metric,
            lambda value: value.get('magnitude_supported_by_bounds', False), policy)
        tiers[magnitude] = signal
    magnitude = next((name for name in ('large', 'medium') if name in tiers and tiers[name]['status'] in POSITIVE), None)
    return {**tiers[magnitude or 'large'], 'estimated_magnitude': magnitude,
            'magnitude_provider': 'grouped-band model estimate', 'tiers': tiers}


def intervals(rows, metric, predicate, policy):
    """Bucket-center endpoints only; gaps/ineligible/negative buckets break runs."""
    result, run = [], []
    def finish():
        if len(run) < policy.min_groups or run[-1]['rating']-run[0]['rating'] < policy.min_span-EPS:
            return
        values = [metric(r) for r in run]
        estimates = [v['estimate'] for v in values if finite(v.get('estimate'))]
        lowers = [v['lower'] for v in values if finite(v.get('lower'))]
        uppers = [v['upper'] for v in values if finite(v.get('upper'))]
        result.append({'rating_min': run[0]['rating'], 'rating_max': run[-1]['rating'],
            'rating_span': round(run[-1]['rating']-run[0]['rating'], 8), 'groups': len(run),
            'players_across_groups': sum(r['players'] for r in run),
            'strength': 'clear' if all(v['status'] == 'clear' for v in values) else 'tendency',
            'median_metric_value': median(estimates) if estimates else None,
            'median_metric_lower_bound': median(lowers) if lowers else None,
            'median_metric_upper_bound': median(uppers) if uppers else None,
            'metric_unit': values[0]['unit'],
            'cls': [r['cls'] for r in run]})
    for row in rows:
        value = metric(row)
        qualifies = row['eligible'] and predicate(value)
        adjacent = not run or (row['cls'] == run[-1]['cls']+1 and row['rating'] > run[-1]['rating'])
        if not qualifies or not adjacent:
            finish()
            run = []
        if qualifies:
            run.append(row)
    finish()
    return result


def summarize(rows, metric, policy):
    positive = intervals(rows, metric, lambda v: v['status'] in POSITIVE, policy)
    clear = intervals(rows, metric, lambda v: v['status'] == 'clear', policy)
    coverage = intervals(rows, metric, lambda v: v['status'] != 'unavailable', policy)
    status = 'clear' if clear else 'tendency' if positive else 'not_triggered' if coverage else 'insufficient_data'
    return {'status': status, 'intervals': positive, 'clear_intervals': clear,
            'coverage_intervals': coverage}


def present_ranges(signal):
    ranges = signal['clear_intervals'] if signal['status'] == 'clear' else signal['intervals']
    ranges = sorted(ranges, key=lambda r: (-r['rating_span'], r['rating_min']))
    shown = '、'.join(f"{r['rating_min']:.2f}–{r['rating_max']:.2f}" for r in ranges[:2])
    return shown + (f" 等{len(ranges)}段" if len(ranges) > 2 else '')


def compact_lines(result):
    def gap_text(signal, labels):
        status = signal['status']
        if status in POSITIVE:
            strength = '明顯' if status == 'clear' else '估計'
            return f"{labels[signal['estimated_magnitude']]}（{strength}）{present_ranges(signal)}"
        return '資料不足' if status == 'insufficient_data' else '未達持續門檻'
    personal = f"個人差：{gap_text(result['personal_spread'], {'large': '大', 'medium': '中等'})}；相對特化：{gap_text(result['relative_specialization'], {'large': '強', 'medium': '有傾向'})}"
    eating = []
    for name, signal in (('P50', result['eating_central']), ('P30', result['eating_familiar'])):
        if signal['status'] in POSITIVE:
            strength = '明顯' if signal['status'] == 'clear' else '估計'
            eating.append(f"{name}（{strength}）{present_ranges(signal)}")
    if eating:
        second = '吃分：'+'；'.join(eating)
    else:
        insufficient = all(result[k]['status'] == 'insufficient_data' for k in ('eating_central', 'eating_familiar'))
        second = '吃分：'+('資料不足' if insufficient else '未達持續門檻')
    return [personal, second]


def analyze(raw_groups, raw_bands, constant, rating_range, minimum=100, policy=None):
    policy = (policy or Policy()).validate()
    if minimum < 1 or len(rating_range) != 2 or not all(finite(v) for v in rating_range) or rating_range[0] > rating_range[1]:
        raise ValueError('invalid analysis scope')
    indexed = {}
    for row in raw_bands:
        name = row['line_id']
        if name not in BAND_RANGES:
            continue
        if (row['band_start_percent'], row['band_end_percent']) != BAND_RANGES[name]:
            raise ValueError('band name/range mismatch')
        key = row['cls'], name
        if key in indexed:
            raise ValueError('duplicate population band')
        indexed[key] = row
    features, seen = [], set()
    for group in sorted(raw_groups, key=lambda g: g['rating']):
        rating = group['rating']
        if not finite(rating) or not rating_range[0]-EPS <= rating <= rating_range[1]+EPS:
            continue
        if group['cls'] in seen:
            raise ValueError('duplicate rating bucket')
        seen.add(group['cls'])
        mean = group['average_score']
        eligible = group['players'] >= minimum and finite(mean) and group.get('eligible', True)
        b = {name: band_values(indexed.get((group['cls'], name))) for name in BAND_RANGES}
        spread = gate(threshold=policy.spread_threshold)
        tail = gate(threshold=policy.tail_threshold)
        p5_gap = None
        if eligible and b['P10'] and b['P70']:
            a, z = b['P10'], b['P70']
            spread = gate(a['estimate']-z['estimate'], max(0, a['lower']-z['upper']),
                max(0, a['upper']-z['lower']), policy.spread_threshold)
        if eligible and b['P10']:
            a = b['P10']
            tail = gate(a['estimate']-mean, max(0, a['lower']-mean),
                max(0, a['upper']-mean), policy.tail_threshold)
        spread = classify_gap(spread, 'personal_spread', policy)
        tail = classify_gap(tail, 'relative_specialization', policy)
        if eligible and b['P5']:
            p5_gap = b['P5']['estimate']-mean
        formula = reference.inverse_rating(str(rating), constant)
        score = formula['score']
        if formula['status'] == 'at_cap_score_interval':
            score = formula['benchmark_score']
        central = gate(threshold=policy.eating_rating_margin, unit='rating')
        familiar = gate(threshold=policy.eating_rating_margin, unit='rating')
        if eligible and score is not None:
            def advantage(name):
                a = b[name]
                if a is None:
                    return gate(threshold=policy.eating_rating_margin, unit='rating')
                band_rating = {key: reference.score_to_rating(value, constant) for key, value in a.items()}
                gap = {key: float(value-reference.dec(str(rating))) for key, value in band_rating.items()}
                return {**gate(gap['estimate'], gap['lower'], gap['upper'],
                    policy.eating_rating_margin, unit='rating'),
                    'band_rating': {key: float(value) for key, value in band_rating.items()},
                    'score_gap': {key: value-score for key, value in a.items()}}
            p50, p30 = advantage('P50'), advantage('P30')
            if p50['status'] in POSITIVE:
                central = p50
                familiar = {**p30, 'status': 'not_selected'}
            elif p50['status'] != 'unavailable' and p30['status'] in POSITIVE:
                central = p50
                certain_type = p30['status'] == 'clear' and p50['upper'] < policy.eating_rating_margin-RATING_EPS
                familiar = {**p30, 'status': 'clear' if certain_type else 'tendency'}
            else:
                central = p50
                familiar = p30 if p50['status'] != 'unavailable' else gate(threshold=policy.eating_rating_margin, unit='rating')
        features.append({'cls': group['cls'], 'rating': rating, 'players': group['players'],
            'eligible': eligible, 'published_mean_score': mean, 'bands': b,
            'p5_minus_mean_score': p5_gap, 'personal_spread': spread, 'relative_specialization': tail,
            'upper_tail': tail,
            'reference_status': formula['status'], 'comparison_score': score,
            'comparison_rating': rating if score is not None else None,
            'eating_central': central, 'eating_familiar': familiar})
    result = {'schema_version': 3, 'decision_provider': 'python', 'llm_analysis_used': False,
        'metric_source': 'raw grouped-band estimates; published group mean; no LOWESS input',
        'bound_kind': 'grade-count identification bounds, not sampling confidence intervals',
        'chart_constant': str(constant), 'rating_scope': list(rating_range), 'min_players': minimum,
        'policy': asdict(policy),
        'personal_reference': {**PERSONAL_REFERENCE['reference'],
            'file_sha256': hashlib.sha256(REFERENCE_PATH.read_bytes()).hexdigest(),
            'default_thresholds': REFERENCE_DEFAULTS,
            'anchors': {name: anchor['reference'] for name, anchor in PERSONAL_REFERENCE['references'].items()},
            'tier_thresholds': {key: tier_limits(key, policy) for key in ('personal_spread', 'relative_specialization')},
            'reference_defaults_active': policy.spread_threshold == REFERENCE_DEFAULTS['spread_threshold']
                and policy.tail_threshold == REFERENCE_DEFAULTS['tail_threshold']},
        'metric_units': {'personal_spread': 'score_points', 'relative_specialization': 'score_points', 'upper_tail': 'score_points',
            'eating_central': 'rating', 'eating_familiar': 'rating'},
        'band_definitions': {name: {'start_percent': start, 'end_percent': end,
            'population_order': 'descending_score', 'is_single_point_percentile': False}
            for name, (start, end) in BAND_RANGES.items()},
        'compatibility_aliases': {'upper_tail': 'relative_specialization'},
        'cause_identification': {'status': 'unknown', 'inferred_pattern_tags': [],
            'reason': 'Aggregate gaps suggest relative specialization; cannot identify keyboard, stamina or a specific chart feature'},
        'rules': {'personal_spread': 'P10-P70 independently selects large or medium estimated magnitude; no upper-tail condition required',
            'relative_specialization': 'P10-published_mean independently signals relative specialization; P5 is not an independent vote; causes remain unknown',
            'gap_evidence': 'Clear requires estimated magnitude threshold and identification lower bound above its separate detectability threshold; not proof of magnitude threshold',
            'eating_central': 'score_to_rating(P50)-bucket_rating >= eating_rating_margin',
            'eating_familiar': 'score_to_rating(P30)-bucket_rating >= eating_rating_margin while P50 rating advantage is below that margin',
            'continuity': 'consecutive cls; min_groups and min_span; no bridging gaps or extrapolation',
            'above_cap': 'exclude non-unique post-cap benchmark from eating judgments',
            'exact_cap': 'compare to the plotted SSS+ endpoint benchmark'},
        'groups': features}
    for key in ('personal_spread', 'relative_specialization'):
        result[key] = summarize_gap(features, key, policy)
    result['upper_tail'] = result['relative_specialization']
    for key in ('eating_central', 'eating_familiar'):
        result[key] = summarize(features, lambda row, key=key: row[key], policy)
    result['compact_lines'] = compact_lines(result)
    return result
