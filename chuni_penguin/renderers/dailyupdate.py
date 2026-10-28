"""Pillow cards for on-demand record changes. Called only through to_thread."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from chuni_penguin.services.dailyupdate import (
    FONT_PATH,
    JACKET_DIR,
    RANKS,
    ChartState,
    Snapshot,
)
from chuni_penguin.utils.localization import t

PAGE_SIZE = 10
WIDTH = 1000
HEADER_HEIGHT = 680
ROW_HEIGHT = 208
INK = "#252932"
MUTED = "#6e7681"
BLUE = "#1685e5"
COLORS = {
    "BAS": "#159b79",
    "ADV": "#ec9800",
    "EXP": "#e95469",
    "MAS": "#ac60d2",
    "ULT": "#333742",
    "WE": "#499cdb",
}


def render_dailyupdate(
    previous: Snapshot,
    current: Snapshot,
    rows: list[tuple[ChartState | None, ChartState]],
    page: int,
    page_count: int,
    *,
    language: str = "english",
    live: bool = False,
    font_path: Path = FONT_PATH,
    jacket_dir: Path = JACKET_DIR,
) -> bytes:
    image = Image.new(
        "RGB", (WIDTH, HEADER_HEIGHT + max(1, len(rows)) * ROW_HEIGHT + 24), "#f7f8fa"
    )
    draw = ImageDraw.Draw(image)
    fonts = {}

    def font(size):
        if size not in fonts:
            fonts[size] = ImageFont.truetype(str(font_path), size)
        return fonts[size]

    def text(x, y, value, size=26, color=INK, *, anchor=None):
        draw.text((x, y), str(value), font=font(size), fill=color, anchor=anchor)

    def fit(value, size, width):
        value = str(value)
        if draw.textlength(value, font=font(size)) <= width:
            return value
        while value and draw.textlength(value + "…", font=font(size)) > width:
            value = value[:-1]
        return value + "…"

    def delta(value, digits=0):
        # Avoid negative zero after subtracting two parsed floating-point values.
        value = round(value, digits)
        return f"{value:+,.{digits}f}"

    def badge(x, y, label, color="#f3deae", width=100, text_color=INK):
        draw.rounded_rectangle((x, y, x + width, y + 34), radius=5, fill=color)
        text(
            x + width / 2, y + 2, fit(label, 20, width - 8), 20, text_color, anchor="mt"
        )

    text(24, 13, t(language, "Updated Records", "成績更新"), 42)
    text(974, 25, f"{page + 1}/{page_count}", 30, anchor="rt")
    text(
        24,
        76,
        f"{previous.captured_at:%Y-%m-%d %H:%M} -> {current.captured_at:%Y-%m-%d %H:%M} GMT+8",
        25,
    )
    text(
        24,
        116,
        t(
            language,
            "Cumulative changes since this window's fixed baseline"
            if live
            else "Saved baseline -> final observation in this window",
            "相對此視窗固定基準的累積變化"
            if live
            else "此視窗的固定基準 -> 最後一份觀測快照",
        ),
        21,
        MUTED,
    )
    text(
        24,
        151,
        t(language, "Captured", "擷取時間")
        + f": {previous.captured_at:%Y-%m-%d %H:%M:%S} -> {current.captured_at:%Y-%m-%d %H:%M:%S} GMT+8",
        20,
        MUTED,
    )

    draw.rounded_rectangle(
        (24, 199, 976, 355), radius=10, fill="#fff0a0", outline="#e9cd45", width=2
    )
    text(46, 215, fit(current.player_name, 40, 890), 40)
    text(46, 289, f"Lv. {current.level:,}", 30)
    text(955, 284, f"RATING  {current.rating:.2f}", 34, anchor="rt")

    draw.rounded_rectangle(
        (24, 376, 976, 655), radius=8, fill="white", outline="#d9dfe5", width=2
    )
    for index, rank in enumerate(RANKS):
        x = 94 + index * 135
        value = current.rank_counts[rank]
        text(x, 386, rank, 23, MUTED, anchor="mt")
        text(x, 426, f"{value:,}", 30, anchor="mt")
        text(
            x,
            470,
            f"({delta(value - previous.rank_counts[rank])})",
            22,
            BLUE,
            anchor="mt",
        )
    draw.line((44, 516, 956, 516), fill="#e1e5e9", width=2)
    for index, lamp in enumerate(("FC", "AJ")):
        x = 102 + index * 150
        value = current.lamp_counts[lamp]
        text(x, 528, lamp, 24, MUTED, anchor="mt")
        text(x, 565, f"{value:,}", 28, anchor="mt")
        text(
            x,
            609,
            f"({delta(value - previous.lamp_counts[lamp])})",
            20,
            BLUE,
            anchor="mt",
        )
    text(365, 528, t(language, "Total Score", "總分"), 22, MUTED)
    text(365, 565, f"{current.total_score:,}", 27)
    text(365, 609, f"({delta(current.total_score - previous.total_score)})", 20, BLUE)
    text(680, 528, "Over Power", 22, MUTED)
    text(
        680,
        565,
        f"{current.over_power:,.2f} ({current.over_power_percentage:.4f}%)",
        21,
    )
    text(
        680,
        609,
        f"({delta(current.over_power - previous.over_power, 2)})  ({delta(current.over_power_percentage - previous.over_power_percentage, 4)}%)",
        20,
        BLUE,
    )

    if not rows:
        text(
            500,
            HEADER_HEIGHT + 65,
            t(language, "Profile changes only", "只有玩家資料變更"),
            30,
            MUTED,
            anchor="mt",
        )
    for index, (old, new) in enumerate(rows):
        y = HEADER_HEIGHT + index * ROW_HEIGHT
        draw.rectangle(
            (24, y, 976, y + ROW_HEIGHT - 12), fill="white", outline="#d9dfe5", width=2
        )
        jacket = None
        try:
            with Image.open(jacket_dir / f"{new.song_id}.webp") as source:
                jacket = ImageOps.fit(source.convert("RGB"), (164, 164))
        except (OSError, ValueError):
            pass
        if jacket is not None:
            image.paste(jacket, (40, y + 16))
            jacket.close()
        else:
            draw.rectangle((40, y + 16, 204, y + 180), fill="#e4e9ef")
            text(122, y + 74, "♪", 48, MUTED, anchor="mt")
        text(228, y + 12, fit(new.title, 29, 565), 29)
        badge(
            812,
            y + 12,
            f"{new.difficulty} {new.level}",
            COLORS.get(new.difficulty, "#ced6df"),
            width=148,
            text_color="white",
        )
        text(237, y + 67, f"{old.score:,}" if old else t(language, "NEW", "新紀錄"), 31)
        text(572, y + 70, "→", 36, MUTED)
        text(940, y + 67, f"{new.score:,}", 33, anchor="rt")
        text(
            940,
            y + 112,
            f"({delta(new.score - (old.score if old else 0))})",
            23,
            BLUE,
            anchor="rt",
        )
        for x, record in ((237, old), (652, new)):
            if record is None:
                continue
            badge(x, y + 152, record.rank, "#f8efba")
            if record.lamp != "NONE":
                badge(x + 104, y + 152, record.lamp, "#ffe4b8", width=75)
            badge(x + 183, y + 152, record.clear_lamp, "#e1e8f3", width=100)
    output = BytesIO()
    try:
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()
    finally:
        image.close()
        output.close()
