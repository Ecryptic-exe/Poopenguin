import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from chuni_penguin.services import distribution_source, phase1_charts
from chuni_penguin.services.distribution_source import (
    DistributionUnavailable,
    source_id,
    source_url,
    validate_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def source():
    entry = json.loads(
        (ROOT / "snapshots/statistics/index.json").read_text(encoding="utf-8")
    )[0]
    path = ROOT / "snapshots/statistics" / entry["folder"]
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    chart = SimpleNamespace(
        song_id=entry["song_id"],
        difficulty="MAS",
        song=SimpleNamespace(title=manifest["title"], chunirec_id=entry["chunirec_id"]),
    )
    return SimpleNamespace(entry=entry, path=path, chart=chart)


class ChartError(Exception):
    pass


def service_with_worker(tmp_path, source, *, cached=None):
    service = phase1_charts.Phase1Charts(tmp_path, allow_network=True)
    image = tmp_path / "data/cache/statistics/chart.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"real bytes delivered by the mocked C worker")
    service.runtime = SimpleNamespace(
        assert_unchanged=Mock(),
        catalog=SimpleNamespace(
            Song=lambda song_id, title, difficulties: SimpleNamespace(
                song_id=song_id, title=title, difficulties=difficulties
            )
        ),
        module=lambda name: SimpleNamespace(ChartError=ChartError),
    )
    service.worker = SimpleNamespace(
        read_snapshot=Mock(return_value=cached),
        get_chart=AsyncMock(return_value={"image": image}),
        close=AsyncMock(),
    )
    return service


def test_source_identity_uses_native_id_not_a_title_search(source):
    assert source_id(source.chart) == source.entry["chunirec_id"]
    assert source_url("Same title / 違う曲", source.entry["chunirec_id"]).endswith(
        "/" + source.entry["chunirec_id"]
    )
    source.chart.difficulty = "WE"
    with pytest.raises(DistributionUnavailable, match="WORLD"):
        source_id(source.chart)
    source.chart.difficulty = "MAS"
    source.chart.song.chunirec_id = None
    with pytest.raises(DistributionUnavailable, match="可信"):
        source_id(source.chart)


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "Other title"),
        ("difficulty", "EXPERT"),
        ("native_id", "0000000000000000"),
    ],
)
def test_source_snapshot_rejects_wrong_song_difficulty_or_native_id(
    source, field, value
):
    values = dict(
        title=source.chart.song.title,
        difficulty="MASTER",
        native_id=source.entry["chunirec_id"],
    )
    values[field] = value
    with pytest.raises(DistributionUnavailable):
        validate_snapshot(source.path, **values)


@pytest.mark.asyncio
async def test_non_bundled_chart_fetches_selected_source_then_c_full_distribution(
    tmp_path, source, monkeypatch
):
    service = service_with_worker(tmp_path, source)
    assert service.statistic_for(source.chart) is None
    collector = AsyncMock(return_value=source.path)
    monkeypatch.setattr(phase1_charts, "collect_source", collector)
    try:
        origin, image = await service.analysis(source.chart)
        collector.assert_awaited_once_with(
            tmp_path,
            title=source.chart.song.title,
            difficulty="MASTER",
            native_id=source.entry["chunirec_id"],
        )
        assert origin["source_kind"] == "live"
        assert origin["players"] > 0 and origin["observed_at"]
        assert image == b"real bytes delivered by the mocked C worker"
        args, kwargs = service.worker.get_chart.call_args
        assert args[0].song_id == str(source.chart.song_id)
        assert args[1:] == ("MASTER", source.path)
        assert kwargs == {"detailed": True}
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_fresh_verified_snapshot_is_reused_without_network(
    tmp_path, source, monkeypatch
):
    service = service_with_worker(tmp_path, source, cached={"path": source.path})
    collector = AsyncMock(
        side_effect=AssertionError("Fresh cached statistics must not refetch")
    )
    monkeypatch.setattr(phase1_charts, "collect_source", collector)
    try:
        origin, _ = await service.analysis(source.chart)
        assert origin["source_kind"] == "cache"
        collector.assert_not_awaited()
        assert service.worker.get_chart.call_args.args[2] is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_mismatched_cached_native_id_is_replaced_before_rendering(
    tmp_path, source, monkeypatch
):
    wrong = tmp_path / "wrong_source"
    shutil.copytree(source.path, wrong)
    manifest = json.loads((wrong / "manifest.json").read_text(encoding="utf-8"))
    manifest["source_url"] = "https://db.chunirec.net/music/wrong/0000000000000000"
    (wrong / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    service = service_with_worker(tmp_path, source, cached={"path": wrong})
    collector = AsyncMock(return_value=source.path)
    monkeypatch.setattr(phase1_charts, "collect_source", collector)
    try:
        await service.analysis(source.chart)
        collector.assert_awaited_once()
        assert service.worker.get_chart.call_args.args[2] == source.path
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_same_chart_requests_share_work_and_waiter_cancel_does_not_stop_other(
    tmp_path, source
):
    service = phase1_charts.Phase1Charts(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def produce(*args):
        entered.set()
        await release.wait()
        return {"players": 100}, b"image"

    service._analyze_one = AsyncMock(side_effect=produce)
    first = asyncio.create_task(service.analysis(source.chart))
    await entered.wait()
    second = asyncio.create_task(service.analysis(source.chart))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not second.done()
    release.set()
    assert await second == ({"players": 100}, b"image")
    service._analyze_one.assert_awaited_once()
    await service.close()


@pytest.mark.asyncio
async def test_bundled_fallback_remains_labelled_on_subsequent_cache_hit(
    tmp_path, source, monkeypatch
):
    service = service_with_worker(tmp_path, source)
    service.statistics = [source.entry]
    service._bundled_snapshot = Mock(return_value=source.path)
    collector = AsyncMock(
        side_effect=DistributionUnavailable("temporarily unavailable")
    )
    monkeypatch.setattr(phase1_charts, "collect_source", collector)
    try:
        origin, _ = await service.analysis(source.chart)
        assert origin["source_kind"] == "bundled"
        assert origin["observed_at"].startswith("2026-10-04")
        service.worker.read_snapshot.return_value = {"path": source.path}
        origin, _ = await service.analysis(source.chart)
        assert origin["source_kind"] == "bundled"
        assert collector.await_count == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancelling_source_collector_kills_and_reaps_its_subprocess(
    tmp_path, monkeypatch
):
    entered = asyncio.Event()
    calls = 0
    process = SimpleNamespace(returncode=None, kill=Mock())

    async def communicate():
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        return b"", b""

    process.communicate = AsyncMock(side_effect=communicate)
    monkeypatch.setattr(
        distribution_source.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    task = asyncio.create_task(
        distribution_source.collect_source(
            tmp_path,
            title="Selected song",
            difficulty="MASTER",
            native_id="0123456789abcdef",
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process.kill.assert_called_once()
    assert calls == 2
