import asyncio
from contextlib import closing
from io import BytesIO

import httpx
from discord.ext import commands
from PIL import Image

from chuni_penguin.database import Chart


def compose_chart_view(bg: bytes, data: bytes, bar: bytes):
    with (
        Image.open(BytesIO(bg)) as bg_img,
        Image.open(BytesIO(data)) as data_img,
        Image.open(BytesIO(bar)) as bar_img,
    ):
        if bg_img.mode != "RGBA":
            bg_img_new = bg_img.convert("RGBA")
            bg_img.close()
            bg_img = bg_img_new

        if data_img.mode != "RGBA":
            data_img_new = data_img.convert("RGBA")
            data_img.close()
            data_img = data_img_new

        if bar_img.mode != "RGBA":
            bar_img_new = bar_img.convert("RGBA")
            bar_img.close()
            bar_img = bar_img_new

        with (
            closing(Image.new("RGBA", bg_img.size, (0, 0, 0, 255))) as background,
            closing(bg_img),
        ):
            result = Image.alpha_composite(background, bg_img)

        if data_img.size != bg_img.size:
            container = Image.new("RGBA", bg_img.size, (0, 0, 0, 0))
            container.paste(data_img, (0, 0), data_img)
            data_img.close()
            data_img = container

        with closing(result), closing(data_img):
            result = Image.alpha_composite(result, data_img)

        if bar_img.size != bg_img.size:
            container = Image.new("RGBA", bg_img.size, (0, 0, 0, 0))
            container.paste(bar_img, (0, 0), bar_img)
            bar_img.close()
            bar_img = container

        with closing(result), closing(bar_img):
            result = Image.alpha_composite(result, bar_img)

        output = BytesIO()

        with closing(result):
            result_rgb = result.convert("RGB")

            result_rgb.save(output, format="JPEG", quality=92)
            result_rgb.close()

    output.seek(0)

    return output


async def fetch_chart_view(
    client: httpx.AsyncClient, chart: Chart, *, mirror: bool = False
) -> BytesIO:
    """Fetch and compose the same sdvx.in layers used by /chart."""
    if chart.sdvxin_chart_view is None:
        msg = "Chart view is not available yet."
        raise NotImplementedError(msg)

    view = chart.sdvxin_chart_view
    chart_id = view.id
    data_key = "mirror" if mirror else "data"
    standard_base = f"https://sdvx.in/chunithm/{chart_id[:2]}"
    base = standard_base
    suffix = chart.difficulty.lower() if chart.difficulty != "MAS" else "mst"
    if chart.difficulty == "WE":
        base = "https://sdvx.in/chunithm/end"
        suffix = f"end{view.end_index}"
    elif chart.difficulty == "ULT":
        base = "https://sdvx.in/chunithm/ult"
        suffix = "ult"

    async def layer(kind: str):
        response = await client.get(f"{base}/bg/{chart_id}{kind}.png")
        if response.is_error and chart.difficulty in ("ULT", "WE"):
            response = await client.get(f"{standard_base}/bg/{chart_id}{kind}.png")
        return response

    bg, data, bar = await asyncio.gather(
        layer("bg"),
        client.get(f"{base}/obj/{data_key}{chart_id}{suffix}.png"),
        layer("bar"),
    )
    if any(response.is_error for response in (bg, data, bar)):
        msg = "Failed to fetch chart view. Please try again later."
        raise commands.CommandError(msg)

    return await asyncio.to_thread(
        compose_chart_view, bg.content, data.content, bar.content
    )
