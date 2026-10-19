"""HTTP runtime ownership independent of the Web Cog and its consumers."""

import asyncio
import sys
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import ClientSession, web
from aiohttp.web import AppRunner, TCPSite
from async_lru import alru_cache

from chuni_penguin.utils import json_dumps

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class WebApplicationService:
    def __init__(self, bot: "ChuniBot"):
        self.bot = bot
        self.settings = bot.settings
        self.app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._session: aiohttp.ClientSession | None = None
        self._owner: object | None = None
        self._songlist: Any = None
        self._lock = asyncio.Lock()
        self.closed = False

    @property
    def oauth_available(self) -> bool:
        return (
            self.app is not None
            and self.settings.web.base_url is not None
            and self.settings.credentials.kamaitachi_client_id is not None
            and self.settings.credentials.kamaitachi_client_secret is not None
        )

    async def enable(self, owner: object):
        async with self._lock:
            if self.closed:
                msg = "Web application service is closed."
                raise RuntimeError(msg)
            if self._owner is not None:
                if self._owner is owner:
                    return
                msg = "Web application already has an owner."
                raise RuntimeError(msg)
            if (
                self.bot.offline
                or not self.settings.web.enable
                or (self.bot.shard_id is not None and self.bot.shard_id != 0)
                or (self.bot.shard_ids is not None and 0 not in self.bot.shard_ids)
            ):
                return
            await self._start(owner)

    async def _start(self, owner: object):
        # Import failure only affects an enabled Web Cog, never manual Kamaitachi.
        from chuni_penguin.web.routes import (
            _get_songlist,
            create_fallback_middleware,
            create_goatcounter_middleware,
            on_response_prepare,
            on_shutdown,
            router,
        )

        app = web.Application()
        session = ClientSession(json_serialize=json_dumps)
        runner = AppRunner(app)
        songlist = alru_cache(maxsize=1, ttl=3600)(_get_songlist)
        try:
            app.on_response_prepare.append(on_response_prepare)
            app.on_shutdown.append(on_shutdown)
            app.add_routes(router)
            session.headers.add(
                "User-Agent",
                f"chuni-penguin (+https://github.com/beer-psi/chuni-penguin) Python/{sys.version_info[0]}.{sys.version_info[1]} aiohttp/{aiohttp.__version__}",
            )
            app["bot"] = self.bot
            app["session"] = session
            app["songlist"] = songlist
            app["base_url"] = self.settings.web.base_url
            app["goatcounter"] = self.settings.web.goatcounter
            app["kamaitachi_client_id"] = self.settings.credentials.kamaitachi_client_id
            app["kamaitachi_client_secret"] = (
                self.settings.credentials.kamaitachi_client_secret
            )
            if self.settings.web.trust_proxy:
                import aiohttp_remotes  # pyright: ignore[reportMissingImports]

                await aiohttp_remotes.setup(app, aiohttp_remotes.XForwardedRelaxed())
            if self.settings.web.fallback_url is not None:
                app.middlewares.append(
                    create_fallback_middleware(self.settings.web.fallback_url)
                )
            if (
                self.settings.web.goatcounter is not None
                and self.settings.credentials.goatcounter_api_key is not None
            ):
                app.middlewares.append(
                    create_goatcounter_middleware(
                        session,
                        self.settings.web.goatcounter,
                        self.settings.credentials.goatcounter_api_key,
                        [
                            "/kamaitachi/users/*",
                            "/assets/jackets/*",
                            "/assets/characters/*",
                            "/kofi",
                        ],
                    )
                )
            await runner.setup()
            site = TCPSite(
                runner,
                host=self.settings.web.listen_address,
                port=self.settings.web.port,
            )
            await site.start()
        except BaseException:
            try:
                await runner.cleanup()
            finally:
                try:
                    await songlist.cache_close()
                finally:
                    songlist.cache_clear()
                    await session.close()
            raise
        self._session, self._runner, self.app, self._owner = session, runner, app, owner
        self._songlist = songlist

    async def _stop(self):
        runner, session, songlist = self._runner, self._session, self._songlist
        # Consumers immediately stop advertising OAuth while HTTP drains.
        self.app = self._runner = self._session = self._owner = None
        self._songlist = None
        try:
            if runner is not None:
                await runner.cleanup()
        finally:
            try:
                if songlist is not None:
                    await songlist.cache_close()
            finally:
                if songlist is not None:
                    songlist.cache_clear()
                if session is not None:
                    await session.close()

    async def disable(self, owner: object):
        async with self._lock:
            if self._owner is owner:
                await self._stop()

    async def close(self):
        async with self._lock:
            self.closed = True
            await self._stop()
