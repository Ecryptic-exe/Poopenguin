import asyncio
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from chuni_penguin.services.jackets import JacketCache, public_art_url


def artwork():
    output = io.BytesIO()
    Image.new("RGB", (128, 128), "red").save(output, format="PNG")
    return output.getvalue()


def cache(tmp_path, handler):
    seeds = tmp_path / "songs.json"
    seeds.write_text(
        json.dumps(
            [
                {
                    "id": 123,
                    "jackets": [
                        "https://dp4p6x0xfi5o9.cloudfront.net/chunithm/img/cover/fallback.png"
                    ],
                }
            ]
        )
    )
    return JacketCache(
        directory=tmp_path / "jackets",
        transport=httpx.MockTransport(handler),
        seeds=seeds,
    )


@pytest.mark.asyncio
async def test_public_artwork_download_fallback_atomic_cache_and_reuse(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        assert not request.headers.get("authorization") and not request.headers.get(
            "cookie"
        )
        if request.url.host == "chunithm-net-eng.com":
            return httpx.Response(404)
        return httpx.Response(200, content=artwork())

    service = cache(tmp_path, handler)
    row = SimpleNamespace(
        song=SimpleNamespace(
            id=123, jacket_url="https://chunithm-net-eng.com/mobile/img/missing.jpg"
        )
    )
    first, second = await asyncio.gather(
        service.ensure([row, row]), service.ensure([row])
    )
    assert len(requests) == 2
    assert first["downloaded"] + second["downloaded"] == 1
    assert first["cached"] + second["cached"] == 1
    assert (await service.ensure([row]))["cached"] == 1 and len(requests) == 2
    with Image.open(tmp_path / "jackets/123.webp") as image:
        assert image.format == "WEBP" and image.getpixel((50, 50))[0] > 200
    assert not list((tmp_path / "jackets").glob("*.tmp"))


@pytest.mark.asyncio
async def test_untrusted_url_redirect_and_bad_image_fail_without_cached_garbage(
    tmp_path,
):
    for url in (
        "http://chunithm-net-eng.com/a.jpg",
        "https://localhost/a.jpg",
        "https://127.0.0.1/a.jpg",
        "https://chunithm-net-eng.com/a.jpg?token=secret",
        "https://user:secret@chunithm-net-eng.com/a.jpg",
        "https://chunithm-net-eng.com:8443/a.jpg",
    ):
        assert not public_art_url(url)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "chunithm-net-eng.com":
            return httpx.Response(
                302, headers={"location": "https://localhost/private"}
            )
        return httpx.Response(200, content=b"invalid image")

    service = cache(tmp_path, handler)
    row = SimpleNamespace(
        song=SimpleNamespace(id=123, jacket_url="https://chunithm-net-eng.com/a.jpg")
    )
    assert (await service.ensure([row]))["missing"] == 1
    assert (await service.ensure([row]))["missing"] == 1
    assert len(requests) == 2 and not (tmp_path / "jackets/123.webp").exists()


@pytest.mark.asyncio
async def test_cancelled_request_does_not_cancel_another_waiting_for_same_art(tmp_path):
    started = asyncio.Event()
    requests = []

    async def handler(request):
        requests.append(request)
        if len(requests) == 1:
            started.set()
            await asyncio.Event().wait()
        return httpx.Response(200, content=artwork())

    service = cache(tmp_path, handler)
    row = SimpleNamespace(
        song=SimpleNamespace(id=123, jacket_url="https://chunithm-net-eng.com/a.jpg")
    )
    first = asyncio.create_task(service.ensure([row]))
    await started.wait()
    second = asyncio.create_task(service.ensure([row]))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert (await asyncio.wait_for(second, 2))["downloaded"] == 1
    assert not service._locks
