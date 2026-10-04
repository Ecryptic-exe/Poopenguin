"""Deterministic display bounds for numeric population plots; no data trimming."""
from __future__ import annotations

import math

import compare_chunirec_rating_reference as reference


def finite(value):
    return value is not None and math.isfinite(float(value))


def tick_step(span, target_intervals):
    """Choose a readable interval from 1/2/2.5/5 times a power of ten."""
    if not finite(span) or span <= 0:
        raise ValueError('tick span must be positive and finite')
    raw = span / target_intervals
    scale = 10 ** math.floor(math.log10(raw))
    return next(multiplier * scale for multiplier in (1, 2, 2.5, 5, 10)
                if multiplier * scale >= raw)


def reference_curve(start, end, constant, samples=400):
    """Reference exists only over the population view, including the cap marker."""
    cap = float(reference.dec(constant) + reference.dec('2.15'))
    solid = []
    if start <= cap:
        stop = min(end, cap)
        xs = [start] if stop == start else [
            start + (stop-start)*i/(samples-1) for i in range(samples)]
        for x in xs:
            # The formula uses decimal breakpoints: make the exact cap exact.
            argument = str(cap) if x == cap else f'{x:.8f}'
            result = reference.inverse_rating(argument, constant)
            score = result['score']
            if score is None:
                score = result['benchmark_score']
            solid.append({'rating': x, 'score': score, 'status': result['status']})
    return {'cap_rating': cap, 'solid_points': solid,
            'post_cap_span': [max(start, cap), end] if end > cap else None,
            'post_cap_score': 1009000}


def default_focus(raw_trends, dense_trends, constant):
    """Display SS-to-MAX at constant +1.00 to +2.15, without trimming inputs."""
    start = float(reference.dec(constant) + reference.dec('1.00'))
    end = float(reference.dec(constant) + reference.dec('2.15'))
    points = [r for r in raw_trends if finite(r['rating']) and finite(r['value'])]
    if not points:
        raise ValueError('no eligible finite population points for focus')
    in_x = [r for r in points if start <= r['rating'] <= end]
    visible = [r for r in in_x if 1000000 <= r['value'] <= 1010000]
    curve = reference_curve(start, end, constant)
    values = [r['value'] for r in in_x] + [
        r['trend_score'] for r in dense_trends
        if finite(r['rating']) and start <= r['rating'] <= end and finite(r['trend_score'])]
    values.extend(r['score'] for r in curve['solid_points'])
    focus = {'version': 2, 'mode': 'focus', 'decision_provider': 'python',
        'population_domain': [min(r['rating'] for r in points), max(r['rating'] for r in points)],
        'visible_population_domain': [min(r['rating'] for r in in_x), max(r['rating'] for r in in_x)] if in_x else None,
        'reference_domain': [start, end], 'x_limits': [start, end],
        'y_limits': [1000000, 1010000], 'visible_score_extent': [min(values), max(values)],
        'eligible_raw_points': len(points), 'visible_population_points': len(visible),
        'x_padding': 0, 'y_padding': 0,
        'ticks': {'rating_step': tick_step(end-start, 12), 'score_step': 1000},
        'rules': {'x_domain': 'chart constant +1.00 through +2.15; exact display limits',
            'y_domain': 'SS (1000000) through MAX (1010000)',
            'data_policy': 'display limits only; keep all raw groups and fit LOWESS over full data',
            'missing_groups': 'kept as gaps; never filled or extrapolated'},
        'formula_cap_rating': curve['cap_rating'], 'post_cap_span': curve['post_cap_span'],
        'points_clipped_by_focus': len(points)-len(visible)}
    return focus, curve


def calculate_focus(raw_trends, dense_trends, constant, view_min=None, view_max=None, mode='auto'):
    """Fit all eligible points, all visible trends and their formula reference.

    Raw/trend tables are not mutated. Auto mode never lets reference-only
    regions or unused grade boundaries expand the population x-domain.
    """
    if mode == 'focus':
        return default_focus(raw_trends, dense_trends, constant)
    if mode not in ('auto', 'fixed'):
        raise ValueError('unknown axis mode')
    for limit in (view_min, view_max):
        if limit is not None and (not finite(limit) or limit < 0):
            raise ValueError('view limits must be finite and nonnegative')
    if view_min is not None and view_max is not None and view_min >= view_max:
        raise ValueError('view min must be below view max')
    points = [r for r in raw_trends if finite(r['rating']) and finite(r['value'])
              and (view_min is None or r['rating'] >= view_min)
              and (view_max is None or r['rating'] <= view_max)]
    if not points:
        raise ValueError('no eligible finite population points for focus')
    data_min = min(r['rating'] for r in points)
    data_max = max(r['rating'] for r in points)
    if mode == 'fixed':
        if view_min is None or view_max is None:
            raise ValueError('fixed axes require both view limits')
        start, end = view_min, view_max
    else:
        start, end = data_min, data_max
    span = end-start
    x_padding = max(span*.03, .025) if span else .1
    x_limits = [max(0, start-x_padding), end+x_padding]
    curve = reference_curve(start, end, constant)
    population_values = [r['value'] for r in points]
    trend_values = [r['trend_score'] for r in dense_trends
                    if finite(r['rating']) and start <= r['rating'] <= end and finite(r['trend_score'])]
    formula_values = [r['score'] for r in curve['solid_points'] if finite(r['score'])]
    if curve['post_cap_span'] is not None:
        formula_values.append(curve['post_cap_score'])
    visible_values = population_values + trend_values + formula_values
    low, high = min(visible_values), max(visible_values)
    y_padding = max((high-low)*.06, 100)
    y_limits = [max(0, low-y_padding), high+y_padding]
    focus = {'version': 1, 'mode': mode, 'decision_provider': 'python',
        'population_domain': [data_min, data_max], 'reference_domain': [start, end],
        'x_limits': x_limits, 'y_limits': y_limits,
        'visible_score_extent': [low, high], 'eligible_raw_points': len(points),
        'x_padding': x_padding, 'y_padding': y_padding,
        'ticks': {'rating_step': tick_step(x_limits[1]-x_limits[0], 12),
                  'score_step': tick_step(y_limits[1]-y_limits[0], 8)},
        'rules': {'x_domain': 'finite eligible population points, not reference-only regions',
            'x_padding_fraction': .03, 'x_padding_minimum': .025, 'single_bucket_x_padding': .1,
            'y_domain': 'all visible raw values, LOWESS trends and formula reference',
            'y_padding_fraction': .06, 'y_padding_minimum': 100,
            'score_limits': 'linear axis; no percentile clipping or fixed grade anchors',
            'missing_groups': 'kept as gaps; never filled for focus'},
        'formula_cap_rating': curve['cap_rating'], 'post_cap_span': curve['post_cap_span'],
        'points_clipped_by_focus': 0}
    return focus, curve


def grade_label_positions(grades, y_limits, height_points, minimum_gap=13.5, edge_padding=6):
    """Space visible grade labels in physical points, keeping them inside axes."""
    ymin, ymax = y_limits
    visible = [(score, label) for score, label in grades if ymin <= score <= ymax]
    if not visible:
        return []
    lower = min(edge_padding, height_points/4)
    upper = height_points-lower
    gap = min(minimum_gap, (upper-lower)/max(1, len(visible)-1))
    positions = [min(upper, max(lower, (score-ymin)/(ymax-ymin)*height_points))
                 for score, _ in visible]
    for i in range(1, len(positions)):
        positions[i] = max(positions[i], positions[i-1]+gap)
    positions[-1] = min(positions[-1], upper)
    for i in range(len(positions)-2, -1, -1):
        positions[i] = min(positions[i], positions[i+1]-gap)
    return [{'score': score, 'label': label, 'axes_fraction': position/height_points}
            for (score, label), position in zip(visible, positions)]
