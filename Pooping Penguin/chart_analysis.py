"""
Pattern-skill estimate and song suggestions from a user's imported scores
plus the chart table in data/chart_tags.json (built by tools/build_chart_tags.py).
No discord imports, so it can be tested on its own. Python 3.9 safe.

Model
  gain(score)   rating gained over the chart constant (CHUNITHM curve; matches
                the reported rating of the test export to 0.0001).
  baseline      least-squares fit over ALL played charts with const >= 14:
                gain ~ 1 + const + (SSS-star difficulty - 3).
                = what this player typically scores at that difficulty.
  residual      actual gain - baseline gain, per chart.
  tag effect    mean residual over the charts carrying that tag, shrunk
                toward 0 by n/(n+SHRINK). Tags are unweighted: every tag on a
                chart gets that chart's residual in full.
Chart notes (sheet column 説明) are turned into flags by the builder. In suggestions:
  not_for_push / hard_score  -> chart is left out of the push list
  high_tolerance / easy_score -> push rank x1.25 ; low_tolerance / personal -> x0.75
  practice                   -> ranked first in the practice list
Push suggestions add a "grind premium" to the baseline: the mean residual of the
player's own B30/N20 charts, i.e. what they reach on charts they actually work on.
Caveat: players mostly play charts they expect to do well on, so the effects
are relative to the player's own average, not absolute skill.
"""
from collections import defaultdict
from statistics import median

import numpy as np

from ratingbot.catalog import normalize

MIN_CONST = 14.0
MIN_N = 5            # charts per tag required to report it (all-scores scope)
MIN_N_SMALL = 3      # same, for the best30 / rating50 scopes
SHRINK = 3.0
RANK_UP, RANK_DOWN = 1.25, 0.75
STRONG = 0.02        # shrunk residual (rating gain) counted as a strength
WEAK = -0.01         # ... and as a weakness (improve list)
VETO = -0.03         # a tag this weak keeps a chart out of the push list
MAX_GAIN = 2.15
_POINTS = [(975000, 0.0), (990000, 0.6), (1000000, 1.0),
           (1005000, 1.5), (1007500, 2.0), (1009000, 2.15)]


def gain(score):
    """Rating gain over the chart constant. Below 975,000 the slope is
    extrapolated (approximation); above 1,009,000 it is flat."""
    if score >= _POINTS[-1][0]:
        return MAX_GAIN
    if score < _POINTS[0][0]:
        return (score - _POINTS[0][0]) / 2500.0 * 0.1
    for (s0, g0), (s1, g1) in zip(_POINTS, _POINTS[1:]):
        if score < s1:
            return g0 + (g1 - g0) * (score - s0) / float(s1 - s0)
    return MAX_GAIN


def score_for_gain(g):
    if g >= MAX_GAIN:
        return _POINTS[-1][0]
    if g < 0:
        return _POINTS[0][0] + g / 0.1 * 2500.0
    for (s0, g0), (s1, g1) in zip(_POINTS, _POINTS[1:]):
        if g < g1:
            return s0 + (s1 - s0) * (g - g0) / (g1 - g0)
    return _POINTS[-1][0]


def build_index(table):
    idx = {}
    for c in table.get("charts", []):
        idx.setdefault((c["key"], c["difficulty"]), c)
    return idx


def _key(entry):
    return (normalize(entry["title"]), entry["difficulty"])


def _samples(entries, idx):
    out = []
    for s in entries:
        c = idx.get(_key(s))
        if c and c["const"] >= MIN_CONST and s["score"] > 0:
            out.append((c, s))
    return out


def _row(c):
    return [1.0, c["const"], float((c["sss_stars"] or 3) - 3)]


def fit_baseline(samples):
    """Return (beta, uses_stars) or None if there is too little data."""
    if len(samples) < 15:
        return None
    X = np.array([_row(c) for c, _ in samples])
    y = np.array([min(gain(s["score"]), MAX_GAIN) for _, s in samples])
    use_stars = len(samples) >= 30
    cols = [0, 1, 2] if use_stars else [0, 1]
    beta = np.linalg.lstsq(X[:, cols], y, rcond=None)[0]
    full = np.zeros(3)
    full[cols] = beta
    return full


def predict(beta, c):
    return float(np.dot(beta, _row(c)))


def tag_effects(samples, beta):
    acc = defaultdict(list)
    for c, s in samples:
        e = min(gain(s["score"]), MAX_GAIN) - predict(beta, c)
        for t in c["tags"]:
            acc[t].append(e)
    out = {}
    for t, v in acc.items():
        n = len(v)
        out[t] = {"n": n, "mean": sum(v) / n, "shrunk": sum(v) / (n + SHRINK)}
    return out


def _entries_for(rec, scope):
    if scope == "best":
        return rec.get("best", [])
    if scope == "rating":
        return list(rec.get("best", [])) + list(rec.get("new", []))
    return rec["scores"]


def pattern_report(rec, table, scope="all"):
    """Strength/weakness ranking of tags. Returns a dict or {'error': msg}."""
    idx = build_index(table)
    base_samples = _samples(rec["scores"], idx)
    beta = fit_baseline(base_samples)
    if beta is None:
        return {"error": "Too few played charts at const 14+ that match the chart sheet."}
    samples = base_samples if scope == "all" else _samples(_entries_for(rec, scope), idx)
    eff = tag_effects(samples, beta)
    min_n = MIN_N if scope == "all" else MIN_N_SMALL
    rows = sorted(({"tag": t, **v} for t, v in eff.items() if v["n"] >= min_n),
                  key=lambda r: r["shrunk"], reverse=True)
    return {"scope": scope, "charts": len(samples), "tagged": sum(1 for c, _ in samples if c["tags"]),
            "min_n": min_n, "rows": rows}


def suggestions(rec, table, top=5):
    """Return {'push': [...], 'improve': [...]} or {'error': msg}."""
    idx = build_index(table)
    base_samples = _samples(rec["scores"], idx)
    beta = fit_baseline(base_samples)
    if beta is None:
        return {"error": "Too few played charts at const 14+ that match the chart sheet."}
    eff = tag_effects(base_samples, beta)
    ok = {t: v["shrunk"] for t, v in eff.items() if v["n"] >= MIN_N}
    if not ok:
        return {"error": "Not enough tagged charts to find strengths or weaknesses yet."}

    user = {_key(s): s["score"] for s in rec["scores"]}
    best = [(c, s) for c, s in _samples(rec.get("best", []), idx)]
    new = [(c, s) for c, s in _samples(rec.get("new", []), idx)]
    if not best or not new:
        return {"error": "Your export's best/new lists don't match the chart sheet."}
    r_of = lambda c, s: c["const"] + gain(s["score"])
    best_floor = min(r_of(c, s) for c, s in best)
    new_floor = min(r_of(c, s) for c, s in new)
    in_lists = {_key(s): r_of(c, s) for c, s in best + new}
    new_titles = {normalize(s["title"]) for _, s in new}
    premium = sum(min(gain(s["score"]), MAX_GAIN) - predict(beta, c)
                  for c, s in best + new) / float(len(best) + len(new))
    consts = [c["const"] for c, _ in best + new]
    level, ceiling = median(consts), max(consts)
    n_list = len(best) + len(new)

    push, improve = [], []
    weak_tags = {t for t, e in ok.items() if e <= WEAK}
    for c in table["charts"]:
        if c["const"] < MIN_CONST or not c["tags"]:
            continue
        k = (c["key"], c["difficulty"])
        cur = user.get(k)
        cur_r = c["const"] + gain(cur) if cur else None
        effs = [ok[t] for t in c["tags"] if t in ok]
        if not effs:
            continue

        fl = set(c.get("flags") or [])

        # strength-based: only charts with no known weak tag
        if (not fl & {"not_for_push", "hard_score"} and c["const"] <= ceiling and min(effs) > VETO
                and sum(effs) / len(effs) >= STRONG):
            ce = sum(effs) / len(effs)
            pg = min(MAX_GAIN, max(0.0, predict(beta, c) + premium + ce))
            pred_r = c["const"] + pg
            if cur_r is not None and pred_r < cur_r + 0.03:
                continue
            base = in_lists.get(k)
            if base is None:
                base = new_floor if c["key"] in new_titles else best_floor
            delta = (pred_r - base) / n_list
            if delta >= 0.002:
                mult = 1.0
                if fl & {"high_tolerance", "easy_score"}:
                    mult *= RANK_UP
                if fl & {"low_tolerance", "personal"}:
                    mult *= RANK_DOWN
                push.append({"chart": c, "current": cur, "target": int(round(score_for_gain(pg), -2)),
                             "delta": delta, "rank": delta * mult,
                             "tags": [t for t in c["tags"] if t in ok]})

        # weakness-based: practice charts at/below the player's level
        wt = [t for t in c["tags"] if t in weak_tags]
        if (wt and level - 1.0 <= c["const"] <= level + 0.2
                and (c["sss_stars"] or 0) < 4
                and not (cur and cur >= 1007500)):
            improve.append({"chart": c, "current": cur, "tags": wt,
                            "prio": sum(ok[t] for t in wt),
                            "practice": "practice" in fl})

    push.sort(key=lambda r: r["rank"], reverse=True)
    improve.sort(key=lambda r: (not r["practice"], r["prio"], r["current"] is not None))
    return {"push": push[:top], "improve": improve[:top], "effects": ok,
            "best_floor": best_floor, "new_floor": new_floor, "level": level,
            "premium": premium}
