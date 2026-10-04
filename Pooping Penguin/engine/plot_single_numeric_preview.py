"""One Python plot of population-band estimated means and LOWESS trends.

Original grade counts and group means stay intact. Grouped-data estimates
are explicitly distinguished from unavailable exact population-band means.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import analyze_chunirec_numeric as core
import compare_chunirec_rating_reference as reference
import plot_b10_quantiles as quantiles
import population_band_numeric as bands
import numeric_plot_focus as focus_rules
import numeric_signals as signals

DEFAULT_INPUT = core.ROOT / 'table_probes/chunirec_Deep Blue_MASTER/20261004_112015'
DEFAULT_WIKI = core.ROOT / 'table_probes/chunirec_Air_ULTIMA/20261004_100328/rating_reference_20261004_105530/wiki_rating_source.html'
DISPLAY_BANDS = tuple(b for b in bands.BANDS if b[0] in ('P5', 'P10', 'P30', 'P70'))
COLORS = {'P5': '#7C3AED', 'P10': '#2563EB', 'P30': '#008C83', 'P70': '#EA6C19'}
LABELS = {'P5': 'P5 · P0–P10', 'P10': 'P10 ≈ 擅長 · P0–P20',
    'P30': 'P30 ≈ 熟悉 · P20–P40', 'P70': 'P70 ≈ 初見／苦手 · P60–P80'}
HIGHLIGHTED = ('MEAN', 'REFERENCE', 'P30')
SECONDARY = ('P5', 'P10')
FADED = ('P70',)
LEGEND_LABELS = {'P5': 'P5 高分端（估）', 'P10': 'P10 擅長（估）',
    'P30': 'P30 熟悉（估）', 'P70': 'P70 初見／苦手（估）'}


def descending_quantile_index(counts, percent):
    """Legacy v1 helper; not used in population-band outputs."""
    quantiles.quantile_index(counts, percent)
    n = sum(counts)
    if not n:
        return None
    position, total = (n*percent+99)//100, 0
    for i in reversed(range(len(counts))):
        total += counts[i]
        if total >= position:
            return i
    raise ValueError('unreachable descending quantile')


def score_intervals(formula):
    rows = formula['wiki_rows']
    edges = [0] + [rows[g]['score'] for g in ('S', 'SS', 'SS+', 'SSS', 'SSS+')] + [1010000]
    return [{'lower': lo, 'upper': hi, 'upper_inclusive': False}
            for lo, hi in zip(edges, edges[1:])] + [
                {'lower': 1010000, 'upper': 1010000, 'upper_inclusive': True}]


def build_data(groups, intervals, minimum, view_min, view_max, smooth_fraction, robust_iterations):
    raw_bands, raw_groups = [], []
    shown = [g for g in groups if (view_min is None or g['rating'] >= view_min)
             and (view_max is None or g['rating'] <= view_max)]
    for group in shown:
        counts = quantiles.grade_counts(group)
        check = bands.assess_group_mean(counts, intervals, group['average_score'])
        sample_eligible = group['players'] >= minimum
        reasons = ([] if sample_eligible else ['below_minimum_players'])
        if check['status'] != 'consistent':
            reasons.append(check['status'])
        eligible = not reasons
        # Excluded groups never reach model fitting. Unexpected model errors
        # still propagate instead of being mistaken for recoverable source data.
        model = bands.fit_grouped_model(counts, intervals, group['average_score']) if eligible else None
        raw_groups.append({**group, 'sample_eligible': sample_eligible, 'eligible': eligible,
            'exclusion_reasons': reasons, 'group_mean_validation': check,
            'within_grade_model': model, 'source_exclusive_grade_counts': dict(zip(core.LOW_TO_HIGH, counts))})
        values = []
        for name, start, end in DISPLAY_BANDS:
            result = bands.grouped_band_mean(counts, intervals, start, end, model)
            for piece in result['grade_contributions']:
                piece['source_grade'] = core.LOW_TO_HIGH[piece['grade_index']]
            raw_bands.append({'line_id': name, 'metric': 'population_band_mean',
                'is_single_point_percentile': False, 'band_start_percent': start, 'band_end_percent': end,
                'population_order': 'descending_score', 'rating': group['rating'], 'cls': group['cls'],
                'label': group['label'], 'source_group_players': group['players'],
                'eligible': eligible, 'exclusion_reasons': list(reasons),
                'group_validation_status': check['status'], 'published_group_mean_score': group['average_score'],
                'mean_status': 'estimated_from_grouped_grades' if model else 'unavailable',
                'reading_label': LABELS[name], 'source_locator': group['source_locator'], **result})
            values.append(result['estimated_band_mean_score'])
        if model:
            if any(a+1e-6 < b for a, b in zip(values, values[1:])):
                raise ValueError('raw population-band mean ordering violated')
            if abs(model['estimated_group_mean']-group['average_score']) > 1e-5:
                raise ValueError('grade model does not reconstruct published group mean')

    raw_trends, dense_trends = [], []
    definitions = [('MEAN', None, None)]+list(DISPLAY_BANDS)
    for name, start, end in definitions:
        points = [{'cls': g['cls'], 'rating': g['rating'], 'value': g['average_score']}
                  for g in raw_groups if g['eligible']] if name == 'MEAN' else [
                  {'cls': r['cls'], 'rating': r['rating'], 'value': r['estimated_band_mean_score']}
                  for r in raw_bands if r['line_id'] == name and r['eligible'] and r['estimated_band_mean_score'] is not None]
        raw, dense = bands.smooth_segments(points, smooth_fraction, robust_iterations)
        info = {'line_id': name, 'band_start_percent': start, 'band_end_percent': end,
                'metric': 'published_group_mean' if name == 'MEAN' else 'population_band_mean',
                'input_value_kind': 'observed' if name == 'MEAN' else 'model_estimate'}
        raw_trends.extend({**info, **r} for r in raw)
        dense_trends.extend({**info, **r} for r in dense)

    # Independent robust fits can cross. Project only cross-band ordering at
    # the same ability point; this never forces monotonicity across Rating.
    projection_count = 0
    for table in (raw_trends, dense_trends):
        by_x = {}
        for r in table:
            r['unconstrained_LOWESS_score'] = r['trend_score']
            r['band_order_projection_delta'] = 0.0
            if r['line_id'] != 'MEAN' and r['trend_score'] is not None:
                by_x.setdefault((r['segment_id'], round(r['rating'], 8)), {})[r['line_id']] = r
        for records in by_x.values():
            if len(records) != len(DISPLAY_BANDS):
                continue
            ordered = [records[name] for name, _, _ in DISPLAY_BANDS]
            values = [r['trend_score'] for r in ordered]
            if any(a < b for a, b in zip(values, values[1:])):
                projected = [-v for v in core.pava([-v for v in values], [1]*len(values))]
                for row, value in zip(ordered, projected):
                    row['band_order_projection_delta'] = value-row['trend_score']
                    row['trend_score'] = value
                projection_count += 1
    return raw_groups, raw_bands, raw_trends, dense_trends, projection_count


def build_analysis_bands(raw_groups, raw_bands, intervals):
    """P50 is P40-P60; reuse the same fitted group model, without a new line."""
    records = list(raw_bands)
    for group in raw_groups:
        counts = [group['source_exclusive_grade_counts'][name] for name in core.LOW_TO_HIGH]
        result = bands.grouped_band_mean(counts, intervals, 40, 60, group['within_grade_model'])
        for piece in result['grade_contributions']:
            piece['source_grade'] = core.LOW_TO_HIGH[piece['grade_index']]
        records.append({'line_id': 'P50', 'metric': 'population_band_mean',
            'is_single_point_percentile': False, 'band_start_percent': 40, 'band_end_percent': 60,
            'population_order': 'descending_score', 'rating': group['rating'], 'cls': group['cls'],
            'source_group_players': group['players'], 'eligible': group['eligible'],
            'exclusion_reasons': list(group['exclusion_reasons']),
            'group_validation_status': group['group_mean_validation']['status'],
            'published_group_mean_score': group['average_score'],
            'mean_status': 'estimated_from_grouped_grades' if group['within_grade_model'] else 'unavailable',
            'source_locator': group['source_locator'], **result})
    return records


def group_quality_report(raw_groups, minimum):
    excluded = [{'cls': g['cls'], 'rating': g['rating'], 'players': g['players'],
        'published_group_mean_score': g['average_score'],
        'exclusion_reasons': list(g['exclusion_reasons']),
        'group_mean_validation': g['group_mean_validation'],
        'source_exclusive_grade_counts': g['source_exclusive_grade_counts'],
        'source_locator': g['source_locator']} for g in raw_groups if not g['eligible']]
    reason_counts = {}
    for g in excluded:
        for reason in g['exclusion_reasons']:
            reason_counts[reason] = reason_counts.get(reason, 0)+1
    return {'schema_version': 1, 'decision_provider': 'python', 'llm_analysis_used': False,
        'min_players': minimum, 'total_groups': len(raw_groups),
        'eligible_groups': len(raw_groups)-len(excluded), 'excluded_group_count': len(excluded),
        'exclusion_reason_counts': reason_counts, 'excluded_groups': excluded,
        'policy': 'Check minimum population and mean/count compatibility before fitting; excluded groups break all plotted and judgment segments',
        'source_values_changed': False, 'inconsistent_means_clamped': False}


class NoEligibleGroupsError(ValueError):
    def __init__(self, quality):
        self.group_quality = quality
        super().__init__(f"沒有通過每組 {quality['min_players']} 人門檻與來源一致性校驗的有效分組；已保存排除原因。")


def render(output, manifest, raw_trends, dense_trends, minimum, focus, curve, fraction):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter, MultipleLocator
    from matplotlib import font_manager
    font_file = os.environ.get('RATING_BOT_FONT_FILE')
    families = ['Microsoft JhengHei', 'Yu Gothic', 'Microsoft YaHei', 'DejaVu Sans']
    if font_file:
        font_manager.fontManager.addfont(font_file)
        families = [font_manager.FontProperties(fname=font_file).get_name(), 'DejaVu Sans']
    plt.rcParams.update({'font.family': families, 'axes.unicode_minus': False, 'font.size': 10})
    fig, ax = plt.subplots(figsize=(10.0, 5.5))
    fig.subplots_adjust(left=.11, right=.985, bottom=.23, top=.98)
    handles = {}
    definitions = [(name, COLORS[name], LEGEND_LABELS[name]) for name, _, _ in DISPLAY_BANDS]+[
        ('MEAN', '#191919', '平均（實測）')]
    for name, color, label in definitions:
        emphasized = name in HIGHLIGHTED
        secondary = name in SECONDARY
        alpha = 1.0 if emphasized else .8 if secondary else .26
        width = 2.5 if emphasized else 1.5 if secondary else 1.1
        raw = [r for r in raw_trends if r['line_id'] == name]
        dense = [r for r in dense_trends if r['line_id'] == name]
        for segment in sorted({r['segment_id'] for r in raw}):
            d = [p for p in dense if p['segment_id'] == segment]
            r = [p for p in raw if p['segment_id'] == segment]
            chosen = d or r
            field = 'trend_score' if d else 'value'
            ax.plot([p['rating'] for p in chosen], [p[field] for p in chosen], color=color,
                lw=width, alpha=alpha, marker=None if d else 'o', ms=2.3,
                zorder=6 if emphasized else 3 if secondary else 2)
        handles[name] = Line2D([], [], color=color, lw=width, alpha=alpha, label=label)
    solid = curve['solid_points']
    if solid:
        ax.plot([p['rating'] for p in solid], [p['score'] for p in solid],
            color='#D9AA00', lw=2.5, marker='o' if len(solid) == 1 else None, zorder=7)
    if curve['post_cap_span'] is not None:
        ax.plot(curve['post_cap_span'], [curve['post_cap_score']]*2, color='#D9AA00', ls='--', lw=2.5)
    handles['REFERENCE'] = Line2D([], [], color='#D9AA00', lw=2.5,
        label='公式（虛線：SSS+）' if curve['post_cap_span'] is not None else '吃分公式')
    ax.set_ylim(*focus['y_limits'])
    ax.set_xlim(*focus['x_limits'])
    ax.set_ylabel('Score', labelpad=8)
    ax.set_xlabel('Rating（BEST 枠平均）', labelpad=8)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x:,.0f}'))
    ax.xaxis.set_major_locator(MultipleLocator(focus['ticks']['rating_step']))
    ax.yaxis.set_major_locator(MultipleLocator(focus['ticks']['score_step']))
    precision = max(2, -reference.dec(focus['ticks']['rating_step']).normalize().as_tuple().exponent)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x:.{precision}f}'))
    ax.spines[['top', 'right']].set_visible(False)
    ax.spines[['left', 'bottom']].set_color('#A2ABB7')
    ax.grid(axis='y', alpha=.1)
    order = ('MEAN', 'P5', 'REFERENCE', 'P10', 'P30', 'P70')
    legend = fig.legend(handles=[handles[name] for name in order], loc='lower center',
        bbox_to_anchor=(.55, .012), ncol=3, frameon=False, fontsize=9.5,
        handlelength=1.8, columnspacing=1.5, labelspacing=.75)
    for text, name in zip(legend.get_texts(), order):
        text.set_color('#26323F' if name in HIGHLIGHTED else '#56667B' if name in SECONDARY else '#9AA5B1')
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', manifest['title']).strip(' .') or 'song'
    path = output/f"{safe_title}_{manifest['difficulty']}_population_bands.png"
    fig.savefig(path, dpi=160, facecolor='white')
    plt.close(fig)
    focus['grade_labels'] = []
    return path


def run(args):
    source = args.input.resolve()
    manifest, stats, groups, hashes = core.load_input(source)
    formula = reference.extract_formula(args.wiki_file.read_bytes())
    intervals = score_intervals(formula)
    raw_groups, raw_bands, raw_trends, dense_trends, projections = build_data(groups, intervals,
        args.min_players, args.view_min, args.view_max, args.smooth_fraction, args.robust_iterations)
    quality = group_quality_report(raw_groups, args.min_players)
    if not raw_trends:
        raise NoEligibleGroupsError(quality)
    focus, curve = focus_rules.calculate_focus(raw_trends, dense_trends, manifest['chart']['const'],
                                               args.view_min, args.view_max, args.axis_mode)
    policy = signals.Policy(spread_threshold=getattr(args, 'spread_threshold', signals.Policy.spread_threshold),
        tail_threshold=getattr(args, 'tail_threshold', signals.Policy.tail_threshold),
        eating_rating_margin=getattr(args, 'eating_rating_margin', 0),
        min_groups=getattr(args, 'signal_min_groups', 3), min_span=getattr(args, 'signal_min_span', .2))
    analysis_bands = build_analysis_bands(raw_groups, raw_bands, intervals)
    judgments = signals.analyze(raw_groups, analysis_bands, manifest['chart']['const'],
        focus['reference_domain'], minimum=args.min_players, policy=policy)
    judgments['constant_confirmed'] = manifest['chart']['const_confirmed']
    output = getattr(args, 'output', None) or source / ('population_band_preview_'+datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y%m%d_%H%M%S'))
    output.mkdir(exist_ok=False)
    path = render(output, manifest, raw_trends, dense_trends, args.min_players,
                  focus, curve, args.smooth_fraction)
    (output/'selected_stats_original.json').write_bytes((source/'selected_stats.json').read_bytes())
    core.save_json(output/'source_groups_and_models.json', raw_groups)
    core.save_json(output/'population_band_raw.json', raw_bands)
    core.save_json(output/'analysis_bands.json', analysis_bands)
    core.save_json(output/'numeric_signals.json', judgments)
    core.save_json(output/'group_quality.json', quality)
    core.write_csv(output/'population_band_raw.csv', [
        {k: json.dumps(v, ensure_ascii=False) if k == 'grade_contributions' else v for k, v in r.items()} for r in raw_bands])
    core.write_csv(output/'group_trends.csv', raw_trends)
    if dense_trends:
        core.write_csv(output/'dense_trends.csv', dense_trends)
    core.write_csv(output/'formula_reference.csv', curve['solid_points']+[
        {'rating': x, 'score': curve['post_cap_score'], 'status': 'above_cap_sssplus_benchmark'}
        for x in (curve['post_cap_span'] or [])])
    from PIL import Image
    with Image.open(path) as image:
        dimensions = [image.width, image.height]
        image.verify()
    if not all(core.sha256(source/name) == digest for name, digest in hashes.items()):
        raise ValueError('source snapshot changed')
    core.save_json(output/'preview_manifest.json', {
        'schema_version': 8, 'status': 'complete', 'title': manifest['title'], 'difficulty': manifest['difficulty'],
        'image': path.name, 'dimensions': dimensions, 'source_url': manifest['source_url'],
        'input_snapshot': str(source), 'source_sha256': hashes,
        'script_sha256': core.sha256(Path(__file__)), 'numerical_module_sha256': core.sha256(Path(bands.__file__)),
        'focus_module_sha256': core.sha256(Path(focus_rules.__file__)),
        'signal_module_sha256': core.sha256(Path(signals.__file__)),
        'numeric_signals_file': 'numeric_signals.json', 'numeric_signal_policy': judgments['policy'],
        'group_quality_file': 'group_quality.json', 'eligible_groups': quality['eligible_groups'],
        'excluded_group_count': quality['excluded_group_count'],
        'formula_source': formula, 'formula_html_sha256': core.sha256(args.wiki_file), 'axis_label': 'BEST 枠平均',
        'min_players': args.min_players, 'input_view_range': [args.view_min, args.view_max],
        'view_range': focus['reference_domain'], 'focus': focus,
        'view_mode': 'focus' if args.axis_mode == 'focus' else 'full',
        'y_axis_range_policy': ('SS-to-MAX display limits; full raw data and full-data LOWESS retained'
            if args.axis_mode == 'focus' else 'adaptive: all population values, trends and formula reference with padding'),
        'metric': 'population_band_mean', 'is_single_point_percentile': False,
        'population_order': 'descending_score',
        'bands': [{'line_id': name, 'band_start_percent': start, 'band_end_percent': end} for name, start, end in DISPLAY_BANDS],
        'display': {'highlighted_lines': list(HIGHLIGHTED), 'secondary_lines': list(SECONDARY), 'faded_lines': list(FADED),
            'omitted_and_not_computed': ['P90', 'P95'], 'computed_not_drawn': ['P50'],
            'compact': True, 'visible_content': ['line_plot', 'score_axis', 'rating_axis', 'legend'],
            'raw_overlays_drawn': False, 'faded_trend_alpha': .26, 'secondary_trend_alpha': .8,
            'mean_provider': 'published group average, not a P50 band mean'},
        'boundary_policy': 'fractional population weights; no rounded player counts',
        'exact_band_means_available': False,
        'band_estimation': 'Common exponential tilt within each source grade; grade counts fixed; calibrated to published group mean',
        'bounds': 'grade-count identification bounds, not confidence intervals',
        'smoothing': {'method': 'robust local-linear LOWESS', 'fraction': args.smooth_fraction,
            'degree': 1, 'robust_iterations': args.robust_iterations, 'min_neighbours': 7,
            'min_contiguous_points': 5, 'missing_bucket_policy': 'break segment', 'extrapolation': False,
            'clipping': 'observed range within segment', 'source': bands.LOWESS_SOURCE,
            'population_order_projection_points': projections, 'projection': 'PAVA across band lines if they cross'},
        'source_group_means_changed': False, 'source_fetch_used': False, 'llm_analysis_used': False,
        'images_generated': 1, 'raw_groups_exported': len(raw_groups), 'raw_band_rows': len(raw_bands),
        'output_sha256': {p.name: core.sha256(p) for p in output.iterdir() if p.is_file()}})
    result = {'output': str(output), 'image': str(path), 'images': 1,
                      'band_rows': len(raw_bands), 'exact_band_means_available': False,
                      'focus_range': focus['reference_domain'], 'axis_mode': focus['mode']}
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=DEFAULT_INPUT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--wiki-file', type=Path, default=DEFAULT_WIKI)
    parser.add_argument('--min-players', type=int, default=100)
    parser.add_argument('--view-min', type=float, help='Optional input lower bound; auto focus remains enabled')
    parser.add_argument('--view-max', type=float, help='Optional input upper bound; auto focus remains enabled')
    parser.add_argument('--axis-mode', choices=('auto', 'fixed', 'focus'), default='focus')
    parser.add_argument('--smooth-fraction', type=float, default=.65)
    parser.add_argument('--robust-iterations', type=int, default=2)
    parser.add_argument('--spread-threshold', type=float, default=signals.Policy.spread_threshold)
    parser.add_argument('--tail-threshold', type=float, default=signals.Policy.tail_threshold)
    parser.add_argument('--eating-rating-margin', type=float, default=0)
    parser.add_argument('--signal-min-groups', type=int, default=3)
    parser.add_argument('--signal-min-span', type=float, default=.2)
    args = parser.parse_args()
    if (args.min_players < 1 or not 0 < args.smooth_fraction <= 1 or args.robust_iterations < 0
            or any(v is not None and (not math.isfinite(v) or v < 0) for v in (args.view_min, args.view_max))
            or (args.view_min is not None and args.view_max is not None and args.view_min >= args.view_max)
            or (args.axis_mode == 'fixed' and (args.view_min is None or args.view_max is None))):
        raise ValueError('invalid parameters')
    run(args)
