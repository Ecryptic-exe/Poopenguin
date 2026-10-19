"""Population-band means and robust local-linear LOWESS; no LLM or network.

Grouped grade counts do not identify exact score-band means. The grouped
adapter exports bounds and an explicit distribution estimate, while keeping
exact means null. The separate score adapter computes actual empirical means.
"""
from __future__ import annotations

import math
from fractions import Fraction

import numpy as np

BANDS = (
    ('P5', 0, 10), ('P10', 0, 20), ('P30', 20, 40), ('P50', 40, 60),
    ('P70', 60, 80), ('P90', 80, 100), ('P95', 90, 100),
)
LOWESS_SOURCE = 'https://www.statsmodels.org/stable/generated/statsmodels.nonparametric.smoothers_lowess.lowess.html'


def validate_band(start, end):
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= 100:
        raise ValueError('band must be integer population percentages in 0..100')


def band_pieces(counts, intervals, start, end):
    """Exact fractional overlaps of a descending population band and grades."""
    validate_band(start, end)
    if len(counts) != len(intervals) or any(type(n) is not int or n < 0 for n in counts):
        raise ValueError('invalid exclusive counts')
    n = sum(counts)
    left, right = Fraction(n*start, 100), Fraction(n*end, 100)
    cursor = 0
    pieces = []
    for i in reversed(range(len(counts))):
        count = counts[i]
        a, b = max(left, cursor), min(right, cursor+count)
        if b > a:
            pieces.append({'grade_index': i, 'effective_population_weight': float(b-a),
                'fraction_from_grade_top': float((a-cursor)/count),
                'fraction_to_grade_top': float((b-cursor)/count),
                'source_grade_players': count})
        cursor += count
    if not math.isclose(sum(p['effective_population_weight'] for p in pieces), float(right-left), abs_tol=1e-9):
        raise ValueError('band population overlap mismatch')
    return pieces


def interval_limits(interval):
    low = interval['lower']
    high = interval['upper']-int(not interval['upper_inclusive'])
    if not 0 <= low <= high <= 1010000:
        raise ValueError('invalid integer score interval')
    return float(low), float(high)


def unit_exponential_mean(z):
    """Mean on [0,1] for density proportional to exp(z*x), stably evaluated."""
    if abs(z) < 1e-3:
        return .5 + z/12 - z**3/720 + z**5/30240
    if z < 0:
        return 1-unit_exponential_mean(-z)
    return 1/(-math.expm1(-z))-1/z


def unit_exponential_quantile(t, z):
    if not 0 <= t <= 1:
        raise ValueError('CDF fraction outside [0,1]')
    if t == 0 or t == 1:
        return t
    if abs(z) < 1e-7:
        return t + z*t*(1-t)/2
    if z < 0:
        return 1-unit_exponential_quantile(1-t, -z)
    if z < 50:
        return math.log1p(t*math.expm1(z))/z
    return 1+math.log(t+(1-t)*math.exp(-z))/z


def assess_group_mean(counts, intervals, observed_mean):
    """Check source compatibility without fitting or changing the source mean."""
    if len(counts) != len(intervals) or any(type(n) is not int or n < 0 for n in counts):
        raise ValueError('invalid exclusive counts')
    n = sum(counts)
    limits = [interval_limits(i) for i in intervals]
    low = sum(c*l for c, (l, h) in zip(counts, limits))/n if n else None
    high = sum(c*h for c, (l, h) in zip(counts, limits))/n if n else None
    if not n:
        status = 'empty_population'
    elif observed_mean is None:
        status = 'missing_mean'
    elif not math.isfinite(observed_mean):
        status = 'nonfinite_mean'
    elif not low-1e-7 <= observed_mean <= high+1e-7:
        status = 'mean_outside_grade_count_bounds'
    else:
        status = 'consistent'
    return {'status': status, 'minimum_possible_mean_score': low,
            'maximum_possible_mean_score': high}


def fit_grouped_model(counts, intervals, observed_mean):
    """One common exponential tilt, with counts fixed, fitted to the group mean.

    This is an explicit within-grade model assumption, not recovered players.
    The reported integer group mean is used as the calibration target.
    """
    check = assess_group_mean(counts, intervals, observed_mean)
    if check['status'] in ('empty_population', 'missing_mean'):
        return None
    if check['status'] == 'nonfinite_mean':
        raise ValueError('nonfinite source average')
    if check['status'] != 'consistent':
        raise ValueError('source average outside grade-count bounds')
    n = sum(counts)
    limits = [interval_limits(i) for i in intervals]
    low, high = check['minimum_possible_mean_score'], check['maximum_possible_mean_score']
    kind, beta = 'exponential_tilt', 0.0
    if abs(observed_mean-low) < 1e-7:
        kind = 'lower_endpoint'
    elif abs(observed_mean-high) < 1e-7:
        kind = 'upper_endpoint'
    else:
        def mean_at(value):
            return sum(c*(l+(h-l)*unit_exponential_mean(value*(h-l)/10000))
                       for c, (l, h) in zip(counts, limits))/n
        a, b = -1.0, 1.0
        for _ in range(60):
            if mean_at(a) <= observed_mean <= mean_at(b):
                break
            a *= 2; b *= 2
        else:
            raise ValueError('could not bracket within-grade calibration')
        for _ in range(80):
            beta = (a+b)/2
            if mean_at(beta) < observed_mean:
                a = beta
            else:
                b = beta
    category_means = [l if kind == 'lower_endpoint' else h if kind == 'upper_endpoint'
        else l+(h-l)*unit_exponential_mean(beta*(h-l)/10000) for l, h in limits]
    fitted = sum(c*m for c, m in zip(counts, category_means))/n
    if abs(fitted-observed_mean) > 1e-5:
        raise ValueError('group mean calibration residual too large')
    return {'kind': kind, 'tilt_per_10000_score_points': beta,
        'observed_group_mean': observed_mean, 'estimated_group_mean': fitted,
        'calibration_residual_score': fitted-observed_mean, 'category_means': category_means}


def model_slice_mean(interval, model, a, b):
    low, high = interval_limits(interval)
    if model['kind'] == 'lower_endpoint' or low == high:
        return low
    if model['kind'] == 'upper_endpoint':
        return high
    z = model['tilt_per_10000_score_points']*(high-low)/10000
    q0, q1 = unit_exponential_quantile(1-b, z), unit_exponential_quantile(1-a, z)
    # Conditioning an exponential density on a CDF slice yields the same
    # exponential density on the slice's score interval. No fake player rows.
    unit_mean = q0+(q1-q0)*unit_exponential_mean(z*(q1-q0))
    return low+(high-low)*unit_mean


def grouped_band_mean(counts, intervals, start, end, model):
    pieces = band_pieces(counts, intervals, start, end)
    mass = sum(p['effective_population_weight'] for p in pieces)
    if not mass:
        return {'effective_population_weight': 0.0, 'exact_band_mean_score': None,
                'estimated_band_mean_score': None, 'band_mean_lower_bound_score': None,
                'band_mean_upper_bound_score': None, 'grade_contributions': []}
    lower = upper = estimate = 0.0
    for piece in pieces:
        i, weight = piece['grade_index'], piece['effective_population_weight']
        lo, hi = interval_limits(intervals[i])
        lower += weight*lo; upper += weight*hi
        value = model_slice_mean(intervals[i], model, piece['fraction_from_grade_top'],
                                 piece['fraction_to_grade_top']) if model else None
        piece['estimated_slice_mean_score'] = value
        if value is not None:
            estimate += weight*value
    result = {'effective_population_weight': mass, 'exact_band_mean_score': None,
        'estimated_band_mean_score': estimate/mass if model else None,
        'band_mean_lower_bound_score': lower/mass, 'band_mean_upper_bound_score': upper/mass,
        'grade_contributions': pieces}
    if model and not result['band_mean_lower_bound_score']-1e-6 <= result['estimated_band_mean_score'] <= result['band_mean_upper_bound_score']+1e-6:
        raise ValueError('band estimate outside identification bounds')
    return result


def exact_population_band_mean(scores, start, end):
    """For future real individual scores: fractional population slices, no ceil.

    Boundaries split one observed person's weight instead of rounding a
    20%-wide population interval to a different number of players.
    """
    validate_band(start, end)
    if any(type(s) is not int or not 0 <= s <= 1010000 for s in scores):
        raise ValueError('invalid individual scores')
    ordered = sorted(scores, reverse=True)
    n = len(ordered)
    if not n:
        return None
    a, b = Fraction(n*start, 100), Fraction(n*end, 100)
    total = Fraction(0)
    for i, score in enumerate(ordered):
        weight = max(Fraction(0), min(b, i+1)-max(a, i))
        total += weight*score
    return float(total/(b-a))


def local_linear_lowess(xs, ys, evaluation, fraction=.65, iterations=2, min_neighbours=7):
    """Tricube local degree-one regression plus bisquare residual reweighting.

    Values are evaluated only within the observed x range. No global
    polynomial fit, extrapolation, or synthetic source observations.
    """
    x, y, ev = np.asarray(xs, float), np.asarray(ys, float), np.asarray(evaluation, float)
    if len(x) != len(y) or len(x) < 5 or np.any(np.diff(x) <= 0):
        raise ValueError('LOWESS needs at least five distinct sorted points')
    if not 0 < fraction <= 1 or type(iterations) is not int or iterations < 0:
        raise ValueError('invalid smoothing parameters')
    if not all(np.all(np.isfinite(v)) for v in (x, y, ev)) or np.any(ev < x[0]-1e-10) or np.any(ev > x[-1]+1e-10):
        raise ValueError('nonfinite or extrapolated LOWESS evaluation')
    neighbours = min(len(x), max(min_neighbours, math.ceil(len(x)*fraction)))
    robust = np.ones(len(x))
    def estimate(at):
        distance = np.abs(x-at)
        radius = np.partition(distance, neighbours-1)[neighbours-1]*(1+1e-12)
        spatial = np.maximum(0, 1-(distance/radius)**3)**3
        weights = spatial*robust
        if np.count_nonzero(weights > 1e-12) < 2:
            weights = spatial  # Degenerate robust weights: spatial fit, not zero.
        centered = x-at
        mx, my = np.average(centered, weights=weights), np.average(y, weights=weights)
        variance = np.sum(weights*(centered-mx)**2)
        slope = np.sum(weights*(centered-mx)*(y-my))/variance if variance > 1e-20 else 0.0
        return my-slope*mx
    for _ in range(iterations):
        fit = np.array([estimate(at) for at in x])
        residual = np.abs(y-fit)
        median = float(np.median(residual))
        if median < 1e-8:
            break
        scaled = np.minimum(1, residual/(6*median))
        robust = (1-scaled**2)**2
    return [float(v) for v in np.clip([estimate(at) for at in ev], np.min(y), np.max(y))]


def smooth_segments(points, fraction=.65, iterations=2):
    """Do not bridge a missing/ineligible ability bucket or extrapolate."""
    segments, current = [], []
    for point in points:
        if current and point['cls'] != current[-1]['cls']+1:
            segments.append(current); current = []
        current.append(point)
    if current:
        segments.append(current)
    raw, dense = [], []
    for segment_id, segment in enumerate(segments):
        xs, ys = [p['rating'] for p in segment], [p['value'] for p in segment]
        if len(segment) < 5:
            raw.extend({**p, 'segment_id': segment_id, 'trend_score': None,
                        'smoothing_status': 'insufficient_contiguous_points'} for p in segment)
            continue
        grid = np.linspace(xs[0], xs[-1], round((xs[-1]-xs[0])*100)+1)
        fit = local_linear_lowess(xs, ys, xs, fraction, iterations)
        trend = local_linear_lowess(xs, ys, grid, fraction, iterations)
        raw.extend({**p, 'segment_id': segment_id, 'trend_score': t,
                    'smoothing_status': 'LOWESS'} for p, t in zip(segment, fit))
        dense.extend({'rating': float(x), 'segment_id': segment_id, 'trend_score': t}
                     for x, t in zip(grid, trend))
    return raw, dense
