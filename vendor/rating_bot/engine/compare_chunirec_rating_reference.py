"""Python-only inverse Rating reference, one figure and template conclusions.

Fetch/cache the requested Wiki formula page, verify its numerical boundaries,
then compare with the saved chart's published BEST-average bucket means.
No LLM, tag extraction, remote JavaScript execution, or extra song collection.
"""
from __future__ import annotations

import argparse
import json
import math
import unicodedata
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

import analyze_chunirec_numeric as core

WIKI_URL = "https://wikiwiki.jp/chunithmwiki/レーティング・OVER%20POWER"
VERSION = "0.1.0"


def dec(value):
    return Decimal(str(value))


def extract_formula(html):
    soup = BeautifulSoup(html, "html.parser")
    table = next((t for t in soup.find_all("table")
                  if "レーティング値" in t.get_text() and "1,009,000" in t.get_text()), None)
    if table is None:
        raise ValueError("Wiki single-chart Rating table not found")
    rows = {}
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 3:
            continue
        rank = cells[0].get_text(" ", strip=True)
        if rank in ("C", "BBB", "A", "AA", "AAA", "S", "S+", "SS", "SS+", "SSS", "SSS+"):
            raw_score = unicodedata.normalize("NFKC", cells[1].get_text(strip=True))
            expression = unicodedata.normalize("NFKC", cells[2].get_text(strip=True))
            expression = "".join(expression.split()).replace("−", "-").replace("－", "-")
            rows[rank] = {"score": int(raw_score.replace(",", "")), "rating_expression": expression}
    expected = {"C": (500000, "0"), "BBB": (800000, "(譜面定数-5.0)/2"),
                "A": (900000, "譜面定数-5.0"), "S": (975000, "譜面定数"),
                "S+": (990000, "譜面定数+0.6"), "SS": (1000000, "譜面定数+1.0"),
                "SS+": (1005000, "譜面定数+1.5"), "SSS": (1007500, "譜面定数+2.0"),
                "SSS+": (1009000, "譜面定数+2.15")}
    for rank, (score, expression) in expected.items():
        if rows.get(rank) != {"score": score, "rating_expression": expression}:
            raise ValueError(f"Wiki boundary differs from the supported formula: {rank}: {rows.get(rank)}")
    text = soup.get_text(" ", strip=True)
    if "75,000" not in text or "5.00" not in text:
        raise ValueError("A-to-S linear interpolation evidence missing")
    return {"source_url": WIKI_URL + "#rating_single", "source_locator": "単曲レートの算出 / rank table",
            "wiki_rows": rows, "formula_era": "NEW and later",
            "AA_AAA_policy": "use the stated A-to-S linear formula, not rounded table offsets",
            "precision_policy": "continuous interpolation, without assumptions about display truncation"}


def anchors(constant):
    c = dec(constant)
    if not c.is_finite() or c <= 5:
        raise ValueError("this Lv.15+ probe requires a finite constant above 5")
    return [(Decimal(500000), Decimal(0)), (Decimal(800000), (c-5)/2),
            (Decimal(900000), c-5), (Decimal(975000), c),
            (Decimal(1000000), c+1), (Decimal(1005000), c+dec("1.5")),
            (Decimal(1007500), c+2), (Decimal(1009000), c+dec("2.15"))]


def score_to_rating(score, constant):
    score = dec(score)
    if not score.is_finite() or not 0 <= score <= 1010000:
        raise ValueError("invalid score")
    points = anchors(constant)
    if score <= points[0][0]:
        return Decimal(0)
    if score >= points[-1][0]:
        return points[-1][1]
    for (s0, r0), (s1, r1) in zip(points, points[1:]):
        if s0 <= score <= s1:
            return r0 + (score-s0)*(r1-r0)/(s1-s0)
    raise ValueError("score outside supported intervals")


def inverse_rating(rating, constant):
    """Exact cap: score interval. Above cap: empty inverse, benchmark only."""
    rating = dec(rating)
    points = anchors(constant)
    if not rating.is_finite() or rating < 0:
        raise ValueError("invalid Rating")
    common = {"rating": float(rating), "score": None, "score_interval": None,
              "benchmark_score": None, "status": None}
    if rating > points[-1][1]:
        return {**common, "status": "above_chart_rating_cap_no_inverse", "benchmark_score": 1009000}
    if rating == points[-1][1]:
        return {**common, "status": "at_cap_score_interval", "score_interval": [1009000, 1010000],
                "benchmark_score": 1009000}
    if rating == 0:
        return {**common, "status": "at_zero_score_interval", "score_interval": [0, 500000]}
    for (s0, r0), (s1, r1) in zip(points, points[1:]):
        if r0 <= rating <= r1:
            return {**common, "status": "unique_continuous_inverse",
                    "score": float(s0 + (rating-r0)*(s1-s0)/(r1-r0))}
    raise ValueError("Rating outside supported intervals")


def grade_thresholds(groups, constant, minimum):
    eligible = [g for g in groups if g["players"] >= minimum]
    thresholds = []
    border_scores = {"ss": 1000000, "ssp": 1005000, "sss": 1007500, "sssp": 1009000}
    for target in border_scores:
        values = [core.successes(g, target)/g["players"] for g in eligible]
        fit = core.pava(values, [g["players"] for g in eligible])
        cross = core.crossing([g["rating"] for g in eligible], fit, .5)
        reference = float(score_to_rating(border_scores[target], constant))
        thresholds.append({"grade": core.GRADE_LABEL[target], "border_score": border_scores[target],
                           "formula_rating_position": reference, "observed_R50": cross["estimate"],
                           "observed_bracket_low": cross["bracket_low"], "observed_bracket_high": cross["bracket_high"],
                           "observed_minus_formula_rating": cross["estimate"]-reference if cross["estimate"] is not None else None,
                           "status": cross["status"]})
    return thresholds


def compare_groups(groups, constant, minimum):
    result = []
    for group in groups:
        reference = inverse_rating(group["label"], constant)
        mean = group["average_score"]
        baseline = reference["score"] if reference["score"] is not None else reference["benchmark_score"]
        result.append({"best_average_rating": group["label"], "players": group["players"],
                       "eligible": group["players"] >= minimum and mean is not None,
                       "actual_mean_score": mean, "reference_status": reference["status"],
                       "formula_inverse_score": reference["score"],
                       "post_cap_sssplus_benchmark": reference["benchmark_score"],
                       "comparison_score": baseline,
                       "mean_minus_reference_score": mean-baseline if mean is not None and baseline is not None else None,
                       "source_locator": group["source_locator"]})
    return result


def representative_rows(rows, constant):
    # The SS+ and SSS formula positions move with each chart's constant.
    targets = (float(dec(constant)+dec('1.5')), float(dec(constant)+2))
    unique = [r for r in rows if r['formula_inverse_score'] is not None]
    selected = []
    for target in targets:
        if unique:
            row = min(unique, key=lambda r: abs(float(r['best_average_rating'])-target))
            if row not in selected:
                selected.append(row)
    return selected


def conclusions(rows, thresholds, constant, view_min, view_max, minimum, confirmed=False):
    shown = [r for r in rows if r["eligible"] and view_min <= float(r["best_average_rating"]) <= view_max]
    unique = [r for r in shown if r["reference_status"] == "unique_continuous_inverse"]
    negative = [r for r in unique if r["mean_minus_reference_score"] < 0]
    lines = []
    if unique:
        lines.append(f"顯示範圍內、封頂前的 {len(unique)} 個合格分組中，{len(negative)} 組平均分低於公式參考線。")
    for row in representative_rows(shown, constant):
        lines.append(f"BEST 枠平均 {row['best_average_rating']}：公式對照 {row['formula_inverse_score']:,.0f} 分，實際平均 {row['actual_mean_score']:,.0f} 分，差 {row['mean_minus_reference_score']:+,.0f} 分。")
    by_grade = {t["grade"]: t for t in thresholds}
    if all(by_grade[g]["observed_R50"] is not None for g in ("SSS", "SSS+")):
        a, b = by_grade["SSS"], by_grade["SSS+"]
        observed_gap, formula_gap = b["observed_R50"]-a["observed_R50"], b["formula_rating_position"]-a["formula_rating_position"]
        lines.append(f"SSS／SSS+ 的 50% 達成門檻約為 {a['observed_R50']:.2f}／{b['observed_R50']:.2f}（分組達成率加權單調擬合後插值）。")
        lines.append(f"SSS→SSS+ 的實測 50% 門檻間距約 {observed_gap:.2f}，公式參考間距 {formula_gap:.2f}；兩者差 {observed_gap-formula_gap:+.2f}。")
    capped = [r for r in shown if r["post_cap_sssplus_benchmark"] is not None]
    nonnegative = [r for r in capped if r["mean_minus_reference_score"] >= 0]
    if nonnegative:
        first = nonnegative[0]
        lines.append(f"顯示分組中，封頂後平均分首次達到 SSS+ 門檻的是 {first['best_average_rating']} 組：{first['actual_mean_score']:,.0f} 分。")
    confirmation = '已確認' if confirmed else '來源尚未確認'
    lines.append(f"本圖沿用快照定數 {constant}（{confirmation}），橫軸為 BEST 枠平均分組標籤；每組至少 {minimum} 人。公式線是能力對照假設，不是統計模型預測或虛高／虛低判定。")
    lines.append(f"單曲 Rating 在 {dec(constant)+dec('2.15')} 封頂；更高能力組僅比較 SSS+ 的 1,009,000 分門檻，公式無法唯一推算其分數。")
    return lines


def make_figure(path, rows, constant, title, difficulty, view_min, view_max,
                provider='Chunirec', axis_label='BEST 枠平均'):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, MultipleLocator
    plt.rcParams["font.family"] = ["Microsoft JhengHei", "Microsoft YaHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    shown = [r for r in rows if r["eligible"] and view_min <= float(r["best_average_rating"]) <= view_max]
    if not shown:
        raise ValueError("no adequately populated buckets in the requested view")
    xs, actual = [float(r["best_average_rating"]) for r in shown], [r["actual_mean_score"] for r in shown]
    cap = float(dec(constant)+dec("2.15"))
    fig, (ax, residual) = plt.subplots(2, 1, figsize=(11, 8), sharex=True,
                                       gridspec_kw={"height_ratios": [2.3, 1]}, layout="constrained")
    fig.suptitle(f"{title}／{difficulty}：公式分數參考線與實際平均分", fontsize=17, fontweight="bold")
    ax.set_title(f"沿用定數 {constant}；橫軸以 {axis_label}分組標籤作能力參考", fontsize=11)
    unique_end = min(view_max, cap)
    if view_min < unique_end:
        curve_x = [view_min + (unique_end-view_min)*i/300 for i in range(301)]
        curve_y = [inverse_rating(x, constant)["score"] for x in curve_x]
        # At the cap the inverse is an interval; use its lower endpoint to end the solid line.
        curve_y[-1] = 1009000 if math.isclose(unique_end, cap) else curve_y[-1]
        ax.plot(curve_x, curve_y, color="#D08024", linewidth=2.4, label="公式反推分數（封頂前）")
    ax.plot(xs, actual, color="#2568B0", linewidth=2.4, marker="o", markersize=5, label=f"{provider} 組內平均分")
    for axis in (ax, residual):
        if cap < view_max:
            axis.axvspan(max(view_min, cap), view_max+.04, color="#ECEFF2", alpha=.85, zorder=0)
        axis.axvline(cap, color="#999999", linestyle=":", linewidth=1)
        axis.grid(axis="y", alpha=.22)
        axis.spines[["top", "right"]].set_visible(False)
    if cap < view_max:
        ax.plot([max(view_min, cap), view_max+.025], [1009000, 1009000], color="#D08024",
                linewidth=2, linestyle="--", label="封頂後：SSS+ 門檻對照")
        ax.text(cap+.02, 1010800, "單曲 Rating 封頂\n此區僅比較 SSS+ 門檻", fontsize=10,
                color="#555555", va="top")
    for score, grade in ((1000000, "SS"), (1005000, "SS+"), (1007500, "SSS"), (1009000, "SSS+")):
        ax.axhline(score, color="#AAAAAA", linewidth=.65, linestyle=":", zorder=0)
        ax.text(view_max+.018, score, grade, color="#666666", fontsize=9, va="center")
    ax.set_ylabel("分數", fontsize=11)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_ylim(min(actual)-1500, 1011800)
    ax.legend(loc="lower right", fontsize=10, framealpha=.96)
    for row in representative_rows(shown, constant):
        label = row['best_average_rating']
        x, y = float(label), row['actual_mean_score']
        ax.annotate(f"{label}：{y:,.0f}", xy=(x, y), xytext=(x-.17, y-3000), fontsize=9,
                    color="#2568B0", arrowprops={"arrowstyle": "-", "color": "#2568B0"})
    for row in shown:
        x, difference = float(row["best_average_rating"]), row["mean_minus_reference_score"]
        bar = residual.bar(x, difference, width=.066, color="#B85C45" if difference < 0 else "#2F8672",
                           edgecolor="#666666" if row["post_cap_sssplus_benchmark"] else "none",
                           hatch="//" if row["post_cap_sssplus_benchmark"] else None, linewidth=.6)
        residual.text(x, difference+(-400 if difference < 0 else 400), f"{difference:+,.0f}", ha="center",
                      va="top" if difference < 0 else "bottom", fontsize=8)
    residual.axhline(0, color="#777777", linewidth=.8)
    residual.set_title("平均分 − 參考分數；斜線柱為封頂後與 SSS+ 門檻的差值", fontsize=10)
    residual.set_ylabel("分數差", fontsize=11)
    residual.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    residual.set_ylim(min(r["mean_minus_reference_score"] for r in shown)-2600,
                      max(2000, max(r["mean_minus_reference_score"] for r in shown)+1500))
    residual.set_xlabel(f"{axis_label}分組（不是玩家總 Rating）", fontsize=11)
    residual.xaxis.set_major_locator(MultipleLocator(.1))
    residual.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.2f}"))
    residual.set_xlim(view_min-.04, view_max+.08)
    fig.savefig(path, dpi=170)
    plt.close(fig)


def run(args):
    manifest, stats, groups, source_hashes = core.load_input(args.input.resolve())
    if args.min_players < 1 or args.view_min >= args.view_max:
        raise ValueError("invalid view range or minimum sample count")
    output = args.output.resolve() if args.output else args.input.resolve() / ("rating_reference_"+datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d_%H%M%S"))
    output.mkdir(parents=True, exist_ok=False)
    if args.wiki_file:
        html = args.wiki_file.read_bytes()
        wiki_mode = "cached"
    else:
        with requests.get(WIKI_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; ChuniNumericProbe/1.0)"}, timeout=(8, 25)) as response:
            response.raise_for_status()
            html = response.content
        wiki_mode = "one_read_only_HTTP_request"
    (output / "wiki_rating_source.html").write_bytes(html)
    formula = extract_formula(html)
    core.save_json(output / "formula_source.json", formula)
    constant = manifest["chart"]["const"]
    rows = compare_groups(groups, constant, args.min_players)
    thresholds = grade_thresholds(groups, constant, args.min_players)
    core.write_csv(output / "reference_comparison.csv", rows)
    core.write_csv(output / "grade_reference_comparison.csv", thresholds)
    lines = conclusions(rows, thresholds, constant, args.view_min, args.view_max, args.min_players,
                        manifest['chart']['const_confirmed'])
    conclusion = "\n".join("- "+line for line in lines)
    source_link = "[Wiki 分數／Rating 公式]("+formula["source_url"]+")"
    (output / "CONCLUSION.md").write_text(conclusion+"\n\n公式來源："+source_link+"\n", encoding="utf-8")
    result = {"version": VERSION, "input_sha256": source_hashes, "analyzer_sha256": core.sha256(Path(__file__)),
              "title": manifest["title"], "difficulty": manifest["difficulty"], "constant": constant,
              "constant_confirmed": manifest["chart"]["const_confirmed"], "formula": formula,
              "wiki_mode": wiki_mode, "wiki_html_sha256": core.sha256(output / "wiki_rating_source.html"),
              "min_players": args.min_players, "view_range": [args.view_min, args.view_max],
              "llm_used": False, "tags_generated": False,
              "axis_assumption": "use published BEST-average bucket label as the target single-chart Rating for a formula reference only",
              "rows": rows, "grade_thresholds": thresholds, "conclusions": lines}
    core.save_json(output / "comparison.json", result)
    make_figure(output / "rating_reference.png", rows, constant, manifest["title"], manifest["difficulty"], args.view_min, args.view_max)
    unchanged = all(core.sha256(args.input / name) == checksum for name, checksum in source_hashes.items())
    inverse_checks = 0
    for row in rows:
        if row["formula_inverse_score"] is not None:
            rating = score_to_rating(row["formula_inverse_score"], constant)
            if abs(rating-dec(row["best_average_rating"])) > dec("0.000000001"):
                raise ValueError("inverse Rating roundtrip failed")
            inverse_checks += 1
    if not unchanged:
        raise ValueError("original snapshot changed")
    core.save_json(output / "validation.json", {"status": "passed", "source_files_unchanged": unchanged,
                    "unique_inverse_roundtrips": inverse_checks,
                    "output_sha256": {p.name: core.sha256(p) for p in output.iterdir() if p.is_file()}})
    print(json.dumps({"status": "complete", "output": str(output), "conclusions": lines}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=core.DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--wiki-file", type=Path)
    parser.add_argument("--min-players", type=int, default=100)
    parser.add_argument("--view-min", type=float, default=16.8)
    parser.add_argument("--view-max", type=float, default=17.7)
    run(parser.parse_args())
