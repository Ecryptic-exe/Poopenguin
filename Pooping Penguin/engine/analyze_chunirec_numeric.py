"""Offline numerical features from one saved Chunirec chart; no LLM or network.

The ability axis is the published BEST-slot-average bucket, not total Rating.
The core uses the standard library. --plot optionally uses matplotlib.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from statistics import NormalDist
from zoneinfo import ZoneInfo

VERSION = "0.1.0"
ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "table_probes/chunirec_Air_ULTIMA/20261004_100328"
HIGH_TO_LOW = ("max", "sssp", "sss", "ssp", "ss", "s")
GRADE_LABEL = {"max": "MAX", "sssp": "SSS+", "sss": "SSS", "ssp": "SS+",
               "ss": "SS", "s": "S"}
LOW_TO_HIGH = ("OTHER", "S", "SS", "SS+", "SSS", "SSS+", "MAX")
TARGETS = ("ssp", "sss", "sssp")
PROBABILITIES = (0.10, 0.50, 0.80, 0.90)
METHOD_SOURCES = {
    "wilson": "https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm",
    "weighted_isotonic": "https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.isotonic_regression.html",
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows):
    if not rows:
        raise ValueError(f"cannot write a table without columns: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    expected = [{k: "" if v is None else str(v) for k, v in row.items()} for row in rows]
    if read_csv(path) != expected:
        raise ValueError(f"exported CSV values changed during roundtrip: {path}")


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def wilson(successes, n, confidence=0.95):
    """Pointwise binomial interval; does not correct cohort selection bias."""
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError("invalid binomial counts")
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    p, z2 = successes / n, z * z
    denominator = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denominator
    return max(0.0, center - radius), min(1.0, center + radius)


def pava(values, weights):
    """Weighted increasing isotonic least squares using pool-adjacent violators."""
    if len(values) != len(weights):
        raise ValueError("values/weights length mismatch")
    blocks = []
    for index, (value, weight) in enumerate(zip(values, weights)):
        if weight <= 0 or not math.isfinite(value):
            raise ValueError("nonfinite value or nonpositive weight")
        blocks.append([index, index + 1, float(value) * weight, weight])
        while len(blocks) > 1 and blocks[-2][2] / blocks[-2][3] > blocks[-1][2] / blocks[-1][3]:
            right = blocks.pop()
            left = blocks.pop()
            blocks.append([left[0], right[1], left[2] + right[2], left[3] + right[3]])
    result = [None] * len(values)
    for start, end, total, weight in blocks:
        result[start:end] = [total / weight] * (end - start)
    return result


def crossing(xs, ys, probability):
    """Invert the fitted curve without extrapolation; grid bracket is not a CI."""
    result = {"status": "insufficient_data", "estimate": None, "bracket_low": None,
              "bracket_high": None, "lower_index": None, "upper_index": None}
    if not xs:
        return result
    if ys[0] > probability:
        return {**result, "status": "left_censored", "bracket_high": xs[0]}
    for i, value in enumerate(ys):
        if math.isclose(value, probability, abs_tol=1e-12):
            return {**result, "status": "observed_grid_point", "estimate": xs[i],
                    "bracket_low": xs[i], "bracket_high": xs[i],
                    "lower_index": i, "upper_index": i}
        if i and ys[i - 1] < probability < value:
            estimate = xs[i - 1] + (xs[i] - xs[i - 1]) * (probability - ys[i - 1]) / (value - ys[i - 1])
            return {**result, "status": "interpolated", "estimate": estimate,
                    "bracket_low": xs[i - 1], "bracket_high": xs[i],
                    "lower_index": i - 1, "upper_index": i}
    return {**result, "status": "right_censored", "bracket_low": xs[-1]}


def grade_quantile(counts, probability):
    n = sum(counts)
    if not n:
        return None
    position, running = math.ceil(n * probability), 0
    for label, count in zip(LOW_TO_HIGH, counts):
        running += count
        if running >= position:
            return label
    raise ValueError("unreachable quantile")


def selected_from_embedded(text):
    decoder, pos, selected = json.JSONDecoder(), 0, {}
    while pos < len(text):
        while pos < len(text) and text[pos] in "' \r\n\t":
            pos += 1
        if pos == len(text):
            break
        prefix = text[pos]
        if prefix not in "BAEMU" or prefix in selected:
            raise ValueError("unexpected/duplicate embedded difficulty prefix")
        value, pos = decoder.raw_decode(text, pos + 1)
        selected[prefix] = value
    return selected


class EmbeddedStatsParser(HTMLParser):
    """Read the saved text/plain script as data; never execute JavaScript."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active, self.blocks, self.current = False, [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") == "urec_pdx":
            if attrs.get("type") != "text/plain":
                raise ValueError("statistics script is not text/plain")
            self.active, self.current = True, []

    def handle_data(self, data):
        if self.active:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.blocks.append("".join(self.current))
            self.active = False


def load_input(folder):
    names = ("manifest.json", "selected_stats.json", "embedded_stats.txt",
             "rating_score_table.csv", "rating_rank_counts.csv", "source.html", "music.js")
    hashes = {name: sha256(folder / name) for name in names}
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise ValueError("source collection did not complete")
    stats = json.loads((folder / "selected_stats.json").read_text(encoding="utf-8"))
    prefixes = {"BASIC": "B", "ADVANCED": "A", "EXPERT": "E", "MASTER": "M", "ULTIMA": "U"}
    prefix = prefixes[manifest["difficulty"]]
    if selected_from_embedded((folder / "embedded_stats.txt").read_text(encoding="utf-8"))[prefix] != stats:
        raise ValueError("selected JSON differs from the original embedded chart data")
    for name, key in (("rating_score_table.csv", "csv_sha256"), ("rating_rank_counts.csv", "rank_csv_sha256")):
        if hashes[name] != manifest[key]:
            raise ValueError(f"saved collection checksum mismatch: {name}")
    for entry in manifest["fetches"]:
        name = Path(entry["raw_file"]).name
        if name in hashes and hashes[name] != entry["sha256"]:
            raise ValueError(f"raw checksum mismatch: {name}")
    parser = EmbeddedStatsParser()
    parser.feed((folder / "source.html").read_text(encoding="utf-8"))
    # The original collector's Windows text write can double a leading CRLF.
    # Compare every parsed difficulty object; keep both original files unchanged.
    if len(parser.blocks) != 1 or selected_from_embedded(parser.blocks[0]) != selected_from_embedded((folder / "embedded_stats.txt").read_text(encoding="utf-8")):
        raise ValueError("embedded statistics file differs from checksum-verified source HTML")
    score_rows, rank_rows = read_csv(folder / "rating_score_table.csv"), read_csv(folder / "rating_rank_counts.csv")
    averages, groups = {}, {}
    for rating, score in stats["avg_score"]:
        if rating in averages:
            raise ValueError("duplicate average-score bucket")
        averages[rating] = score
    if len(score_rows) != len(averages) or len(rank_rows) != len(stats["byr"]):
        raise ValueError("CSV/source row count mismatch")
    for group, row in zip(stats["byr"], rank_rows):
        cls, n = group["cls"], group["players"]
        if cls in groups or type(cls) is not int or type(n) is not int or n < 0:
            raise ValueError("invalid or duplicate rank bucket")
        if any(type(group["rank"][k]) is not int or group["rank"][k] < 0 for k in HIGH_TO_LOW):
            raise ValueError("missing or invalid rank counts")
        if sum(group["rank"].values()) > n:
            raise ValueError("exclusive rank counts exceed bucket players")
        if int(row["rating_class_raw"]) != cls or int(row["players"]) != n:
            raise ValueError("CSV bucket identity/count mismatch")
        if any(int(row[k]) != group["rank"][k] for k in HIGH_TO_LOW):
            raise ValueError("CSV ranks differ from original JSON")
        label = format(Decimal(cls) / 10, ".2f")
        if row["best_average_rating"] != (label if label in averages else ""):
            raise ValueError("CSV ability label mismatch")
        groups[cls] = {**group, "rating": float(Decimal(cls) / 10), "label": label,
                       "average_score": averages.get(label), "source_locator": row["source_locator"]}
    seen = set()
    for row in score_rows:
        label = row["best_average_rating"]
        if label in seen or label not in averages:
            raise ValueError("unknown or duplicate CSV score bucket")
        seen.add(label)
        if row["average_score"] != ("" if averages[label] is None else str(averages[label])):
            raise ValueError("CSV average differs from original JSON")
        group = groups.get(int(Decimal(label) * 10))
        if group is None:
            if row['group_players'] != '' or averages[label] is not None:
                raise ValueError('unmatched nonempty mean/rank group')
        elif int(row["group_players"]) != group["players"]:
            raise ValueError("mean/rank group count mismatch")
        for field in ("level", "const", "notes"):
            if row[field] != manifest["chart"][field]:
                raise ValueError(f"CSV chart metadata mismatch: {field}")
        if row["const_confirmed"] != str(manifest["chart"]["const_confirmed"]):
            raise ValueError("constant confirmation mismatch")
    for row in score_rows + rank_rows:
        if (row["title"], row["difficulty"], row["source_url"]) != (manifest["title"], manifest["difficulty"], manifest["source_url"]):
            raise ValueError("mixed chart or source in CSV")
    if sum(g["players"] for g in groups.values()) > stats["players"]:
        raise ValueError("bucket population exceeds chart population")
    # A raw class such as cls=0 can be an unplotted aggregate. Only use labels
    # explicitly published on the mean-score axis as numerical ability points.
    plotted = [g for g in groups.values() if g['label'] in averages]
    return manifest, stats, sorted(plotted, key=lambda g: g["rating"]), hashes


def successes(group, target):
    return sum(group["rank"][key] for key in HIGH_TO_LOW[:HIGH_TO_LOW.index(target) + 1])


def threshold_table(groups, minimum):
    eligible = [g for g in groups if g["players"] >= minimum]
    xs, weights = [g["rating"] for g in eligible], [g["players"] for g in eligible]
    curves, rows = {}, []
    for target in TARGETS:
        raw = [successes(g, target) / g["players"] for g in eligible]
        fit = pava(raw, weights)
        curves[target] = {g["cls"]: p for g, p in zip(eligible, fit)}
        for probability in PROBABILITIES:
            point = crossing(xs, fit, probability)
            lo, hi = point.pop("lower_index"), point.pop("upper_index")
            rows.append({"grade": GRADE_LABEL[target], "achievement_probability": probability,
                         "min_bucket_players": minimum, "eligible_buckets": len(eligible), **point,
                         "lower_bucket_players": eligible[lo]["players"] if lo is not None else None,
                         "upper_bucket_players": eligible[hi]["players"] if hi is not None else None,
                         "lower_source_locator": eligible[lo]["source_locator"] if lo is not None else None,
                         "upper_source_locator": eligible[hi]["source_locator"] if hi is not None else None})
    # Identical buckets/weights for all grades preserve nested cumulative curves.
    for cls in curves["ssp"]:
        if not curves["ssp"][cls] + 1e-12 >= curves["sss"][cls] >= curves["sssp"][cls] - 1e-12:
            raise ValueError("fitted cumulative grade ordering violated")
    return rows, curves


def feature(key, message, evidence, scope="within_chart_observation"):
    return {"feature_id": key, "scope": scope, "message": message, "evidence": evidence}


def analyze(groups, manifest, stats, minimum, confidence):
    thresholds, fits = threshold_table(groups, minimum)
    threshold_map = {(r["grade"], r["achievement_probability"]): r for r in thresholds}
    curve_rows, spreads, jumps = [], [], []
    for group in groups:
        n = group["players"]
        if not n:
            continue
        counts = [n - sum(group["rank"].values())] + [group["rank"][k] for k in reversed(HIGH_TO_LOW)]
        quantiles = [grade_quantile(counts, p) for p in (0.05, 0.50, 0.95)]
        entropy = -sum((count / n) * math.log2(count / n) for count in counts if count)
        spreads.append({"best_average_rating": group["label"], "players": n,
                        "eligible": n >= minimum, "average_score": group["average_score"],
                        "p05_grade": quantiles[0], "median_grade": quantiles[1], "p95_grade": quantiles[2],
                        "rank_step_span_p95_p05": LOW_TO_HIGH.index(quantiles[2]) - LOW_TO_HIGH.index(quantiles[0]),
                        "rank_entropy_bits": entropy, "normalized_rank_entropy": entropy / math.log2(7),
                        "other_count": counts[0], "source_locator": group["source_locator"]})
        for target in TARGETS:
            k = successes(group, target)
            lo, hi = wilson(k, n, confidence)
            curve_rows.append({"best_average_rating": group["label"], "grade": GRADE_LABEL[target],
                               "players": n, "successes": k, "raw_rate": k / n,
                               "wilson_low": lo, "wilson_high": hi,
                               "eligible": n >= minimum, "isotonic_rate": fits[target].get(group["cls"]),
                               "source_locator": group["source_locator"]})
    eligible = [g for g in groups if g["players"] >= minimum]
    for target in TARGETS:
        for left, right in zip(eligible, eligible[1:]):
            p1, p2 = successes(left, target) / left["players"], successes(right, target) / right["players"]
            l1, h1 = wilson(successes(left, target), left["players"], confidence)
            l2, h2 = wilson(successes(right, target), right["players"], confidence)
            jumps.append({"grade": GRADE_LABEL[target], "from_bucket": left["label"], "to_bucket": right["label"],
                          "from_players": left["players"], "to_players": right["players"],
                          "from_rate": p1, "to_rate": p2, "change_percentage_points": 100 * (p2 - p1),
                          "rate_change_per_best_average": (p2 - p1) / (right["rating"] - left["rating"]),
                          "pointwise_wilson_nonoverlap": h1 < l2 or h2 < l1,
                          "from_source_locator": left["source_locator"], "to_source_locator": right["source_locator"]})
    gaps = []
    for lower, upper in (("SS+", "SSS"), ("SSS", "SSS+")):
        a, b = threshold_map[(lower, 0.50)], threshold_map[(upper, 0.50)]
        observed = a["estimate"] is not None and b["estimate"] is not None
        gaps.append({"from_grade": lower, "to_grade": upper, "probability": 0.5,
                     "estimate_gap": b["estimate"] - a["estimate"] if observed else None,
                     "grid_gap_low": b["bracket_low"] - a["bracket_high"] if observed else None,
                     "grid_gap_high": b["bracket_high"] - a["bracket_low"] if observed else None,
                     "status": "available" if observed else "censored_or_unavailable"})
    features = []
    for row in thresholds:
        if row["achievement_probability"] == 0.50:
            message = (f"{row['grade']} 的 50% 達成門檻位於 BEST 枠平均 {row['bracket_low']:.2f}–{row['bracket_high']:.2f} 的觀測點之間。"
                       if row["estimate"] is not None else f"{row['grade']} 的 50% 達成門檻在目前可靠分組範圍內無法定位。")
            features.append(feature("threshold_50_" + row["grade"], message, row))
    if all(g["estimate_gap"] is not None for g in gaps):
        ratio = gaps[1]["estimate_gap"] / gaps[0]["estimate_gap"] if gaps[0]["estimate_gap"] > 0 else None
        features.append(feature("top_end_threshold_gap",
                        f"SSS→SSS+ 的 50% 門檻間距約 {gaps[1]['estimate_gap']:.2f}；SS+→SSS 約 {gaps[0]['estimate_gap']:.2f}。這是同一譜面內的成績線比較。",
                        {"gaps": gaps, "top_to_previous_gap_ratio": ratio, "peer_baseline_available": False}))
    top_jumps = [r for r in jumps if r["grade"] == "SSS+"]
    if top_jumps:
        largest = max(top_jumps, key=lambda row: row["change_percentage_points"])
        features.append(feature("steepest_sssplus_observed_step",
                        f"SSS+ 以上達成率的最大相鄰上升出現在 {largest['from_bucket']}→{largest['to_bucket']}：{largest['from_rate']:.1%}→{largest['to_rate']:.1%}，增加 {largest['change_percentage_points']:.1f} 個百分點。",
                        largest))
    early = next((g for g in groups if g["players"] and successes(g, "sssp")), None)
    if early:
        k, n = successes(early, "sssp"), early["players"]
        lo, hi = wilson(k, n, confidence)
        features.append(feature("first_observed_sssplus",
                        f"最早有 SSS+ 以上紀錄的是 {early['label']} 組，{k}/{n} 人（{k/n:.2%}）；此項只記錄提早達成，未判定專精能力。",
                        {"bucket": early["label"], "successes": k, "players": n, "rate": k/n,
                         "wilson_low": lo, "wilson_high": hi, "source_locator": early["source_locator"]}))
    lower, upper = threshold_map[("SSS+", 0.10)], threshold_map[("SSS+", 0.80)]
    if lower["estimate"] is not None and upper["estimate"] is not None:
        features.append(feature("sssplus_10_to_80_transition",
                        f"SSS+ 達成率由 10% 成長到 80% 的插值區間約為 {lower['estimate']:.2f}–{upper['estimate']:.2f}，寬度約 {upper['estimate']-lower['estimate']:.2f}。",
                        {"R10": lower, "R80": upper, "width": upper["estimate"]-lower["estimate"]}))
    if eligible:
        peak = max((r for r in spreads if r["eligible"]), key=lambda row: row["rank_entropy_bits"])
        features.append(feature("widest_rank_mix",
                        f"合格分組中，{peak['best_average_rating']} 組的成績級別熵最高；P05／中位數／P95 為 {peak['p05_grade']}／{peak['median_grade']}／{peak['p95_grade']}。這是粗略分布訊號，未換算成精確分數離散度。", peak))
        large = [g for g in eligible if g["players"] >= 500]
        high = large[-1] if large else eligible[-1]
        k, n = successes(high, "sssp"), high["players"]
        lo, hi = wilson(n-k, n, confidence)
        features.append(feature("upper_bucket_below_sssplus",
                        f"最高且至少 500 人的組別為 {high['label']}；其中 {n-k}/{n} 人（{1-k/n:.1%}）尚未達 SSS+。"
                        if large else f"最高合格組 {high['label']} 尚有 {n-k}/{n} 人（{1-k/n:.1%}）未達 SSS+；沒有至少 500 人的高端組。",
                        {"bucket": high["label"], "players": n, "below_sssplus": n-k,
                         "rate": 1-k/n, "wilson_low": lo, "wilson_high": hi,
                         "source_locator": high["source_locator"]}))
    mean_drops = [{"from_bucket": a["label"], "to_bucket": b["label"],
                   "from_players": a["players"], "to_players": b["players"],
                   "score_drop": a["average_score"] - b["average_score"]}
                  for a, b in zip(eligible, eligible[1:])
                  if a["average_score"] is not None and b["average_score"] is not None
                  and a["average_score"] > b["average_score"]]
    if mean_drops:
        features.append(feature("nonmonotone_raw_mean",
                        f"有 {len(mean_drops)} 個合格相鄰組的原始平均分下降；沒有分數變異量可檢驗原因或顯著性。", mean_drops))
    total, covered = stats["players"], sum(g["players"] for g in groups)
    quality = {"total_chart_players": total, "bucket_players": covered,
               "bucket_coverage_fraction": covered / total if total else None,
               "players_without_published_bucket": total - covered,
               "observed_buckets": len(groups), "eligible_buckets": len(eligible),
               "excluded_small_buckets": [g["label"] for g in groups if g["players"] < minimum],
               "axis": "published BEST-slot-average bucket", "axis_is_total_player_rating": False,
               "const_confirmed": manifest["chart"]["const_confirmed"],
               "statistics_generated_at": manifest.get("statistics_generated_at"),
               "region": None, "game_version": None,
               "bucket_means_and_rank_counts_same_cohort_verified": False,
               "percentiles_are_grade_categories": True,
               "other_category_is_unlisted_remainder": True,
               "threshold_brackets_are_grid_resolution_not_confidence_intervals": True}
    # Keep the full range. This only shows the effect of a hypothetical old window.
    const = float(manifest["chart"]["const"])
    outside = [g for g in groups if abs(g["rating"] - const) > 2.15]
    window = {"applied": False, "reason": "BEST average and chart constant are not a verified common ability scale",
              "half_width": 2.15, "outside_buckets": [g["label"] for g in outside],
              "outside_players": sum(g["players"] for g in outside),
              "outside_sssplus_or_max": sum(successes(g, "sssp") for g in outside),
              "published_bucket_sssplus_or_max": sum(successes(g, "sssp") for g in groups)}
    return {"thresholds": thresholds, "curves": curve_rows, "spreads": spreads,
            "jumps": jumps, "gaps": gaps, "features": features, "quality": quality, "window_diagnostic": window}


def threshold_text(row):
    if row["estimate"] is not None:
        return f"{row['bracket_low']:.2f}–{row['bracket_high']:.2f}（插值 {row['estimate']:.2f}）"
    if row["status"] == "left_censored":
        return f"低於可靠資料起點 {row['bracket_high']:.2f}，不外推"
    if row["status"] == "right_censored":
        return f"截至 {row['bracket_low']:.2f} 尚未達成，不外推"
    return "資料不足"


def write_report(path, data, manifest, minimum, confidence):
    quality, window = data["quality"], data["window_diagnostic"]
    lines = [f"# {manifest['title']}／{manifest['difficulty']} 純數值試作", "",
             "本報告由 Python 固定模板與數值規則生成，未呼叫 LLM，未產生譜面 tags。", "",
             f"來源：{manifest['source_url']}",
             f"來源取得時間：{manifest['completed_at']}；統計生成日期未知。",
             f"橫軸是網站 BEST 枠平均分組，不是玩家總 Rating。定數 {manifest['chart']['const']} 的確認狀態為 {manifest['chart']['const_confirmed']}。", "",
             "## Python 提取的觀測特徵", ""]
    lines += [f"- {f['message']}" for f in data["features"]]
    lines += ["", "## 高端達成門檻", "", "範圍是觀測分組標籤形成的網格區間，不是信賴區間；插值僅供比較。", "",
              "| 成績線 | 10% | 50% | 80% | 90% |", "| --- | --- | --- | --- | --- |"]
    for target in TARGETS:
        rows = [r for r in data["thresholds"] if r["grade"] == GRADE_LABEL[target]]
        lines.append("| " + GRADE_LABEL[target] + " | " + " | ".join(threshold_text(r) for r in rows) + " |")
    lines += ["", "## 高端原始達成率", "", "以上達成率均包含更高 rank；SSS+ 包含 MAX。", "",
              "| BEST 枠平均組 | 人數 | SS+ 以上 | SSS 以上 | SSS+ 以上 | P05／中位數／P95 |",
              "| --- | ---: | ---: | ---: | ---: | --- |"]
    for spread in data["spreads"]:
        if float(spread["best_average_rating"]) < 17.0:
            continue
        rows = [r for r in data["curves"] if r["best_average_rating"] == spread["best_average_rating"]]
        lines.append(f"| {spread['best_average_rating']} | {spread['players']} | " +
                     " | ".join(f"{r['raw_rate']:.1%}" for r in rows) +
                     f" | {spread['p05_grade']}／{spread['median_grade']}／{spread['p95_grade']} |")
    lines += ["", "## 資料品質與方法", "",
              f"- {quality['observed_buckets']} 個原始分組，{quality['eligible_buckets']} 個達到預設每組至少 {minimum} 人的擬合門檻。低樣本組仍保存。",
              f"- 分組合計 {quality['bucket_players']} 人，網站總計 {quality['total_chart_players']} 人；覆蓋 {quality['bucket_coverage_fraction']:.1%}。其餘 {quality['players_without_published_bucket']} 人的分組位置未知，未補入任何組。",
              f"- 以組內玩家數加權的 PAVA 單調回歸估計達成率曲線，不擬合平均分；每個原始達成率保存 {confidence:.0%} Wilson 區間。",
              "- Wilson 區間只處理二項模型下的計數不確定性，未校正玩家自選、練習次數、歷史最佳成績或多重比較。相鄰區間不重疊只是描述性旗標。",
              "- 每個成績線使用相同合格分組與權重，並核對 SS+ ≥ SSS ≥ SSS+ 的累積達成率順序。",
              "- 百分位沿用網站圖表的級別順序；剩餘人數保留來源名稱 OTHER，沒有自行分成其他成績線。",
              "- 輸出最低樣本門檻 50／100／200／500 人的敏感度表；門檻超出資料範圍時留空並記錄截尾狀態。",
              "- 平均分與 rank 分布依同頁分組標籤對照，但來源未明確證實兩者納入完全相同的玩家。平均分單獨保留，不據此重建個體分布。",
              f"- 未套用定數 ±2.15；若直接套用會排除 {', '.join(window['outside_buckets']) or '無'} 組，及已分組的 {window['outside_sssplus_or_max']}/{window['published_bucket_sssplus_or_max']} 筆 SSS+ 以上紀錄。", "",
              "## 本資料尚不能識別", "",
              "- 同定數相對難易或虛高／虛低：缺少同版本的其他譜面基準。",
              "- 精確分數 IQR、標準差、residual、雙峰或玩家適性：缺少逐玩家分數與可串接的玩家資料。",
              "- 縱連、尾殺、局部難等配置原因，或持續卡關時間：此表沒有譜面位置與遊玩歷程。", "",
              "## 方法來源", "",
              f"- Wilson：{METHOD_SOURCES['wilson']}",
              f"- Weighted isotonic／PAVA：{METHOD_SOURCES['weighted_isotonic']}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def plot_curves(folder, data, manifest, minimum):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    colors = {"SS+": "#177C7A", "SSS": "#3166B5", "SSS+": "#BB5130"}
    fig, axes = plt.subplots(2, 1, figsize=(10, 8), layout="constrained", sharex=True)
    for grade, color in colors.items():
        rows = [r for r in data["curves"] if r["grade"] == grade]
        valid = [r for r in rows if r["eligible"]]
        axes[0].plot([float(r["best_average_rating"]) for r in valid],
                     [r["isotonic_rate"] for r in valid], color=color, label=f"{grade} or higher")
        for r in rows:
            x, y = float(r["best_average_rating"]), r["raw_rate"]
            axes[0].errorbar(x, y, yerr=[[y-r["wilson_low"]], [r["wilson_high"]-y]],
                             fmt="o", markersize=3 if r["eligible"] else 2,
                             color=color, alpha=0.65 if r["eligible"] else 0.20, capsize=2)
    axes[0].axhline(0.5, color="#999999", linestyle="--", linewidth=0.8)
    axes[0].set_ylabel("Observed achievement rate")
    axes[0].set_ylim(-0.035, 1.04)
    axes[0].yaxis.set_major_formatter(PercentFormatter(1))
    axes[0].legend(loc="upper left")
    fig.suptitle(f"{manifest['title']} / {manifest['difficulty']} — high-rank achievement", fontsize=14)
    axes[0].set_title(f"Published points with {data['metadata']['confidence']:.0%} pointwise Wilson intervals; lines: weighted isotonic fit", fontsize=9)
    for grade in colors:
        row = next(r for r in data["thresholds"] if r["grade"] == grade and r["achievement_probability"] == 0.5)
        if row["estimate"] is not None:
            axes[0].annotate(f"{grade} R50 ~ {row['estimate']:.2f}",
                             xy=(row["estimate"], 0.5), xytext=(row["estimate"]-0.28, 0.58+0.1*list(colors).index(grade)),
                             color=colors[grade], fontsize=9,
                             arrowprops={"arrowstyle": "-", "color": colors[grade]})
    spreads = data["spreads"]
    axes[1].bar([float(r["best_average_rating"]) for r in spreads],
                [r["players"] for r in spreads], width=0.075, color="#7A899C")
    axes[1].axhline(minimum, color="#BB5130", linestyle="--", label=f"Fit minimum n={minimum}")
    axes[1].set_ylabel("Published bucket players")
    axes[1].set_xlabel("Published BEST-slot-average bucket (not total player Rating)")
    axes[1].legend(loc="upper left")
    axes[1].set_xlim(16.8, 17.76)
    for ax in axes:
        ax.grid(axis="y", alpha=0.2)
        ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(folder / "achievement_curves.png", dpi=160)
    fig.savefig(folder / "achievement_curves.svg")
    plt.close(fig)


def run(args):
    source = args.input.resolve()
    if args.min_players < 1 or not 0 < args.confidence < 1:
        raise ValueError("invalid minimum players or confidence")
    manifest, stats, groups, hashes = load_input(source)
    data = analyze(groups, manifest, stats, args.min_players, args.confidence)
    sensitivity = []
    for minimum in sorted({50, 100, 200, 500, args.min_players}):
        rows, _ = threshold_table(groups, minimum)
        sensitivity.extend(rows)
    data["threshold_sensitivity"] = sensitivity
    r50s = [r for r in sensitivity if r["grade"] == "SSS+" and r["achievement_probability"] == 0.5]
    if all(r["estimate"] is not None for r in r50s):
        span = max(r["estimate"] for r in r50s) - min(r["estimate"] for r in r50s)
        if span <= 0.01:
            data["features"].append(feature("sssplus_r50_minimum_sample_stability",
                    "SSS+ 的 50% 門檻在每組最低人數 50／100／200／500 的設定下變化不超過 0.01。這只檢查樣本門檻敏感度。",
                    {"estimate_span": span, "thresholds": r50s}, "sample_filter_sensitivity"))
    censored = [r for r in sensitivity if r["grade"] == "SSS+" and r["achievement_probability"] in (0.8, 0.9)
                and r["status"] == "right_censored"]
    if censored:
        data["features"].append(feature("sssplus_upper_threshold_sample_sensitivity",
                "SSS+ 的 80%／90% 門檻依賴最高端小樣本組；較高最低人數設定下超出可靠資料範圍，不能定位。",
                censored, "sample_filter_sensitivity"))
    data["metadata"] = {"analyzer_version": VERSION, "analyzer_sha256": sha256(Path(__file__)), "title": manifest["title"],
                        "difficulty": manifest["difficulty"], "source_url": manifest["source_url"],
                        "input_folder": str(source), "input_sha256": hashes,
                        "chart": manifest["chart"], "min_bucket_players": args.min_players,
                        "confidence": args.confidence, "llm_used": False, "network_used": False,
                        "tags_generated": False, "method_sources": METHOD_SOURCES}
    output = args.output.resolve() if args.output else source / ("python_analysis_" + datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d_%H%M%S"))
    if output == source or output in source.parents:
        raise ValueError("output must not replace the source directory")
    output.mkdir(parents=True, exist_ok=False)
    for name, key in (("achievement_rates.csv", "curves"), ("thresholds.csv", "thresholds"),
                      ("rank_distribution.csv", "spreads"), ("adjacent_changes.csv", "jumps"),
                      ("threshold_gaps.csv", "gaps"), ("threshold_sensitivity.csv", "threshold_sensitivity")):
        write_csv(output / name, data[key])
    write_csv(output / "features.csv", [{"feature_id": f["feature_id"], "scope": f["scope"],
                                        "message": f["message"], "evidence_json": json.dumps(f["evidence"], ensure_ascii=False),
                                        "source_url": manifest["source_url"]} for f in data["features"]])
    save_json(output / "features.json", data)
    write_report(output / "REPORT.md", data, manifest, args.min_players, args.confidence)
    if args.plot:
        plot_curves(output, data, manifest, args.min_players)
    unchanged = all(sha256(source / name) == checksum for name, checksum in hashes.items())
    if not unchanged:
        raise ValueError("source files changed during analysis")
    roundtrip = []
    for path in output.glob("*.csv"):
        rows = read_csv(path)
        if not rows:
            raise ValueError(f"empty exported CSV: {path}")
        roundtrip.append({"file": path.name, "rows": len(rows), "sha256": sha256(path)})
    if len(read_csv(output / "achievement_rates.csv")) != sum(g["players"] > 0 for g in groups) * len(TARGETS):
        raise ValueError("exported achievement row count mismatch")
    audit = {"status": "passed", "analyzer_version": VERSION, "source_files_unchanged": unchanged,
             "raw_json_and_both_csvs_match": True, "raw_collection_hashes_match": True,
             "all_embedded_difficulties_match_source_html": True,
             "fitted_grade_order_valid": True, "csv_roundtrip": roundtrip,
             "output_sha256": {p.name: sha256(p) for p in output.iterdir() if p.is_file()},
             "completed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat()}
    save_json(output / "validation.json", audit)
    print(json.dumps({"status": "complete", "folder": str(output), "features": len(data["features"]),
                      "quality": data["quality"], "thresholds_R50": [r for r in data["thresholds"] if r["achievement_probability"] == 0.5],
                      "feature_messages": [f["message"] for f in data["features"]]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-players", type=int, default=100)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--plot", action="store_true")
    run(parser.parse_args())
