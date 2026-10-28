# ruff: noqa: RUF001 - Bilingual command messages
"""Cumulative daily changes and silent 06:05 CHUNITHM-NET observations."""

import asyncio
from collections import defaultdict
from datetime import date as Date
from datetime import datetime, time
from http.cookiejar import LoadError
from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy import select

from chuni_penguin.adapters.chunithm_net import ChuniNetError
from chuni_penguin.adapters.errors import AuthenticationError, MaintenanceError
from chuni_penguin.application.lifecycle import cancel_loop
from chuni_penguin.context import PenguinContext
from chuni_penguin.database import Cookie
from chuni_penguin.logging import logger
from chuni_penguin.services.dailyupdate import (
    HK,
    MissingSnapshotError,
    SnapshotStorageError,
    SnapshotStore,
    current_time,
    fetch_snapshot,
    has_changes,
    historical_pair,
    window_start,
)
from chuni_penguin.ui.dailyupdate import DailyUpdateView
from chuni_penguin.utils.chunithm_i18n import get_chunithm_language, t

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot

AUTO_FETCH_TIME = time(6, 5, tzinfo=HK)
USER_FETCH_DELAY = 3.0


def authentication_failed(error: Exception) -> bool:
    return isinstance(
        error, (AuthenticationError, LoadError, commands.CommandError)
    ) or (
        isinstance(error, ChuniNetError)
        and error.code
        in {
            ChuniNetError.LOGIN_FAILURE,
            ChuniNetError.CONNECTION_EXPIRED,
            ChuniNetError.INVALID_SESSION,
            ChuniNetError.INVALID_ACCESS,
        }
    )


class DailyUpdateCog(commands.Cog, name="DailyUpdate"):
    def __init__(self, bot: "ChuniBot"):
        self.bot = bot
        self.accounts = bot.services.accounts
        self.store = SnapshotStore()
        self._user_locks = defaultdict(asyncio.Lock)
        self._batch_lock = asyncio.Lock()

    async def cog_load(self):
        if not getattr(self.bot, "offline", False):
            self.auto_fetch.start()

    async def cog_unload(self):
        await cancel_loop(self.auto_fetch)

    async def _cookie(self, user_id):
        saved = await self.accounts.database.cookies.get_by_discord_id(user_id)
        return saved.cookie if saved is not None else ""

    async def _history(self, user_id):
        try:
            return await asyncio.to_thread(self.store.load, user_id)
        except OSError as error:
            msg = "Dailyupdate history could not be read."
            raise SnapshotStorageError(msg) from error

    async def _mark_expired(self, user_id):
        try:
            # The account manager may refresh its jar even during a failed
            # request. Mark the current stored jar, never log its contents.
            cookie = await self._cookie(user_id)
            await asyncio.to_thread(self.store.mark_session_expired, user_id, cookie)
        except Exception as error:  # noqa: BLE001 - advisory metadata must not hide the authentication error
            await logger.awarning(
                "Dailyupdate session marker could not be saved",
                user_id=user_id,
                error_type=type(error).__name__,
            )

    async def _capture(
        self, user_id, context, *, only_missing=False, expected_window=None
    ):
        # Covers load -> fetch -> atomic save for manual and automatic requests.
        # The account service has its own locks for login/logout and readers.
        async with self._user_locks[user_id]:
            history = await self._history(user_id)
            boundary = expected_window or window_start(current_time())
            if only_missing and boundary.date().isoformat() in history.windows:
                return None
            cookie = await self._cookie(user_id)
            if await asyncio.to_thread(self.store.session_expired, user_id, cookie):
                msg = "The stored CHUNITHM-NET session needs /login again."
                raise AuthenticationError(msg)
            try:
                async with asyncio.timeout(720):
                    current = await fetch_snapshot(self.accounts, context)
            except Exception as error:
                if authentication_failed(error):
                    await self._mark_expired(user_id)
                raise
            if expected_window is not None and current.window_start != expected_window:
                # A large batch/fetch must not create tomorrow's baseline early
                # merely because a request crossed the 04:00 boundary.
                return None
            key = current.window_start.date().isoformat()
            is_new = key not in history.windows
            write = asyncio.create_task(
                asyncio.to_thread(self.store.save, user_id, history, current)
            )
            try:
                saved = await asyncio.shield(write)
            except asyncio.CancelledError:
                # Keep the user's lock until the disk write has finished.
                await asyncio.gather(write, return_exceptions=True)
                raise
            except OSError as error:
                msg = "Dailyupdate snapshot could not be saved."
                raise SnapshotStorageError(msg) from error
            try:
                await asyncio.to_thread(self.store.clear_session_expired, user_id)
            except OSError as error:
                await logger.awarning(
                    "Dailyupdate session marker could not be cleared",
                    user_id=user_id,
                    error_type=type(error).__name__,
                )
            return saved, is_new

    async def _session_users(self):
        async with self.accounts.database.read_sessionmaker() as session:
            query = select(Cookie.discord_id).where(Cookie.cookie != "")
            return list(await session.scalars(query))

    async def _fetch_accounts(self, *, only_missing=False):
        async with self._batch_lock:
            boundary = window_start(current_time())
            try:
                user_ids = await self._session_users()
            except Exception as error:  # noqa: BLE001 - never log SQL parameters or credentials
                await logger.awarning(
                    "Dailyupdate session scan failed", error_type=type(error).__name__
                )
                return
            for index, user_id in enumerate(user_ids):
                if window_start(current_time()) != boundary:
                    break
                if index:
                    await asyncio.sleep(USER_FETCH_DELAY)
                try:
                    await self._capture(
                        user_id,
                        user_id,
                        only_missing=only_missing,
                        expected_window=boundary,
                    )
                except Exception as error:  # noqa: BLE001 - isolate each account and only log the exception type
                    await logger.awarning(
                        "Dailyupdate automatic fetch failed",
                        user_id=user_id,
                        error_type=type(error).__name__,
                    )

    @tasks.loop(time=AUTO_FETCH_TIME)
    async def auto_fetch(self):
        # Scores earned before the normal 06:05 capture are part of its baseline.
        # This job never sends a Discord message or notification.
        await self._fetch_accounts()

    @auto_fetch.before_loop
    async def before_auto_fetch(self):
        await self.bot.wait_until_ready()
        now = current_time()
        # Use the day-window date: a restart at 02:00 still belongs to yesterday.
        due = datetime.combine(window_start(now).date(), AUTO_FETCH_TIME)
        if now >= due:
            await self._fetch_accounts(only_missing=True)

    @auto_fetch.error
    async def auto_fetch_error(self, error):
        await logger.awarning(
            "Dailyupdate automatic job failed", error_type=type(error).__name__
        )

    @commands.hybrid_command(name="dailyupdate", extras={"invoke_on_edit": False})
    @app_commands.describe(
        date="Past day window to view (YYYY-MM-DD, starts at 04:00 GMT+8)."
    )
    @commands.max_concurrency(1, per=commands.BucketType.user, wait=False)
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def dailyupdate(self, ctx: PenguinContext, date: str | None = None):
        """Show cumulative changes since this day window's fixed baseline.

        Baselines normally come from the silent 06:05 GMT+8 capture. If missing,
        your first successful manual fetch starts tracking for this window.
        The optional date names a completed window starting at 04:00 GMT+8.
        """
        await ctx.defer()
        language = get_chunithm_language(ctx)

        async def reply(english, chinese):
            await ctx.respond_or_edit(t(language, english, chinese))

        day = None
        if date is not None:
            try:
                day = Date.fromisoformat(date)
            except ValueError:
                day = None
            if (
                day is None
                or day.isoformat() != date
                or day >= window_start(current_time()).date()
            ):
                await reply(
                    "Use YYYY-MM-DD for a past window that has already ended (04:00 GMT+8).",
                    "請使用 YYYY-MM-DD，指定已於 GMT+8 04:00 結束的過去視窗。",
                )
                return
        try:
            if day is not None:
                async with self._user_locks[ctx.author.id]:
                    history = await self._history(ctx.author.id)
                previous, current = historical_pair(history, day)
            else:
                saved, is_new = await self._capture(ctx.author.id, ctx)
                previous, current = saved.baseline, saved.latest
                if is_new:
                    await reply(
                        f"Tracking starts now ({previous.captured_at:%Y-%m-%d %H:%M} GMT+8). Baseline saved. Run /dailyupdate again in this same day window to see cumulative changes.",
                        f"由現在（{previous.captured_at:%Y-%m-%d %H:%M} GMT+8）開始追蹤，已儲存固定基準。在同一個日期視窗再次使用 /dailyupdate 即可查看累積變化。",
                    )
                    return
        except MissingSnapshotError:
            await reply(
                f"Cannot show {day}: that window's baseline/latest pair is missing or older than the retained 30 days.",
                f"無法顯示 {day}：缺少該視窗的 baseline／latest，或資料已超過保留的 30 天。",
            )
            return
        except SnapshotStorageError:
            await reply(
                "Your snapshot history could not be read or saved. Existing snapshots are preserved; ask the bot administrator to check data/dailyupdate.",
                "無法讀取或儲存快照歷史，原有快照已保留。請管理員檢查 data/dailyupdate。",
            )
            return
        except MaintenanceError:
            await reply(
                "CHUNITHM-NET is under maintenance. Try again later. Your snapshots have not changed.",
                "CHUNITHM-NET 正在維護，請稍後再試。快照未變更。",
            )
            return
        except Exception as error:  # noqa: BLE001 - never echo or log cookies, response bodies, or raw exceptions
            if authentication_failed(error):
                await reply(
                    "Your CHUNITHM-NET login is missing, expired, or invalid. Run /login again. Your snapshots have not changed.",
                    "CHUNITHM-NET 尚未登入、憑證已過期或無效。請重新使用 /login。快照未變更。",
                )
            else:
                await reply(
                    "CHUNITHM-NET could not be reached or returned incomplete records. Try again later. Your snapshots have not changed.",
                    "無法連線至 CHUNITHM-NET，或成績資料不完整。請稍後再試。快照未變更。",
                )
            return

        if not has_changes(previous, current):
            await reply(
                f"No changes since {previous.captured_at:%Y-%m-%d %H:%M} GMT+8.",
                f"自 {previous.captured_at:%Y-%m-%d %H:%M} GMT+8 的基準起沒有變化。",
            )
            return
        view = DailyUpdateView(ctx, previous, current, live=day is None)
        try:
            await view.start()
        except (OSError, ValueError):
            view.stop()
            await reply(
                "The image could not be rendered. Check the bundled dailyupdate font and artwork cache. Successfully fetched snapshots remain saved.",
                "無法產生圖片，請檢查隨附的 dailyupdate 字型及曲繪快取。成功擷取的快照已保留。",
            )


async def setup(bot: "ChuniBot"):
    await bot.add_cog(DailyUpdateCog(bot))
