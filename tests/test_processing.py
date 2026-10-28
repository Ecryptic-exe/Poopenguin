import asyncio
from types import SimpleNamespace

import discord
import pytest

from chuni_penguin.ui.processing import (
    processing_embed,
    processing_response,
    run_with_processing,
)


@pytest.mark.asyncio
async def test_nested_panel_work_uses_one_processing_edit():
    from types import SimpleNamespace

    payloads = []

    async def respond(**kwargs):
        payloads.append(kwargs)

    ctx = SimpleNamespace(respond_or_edit=respond)
    async with processing_response(ctx, "搜尋", delay=0):
        async with processing_response(ctx, "載入", delay=0):
            await asyncio.sleep(0.01)
            await ctx.respond_or_edit(content="完成")
    assert len(payloads) == 2 and payloads[-1]["content"] == "完成"
    assert ctx.respond_or_edit is respond


def pending_background_tasks():
    current = asyncio.current_task()
    return {task for task in asyncio.all_tasks() if task is not current}


def test_processing_embed_is_short_and_contains_only_the_label_and_status():
    embed = processing_embed("  歌曲資料  ", "chinese")

    assert isinstance(embed, discord.Embed)
    assert embed.description == "⏳ 歌曲資料處理中…"
    assert not embed.fields
    assert "%" not in embed.description
    assert "秒" not in embed.description


@pytest.mark.asyncio
async def test_fast_work_does_not_show_processing():
    started = asyncio.Event()
    shown = []

    async def work():
        started.set()
        return "done"

    async def show():
        shown.append(True)

    result = await run_with_processing(work(), show, delay=1)

    assert started.is_set()
    assert result == "done"
    assert shown == []


@pytest.mark.asyncio
async def test_slow_work_shows_once_and_finishes_before_returning():
    work_started = asyncio.Event()
    release_work = asyncio.Event()
    show_finished = asyncio.Event()
    events = []

    async def work():
        work_started.set()
        events.append("work-start")
        await release_work.wait()
        events.append("work-finish")
        return "done"

    async def show():
        events.append("show-start")
        await asyncio.sleep(0)
        events.append("show-finish")
        show_finished.set()

    runner = asyncio.create_task(run_with_processing(work(), show, delay=0))
    await show_finished.wait()
    assert work_started.is_set()
    assert not runner.done()

    release_work.set()
    assert await runner == "done"
    assert events == ["work-start", "show-start", "show-finish", "work-finish"]


@pytest.mark.asyncio
async def test_work_exception_is_preserved_without_showing_progress():
    failure = ValueError("work failed")
    shown = []

    async def work():
        raise failure

    async def show():
        shown.append(True)

    with pytest.raises(ValueError) as raised:
        await run_with_processing(work(), show, delay=1)

    assert raised.value is failure
    assert shown == []


@pytest.mark.asyncio
async def test_show_error_is_raised_only_after_successful_work_finishes():
    work_started = asyncio.Event()
    release_work = asyncio.Event()
    work_finished = asyncio.Event()
    show_started = asyncio.Event()
    show_failure = RuntimeError("progress edit failed")

    async def work():
        work_started.set()
        try:
            await release_work.wait()
            return "done"
        finally:
            work_finished.set()

    async def show():
        show_started.set()
        raise show_failure

    runner = asyncio.create_task(run_with_processing(work(), show, delay=0))
    await show_started.wait()
    assert work_started.is_set()
    assert not runner.done()

    release_work.set()
    with pytest.raises(RuntimeError) as raised:
        await runner

    assert raised.value is show_failure
    assert work_finished.is_set()


@pytest.mark.asyncio
async def test_work_error_takes_precedence_over_show_error():
    release_work = asyncio.Event()
    show_started = asyncio.Event()
    work_failure = ValueError("work failed")
    show_failure = RuntimeError("progress edit failed")

    async def work():
        await release_work.wait()
        raise work_failure

    async def show():
        show_started.set()
        raise show_failure

    runner = asyncio.create_task(run_with_processing(work(), show, delay=0))
    await show_started.wait()
    release_work.set()

    with pytest.raises(ValueError) as raised:
        await runner

    assert raised.value is work_failure


@pytest.mark.asyncio
async def test_cancellation_before_progress_cancels_and_awaits_work_and_timer():
    work_started = asyncio.Event()
    work_cleaned = asyncio.Event()
    shown = []
    previous_tasks = pending_background_tasks()

    async def work():
        work_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            work_cleaned.set()

    async def show():
        shown.append(True)

    runner = asyncio.create_task(run_with_processing(work(), show, delay=60))
    await work_started.wait()
    runner.cancel()

    with pytest.raises(asyncio.CancelledError):
        await runner

    assert work_cleaned.is_set()
    assert shown == []
    assert pending_background_tasks() == previous_tasks


@pytest.mark.asyncio
async def test_cancellation_while_showing_cancels_and_awaits_show_and_work():
    work_started = asyncio.Event()
    work_cleaned = asyncio.Event()
    show_started = asyncio.Event()
    show_cleaned = asyncio.Event()
    previous_tasks = pending_background_tasks()

    async def work():
        work_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            work_cleaned.set()

    async def show():
        show_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            show_cleaned.set()

    runner = asyncio.create_task(run_with_processing(work(), show, delay=0))
    await show_started.wait()
    assert work_started.is_set()
    runner.cancel()

    with pytest.raises(asyncio.CancelledError):
        await runner

    assert show_cleaned.is_set()
    assert work_cleaned.is_set()
    assert pending_background_tasks() == previous_tasks


@pytest.mark.asyncio
async def test_processing_response_fast_exit_skips_progress_and_restores_responder():
    calls = []

    async def respond(content=None, **kwargs):
        calls.append((content, kwargs))
        return "response"

    ctx = SimpleNamespace(respond_or_edit=respond)
    original = ctx.respond_or_edit

    async with processing_response(ctx, "搜尋歌曲", delay=1):
        await asyncio.sleep(0)

    assert calls == []
    assert ctx.respond_or_edit is original


@pytest.mark.asyncio
async def test_processing_response_orders_in_flight_progress_before_final_edit():
    progress_started = asyncio.Event()
    release_progress = asyncio.Event()
    events = []

    async def respond(content=None, **kwargs):
        if kwargs.get("embed") is not None:
            events.append("progress-start")
            progress_started.set()
            await release_progress.wait()
            events.append("progress-finish")
            return "progress"
        events.append("final")
        return "final response"

    ctx = SimpleNamespace(respond_or_edit=respond)
    original = ctx.respond_or_edit

    async def route():
        async with processing_response(ctx, "搜尋歌曲", delay=0):
            await progress_started.wait()
            return await ctx.respond_or_edit("完成")

    runner = asyncio.create_task(route())
    await progress_started.wait()
    await asyncio.sleep(0)
    assert events == ["progress-start"]
    assert not runner.done()

    release_progress.set()
    assert await runner == "final response"
    assert events == ["progress-start", "progress-finish", "final"]
    assert ctx.respond_or_edit is original


@pytest.mark.asyncio
async def test_processing_response_first_prompt_stops_timer_before_work_finishes():
    prompt_sent = asyncio.Event()
    finish_work = asyncio.Event()
    calls = []

    async def respond(content=None, **kwargs):
        calls.append((content, kwargs))
        return "prompt"

    ctx = SimpleNamespace(respond_or_edit=respond)
    original = ctx.respond_or_edit

    async def route():
        async with processing_response(ctx, "搜尋歌曲", delay=0):
            await ctx.respond_or_edit("請選擇歌曲", view="selection")
            prompt_sent.set()
            await finish_work.wait()

    runner = asyncio.create_task(route())
    await prompt_sent.wait()
    assert calls == [("請選擇歌曲", {"view": "selection"})]
    assert not runner.done()

    finish_work.set()
    await runner
    assert calls == [("請選擇歌曲", {"view": "selection"})]
    assert ctx.respond_or_edit is original


@pytest.mark.asyncio
async def test_processing_response_suppresses_progress_error_and_keeps_final_response():
    progress_started = asyncio.Event()
    progress_failure = RuntimeError("temporary progress edit failure")
    calls = []

    async def respond(content=None, **kwargs):
        if kwargs.get("embed") is not None:
            progress_started.set()
            raise progress_failure
        calls.append(content)
        return "final response"

    ctx = SimpleNamespace(respond_or_edit=respond)
    original = ctx.respond_or_edit

    async with processing_response(ctx, "載入資料", delay=0):
        await progress_started.wait()
        result = await ctx.respond_or_edit("完成")

    assert result == "final response"
    assert calls == ["完成"]
    assert ctx.respond_or_edit is original


@pytest.mark.asyncio
async def test_processing_response_preserves_work_error_and_restores_responder():
    failure = ValueError("work failed")

    async def respond(content=None, **kwargs):
        return "response"

    ctx = SimpleNamespace(respond_or_edit=respond)
    original = ctx.respond_or_edit

    with pytest.raises(ValueError) as raised:
        async with processing_response(ctx, "載入資料", delay=60):
            raise failure

    assert raised.value is failure
    assert ctx.respond_or_edit is original


@pytest.mark.asyncio
async def test_processing_response_cancellation_before_delay_restores_responder():
    show_calls = []
    work_started = asyncio.Event()
    previous_tasks = pending_background_tasks()

    async def respond(content=None, **kwargs):
        show_calls.append((content, kwargs))

    ctx = SimpleNamespace(respond_or_edit=respond)
    original = ctx.respond_or_edit

    async def route():
        async with processing_response(ctx, "載入資料", delay=60):
            work_started.set()
            await asyncio.Event().wait()

    runner = asyncio.create_task(route())
    await work_started.wait()
    runner.cancel()

    with pytest.raises(asyncio.CancelledError):
        await runner

    assert show_calls == []
    assert ctx.respond_or_edit is original
    assert pending_background_tasks() == previous_tasks


@pytest.mark.asyncio
async def test_processing_response_waits_for_in_flight_progress_on_cancellation():
    progress_started = asyncio.Event()
    release_progress = asyncio.Event()
    progress_finished = asyncio.Event()
    progress_cancelled = asyncio.Event()
    previous_tasks = pending_background_tasks()

    async def respond(content=None, **kwargs):
        if kwargs.get("embed") is not None:
            progress_started.set()
            try:
                await release_progress.wait()
            except asyncio.CancelledError:
                progress_cancelled.set()
                raise
            progress_finished.set()
        return "response"

    ctx = SimpleNamespace(respond_or_edit=respond)
    original = ctx.respond_or_edit

    async def route():
        async with processing_response(ctx, "載入資料", delay=0):
            await progress_started.wait()
            await asyncio.Event().wait()

    runner = asyncio.create_task(route())
    await progress_started.wait()
    runner.cancel()
    await asyncio.sleep(0)

    assert not runner.done()
    assert not progress_cancelled.is_set()

    release_progress.set()
    with pytest.raises(asyncio.CancelledError):
        await runner

    assert progress_finished.is_set()
    assert not progress_cancelled.is_set()
    assert ctx.respond_or_edit is original
    assert pending_background_tasks() == previous_tasks
