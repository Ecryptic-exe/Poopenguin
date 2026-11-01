import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from chuni_penguin.services.tsaibee_statistics import (
    RANKS,
    TsaibeeStatistics,
    parse_statistics,
)
from chuni_penguin.ui.tsaibee_statistics import statistics_embeds


ALL = {"MAS": [[45, 1030, 997096, 2, 148, 245, 362, 595, 946, 126, 196]]}
BANDS = {
    "MAS": {
        "45": [
            [16, 17, 985569, 0, 0, 0, 0, 1, 12],
            [17.7, 14, 1009265, 2, 10, 12, 14, 14, 14],
        ]
    }
}


def test_captured_columns_match_the_supplied_table_header():
    row = parse_statistics(json.dumps(ALL).encode())["MAS", 45]
    assert dict(zip(RANKS, row.counts, strict=True)) == {
        "MAX": 2,
        "SSS+": 148,
        "SSS": 245,
        "SS+": 362,
        "SS": 595,
        "S": 946,
    }
    assert (row.players, row.average, row.aj, row.fc) == (1030, 997096, 126, 196)
    bands = parse_statistics(json.dumps(BANDS).encode(), bands=True)["MAS", 45]
    assert bands[-1].best30 == Decimal("17.7")
    assert bands[-1].players == 14


@pytest.mark.parametrize(
    "change",
    [
        lambda row: row.append(0),
        lambda row: row.__setitem__(3, 2000),
        lambda row: row.__setitem__(10, 100),
        lambda row: row.__setitem__(0, "45"),
    ],
)
def test_malformed_response_is_rejected(change):
    data = json.loads(json.dumps(ALL))
    change(data["MAS"][0])
    with pytest.raises(ValueError):
        parse_statistics(json.dumps(data).encode())


@pytest.mark.asyncio
async def test_post_contract_and_shared_cache_never_use_credentials():
    calls = []

    def respond(request):
        calls.append(request.url.path)
        assert request.method == "POST"
        assert (
            "cookie" not in request.headers and "authorization" not in request.headers
        )
        expected = {} if request.url.path.endswith("lib-stat-song") else {"diff": "MAS"}
        assert json.loads(request.content) == expected
        return httpx.Response(200, json=ALL if not expected else BANDS)

    service = TsaibeeStatistics(transport=httpx.MockTransport(respond))
    result = await service.chart(45, "MAS")
    assert result.overall.players == 1030 and len(result.bands) == 2
    assert not result.source.stale
    await service.chart(45, "MAS")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_http_failure_uses_the_dated_user_capture():
    service = TsaibeeStatistics(
        transport=httpx.MockTransport(lambda request: httpx.Response(403))
    )
    result = await service.chart(45, "MAS")
    assert result.source.saved and result.source.stale
    assert result.source.observed_at == "2026-10-30"
    assert result.overall.aj == 126 and result.overall.fc == 196
    assert len(result.bands) == 18


@pytest.mark.asyncio
async def test_offline_mode_does_not_attempt_an_http_request():
    def forbidden(request):
        raise AssertionError("Offline mode must not attempt a request")

    service = TsaibeeStatistics(transport=httpx.MockTransport(forbidden), offline=True)
    result = await service.chart(45, "MAS")
    assert result.source.saved and result.overall.players == 1030


@pytest.mark.asyncio
async def test_failed_refresh_preserves_the_last_good_sample():
    fail = False

    def respond(request):
        if fail:
            return httpx.Response(503)
        return httpx.Response(
            200, json=ALL if request.url.path.endswith("lib-stat-song") else BANDS
        )

    service = TsaibeeStatistics(transport=httpx.MockTransport(respond))
    first = await service.chart(45, "MAS")
    fail = True
    service._expires.clear()
    second = await service.chart(45, "MAS")
    assert second.overall == first.overall and second.source.stale
    assert second.source.observed_at == first.source.observed_at


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["english", "chinese"])
async def test_statistics_cards_use_existing_pagination_and_languages(
    language, monkeypatch
):
    service = TsaibeeStatistics(
        transport=httpx.MockTransport(lambda request: httpx.Response(403))
    )
    monkeypatch.setattr(
        "chuni_penguin.ui.tsaibee_statistics.statistics_for", lambda bot: service
    )
    ctx = SimpleNamespace(bot=SimpleNamespace(), guild=None, language=language)
    monkeypatch.setattr(
        "chuni_penguin.utils.chunithm_i18n.get_chunithm_language", lambda ctx: language
    )
    pages = await statistics_embeds(
        ctx,
        SimpleNamespace(id=45, title="L9"),
        SimpleNamespace(difficulty="MAS", level="13+"),
    )
    assert len(pages) == 4
    text = " ".join(field.name + field.value for field in pages[0][0].fields)
    assert "126" in text and "196" in text and "SSS+" in text
    assert "**S+**" not in text
    assert all(
        len(embed) <= 6000 and all(len(field.value) <= 1024 for field in embed.fields)
        for page in pages
        for embed in page
    )
