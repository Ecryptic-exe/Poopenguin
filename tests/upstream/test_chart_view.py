from io import BytesIO

import httpx
import pytest
from PIL import Image

from chuni_penguin.database import Chart, SdvxinChartView
from chuni_penguin.services.chart_view import fetch_chart_view


def png(color, size=(8, 8)):
    output = BytesIO()
    with Image.new("RGBA", size, color) as image:
        image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("difficulty", "folder", "suffix"),
    [("MAS", "13", "mst"), ("ULT", "ult", "ult"), ("WE", "end", "end2")],
)
async def test_chart_layers_mirror_and_special_difficulty_fallback(
    difficulty, folder, suffix
):
    requests = []

    def respond(request):
        path = request.url.path
        requests.append(path)
        if folder != "13" and path == f"/chunithm/{folder}/bg/130001bg.png":
            return httpx.Response(404)
        if "/obj/" in path:
            return httpx.Response(200, content=png((255, 0, 0, 255)))
        return httpx.Response(200, content=png((0, 0, 0, 0)))

    chart = Chart(
        difficulty=difficulty,
        sdvxin_chart_view=SdvxinChartView(
            id="130001", difficulty=difficulty, end_index="2"
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        output = await fetch_chart_view(client, chart, mirror=True)
    try:
        with Image.open(output) as image:
            assert image.size == (8, 8)
            assert image.mode == "RGB"
            assert image.getpixel((4, 4))[0] > 240
        assert f"/chunithm/{folder}/obj/mirror130001{suffix}.png" in requests
        if folder != "13":
            assert "/chunithm/13/bg/130001bg.png" in requests
    finally:
        output.close()
