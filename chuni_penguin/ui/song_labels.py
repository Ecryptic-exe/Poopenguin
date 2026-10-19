"""Player-facing labels for distinct catalog versions; IDs are not descriptions."""


def availability_label(song):
    if song.removed:
        return "已下架"
    return "可遊玩" if song.available else "國際版未開放"


def variant_label(song, charts=()):
    if song.genre != "WORLD'S END":
        return "一般譜面"
    levels = list(
        dict.fromkeys(chart.level for chart in charts if chart.difficulty == "WE")
    )
    return "WORLD'S END" + (" · " + "／".join(levels) if levels else "")


def record_label(song, charts=()):
    return f"{variant_label(song, charts)} · 收錄版本 {song.version} · {availability_label(song)}"
