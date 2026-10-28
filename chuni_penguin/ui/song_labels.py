# ruff: noqa: RUF001
"""Player-facing labels for distinct catalog versions; IDs are not descriptions."""

from chuni_penguin.utils.chunithm_i18n import ui_text


def availability_label(song, ctx=None):
    if song.removed:
        return ui_text(ctx, "已下架")
    return ui_text(ctx, "可遊玩") if song.available else ui_text(ctx, "國際版未開放")


def variant_label(song, charts=(), ctx=None):
    if song.genre != "WORLD'S END":
        return ui_text(ctx, "一般譜面")
    levels = list(
        dict.fromkeys(chart.level for chart in charts if chart.difficulty == "WE")
    )
    return "WORLD'S END" + (" · " + "／".join(levels) if levels else "")


def record_label(song, charts=(), ctx=None):
    return ui_text(
        ctx,
        "{0} · 收錄版本 {1} · {2}",
        variant_label(song, charts, ctx),
        song.version,
        availability_label(song, ctx),
    )
