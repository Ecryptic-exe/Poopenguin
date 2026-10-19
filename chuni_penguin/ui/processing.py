"""Small helpers for delayed, user-facing processing feedback."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncIterator, TypeVar

import discord

T = TypeVar("T")


def processing_embed(label: str) -> discord.Embed:
    """Build a short Traditional Chinese processing embed for ``label``."""
    label = label.strip()
    description = f"⏳ {label}處理中…" if label else "⏳ 處理中…"
    return discord.Embed(description=description, color=discord.Color.blurple())


async def run_with_processing(
    work: Awaitable[T],
    show: Callable[[], Awaitable[None]],
    *,
    delay: float = 0.6,
) -> T:
    """Run work immediately and show one progress edit only if it runs long.

    The progress callback is awaited before this function returns, so a caller
    can safely edit the same message with the final result afterward. If the
    callback raises, the work is still awaited; its exception takes precedence
    over the callback error. When work succeeds, a callback error is re-raised.
    Cancelling this function cancels and awaits its work and delay tasks.
    """
    work_task = asyncio.ensure_future(work)
    delay_task = asyncio.create_task(asyncio.sleep(delay))

    try:
        await asyncio.wait((work_task, delay_task), return_when=asyncio.FIRST_COMPLETED)

        # Prefer work if it completed at the same time as the delay.
        if work_task.done():
            return await work_task

        show_error: Exception | None = None
        try:
            await show()
        except Exception as error:  # noqa: BLE001 - defer any ordinary edit failure
            show_error = error

        # Await the work even if showing progress failed. A work exception is
        # the primary operation's outcome and must not be hidden by the edit.
        result = await work_task
        if show_error is not None:
            raise show_error
        return result
    finally:
        for task in (work_task, delay_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(work_task, delay_task, return_exceptions=True)


@asynccontextmanager
async def processing_response(
    ctx: Any,
    label: str,
    *,
    delay: float = 0.6,
) -> AsyncIterator[None]:
    """Wrap ``ctx.respond_or_edit`` with a delayed edit on its response.

    Any normal response ends the delay. If progress editing has begun, the
    response waits for that edit to finish before making its own edit. This
    preserves ordering for the final response and interactive prompts. The
    context manager does not run or cancel the caller's work.

    Ordinary exceptions from the progress-only response are suppressed without
    logging so a transient display failure cannot replace the caller's result
    or error. Cancellation is still propagated. The original responder is
    restored when the context exits.
    """
    if getattr(ctx, "_processing_response_active", False):
        yield
        return
    original = ctx.respond_or_edit
    had_override = "respond_or_edit" in vars(ctx)
    previous_original = getattr(ctx, "_processing_response_original", None)
    ctx._processing_response_original = original
    ctx._processing_response_active = True
    progress_started = False
    progress_cancelled_before_start = False
    progress_task: asyncio.Task[None] | None = None

    async def show_after_delay() -> None:
        nonlocal progress_started
        await asyncio.sleep(delay)
        progress_started = True
        with suppress(Exception):
            await original(content=None, embed=processing_embed(label), view=None)

    async def finish_progress() -> None:
        nonlocal progress_cancelled_before_start
        if progress_task is None:
            return

        cancel_pending = not progress_started and not progress_task.done()
        if cancel_pending:
            progress_cancelled_before_start = True
            progress_task.cancel()

        current = asyncio.current_task()
        cancellation_count = current.cancelling() if current is not None else 0
        interrupted = False
        while not progress_task.done():
            try:
                await asyncio.shield(progress_task)
            except asyncio.CancelledError:
                if current is not None and current.cancelling() > cancellation_count:
                    interrupted = True
                    while current.cancelling() > cancellation_count:
                        current.uncancel()
                    continue
                if cancel_pending:
                    continue
                raise

        try:
            await progress_task
        except asyncio.CancelledError:
            if not progress_cancelled_before_start:
                raise

        if interrupted:
            raise asyncio.CancelledError

    async def wrapped_responder(*args: Any, **kwargs: Any) -> Any:
        await finish_progress()
        return await original(*args, **kwargs)

    ctx.respond_or_edit = wrapped_responder
    try:
        progress_task = asyncio.create_task(show_after_delay())
        yield
    finally:
        try:
            await finish_progress()
        finally:
            if had_override:
                ctx.respond_or_edit = original
            else:
                del ctx.respond_or_edit
            ctx._processing_response_active = False
            ctx._processing_response_original = previous_original
