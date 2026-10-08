"""
CHUNITHM-NET login (cookie session) for /chunithm login | logout | token.

This cog holds the logic; the three user-facing subcommands live in
cogs/chunithm_cog.py (a cog can't add subcommands to another cog's group) and
hand off here, the same way `rating` / `song_search` hand off to RatingCog.

Ways to log in (all end up storing the same cookie jar):
  1. Bookmarklet / paste: the user opens the Aime login URL, runs the login
     bookmarklet and either the website POSTs the token to this bot's optional
     web endpoint (see LOGIN_WEB_PORT below) or shows a `login clal=...`
     command to paste in the bot's DMs.
  2. `/chunithm login clal:<token>` with a token they already have (DM / slash).
  3. The "Login with SEGA ID" button, which asks for username/password(/2FA) in
     a modal. The credentials are only held in memory for that one request.

Using a stored session from other code:

    login = bot.get_cog("login")
    async with login.session(user_id) as net:      # raises NotLoggedIn
        resp = await net.request("GET", "/mobile/home/playerData")

The session re-authenticates on its own when CHUNITHM-NET expires it, and the
refreshed cookie jar is written back when the `async with` block exits.

Optional web endpoint (for the bookmarklet's "server" field), off by default:
  LOGIN_WEB_PORT       port to listen on, e.g. 8080 (unset = disabled)
  LOGIN_WEB_HOST       bind address (default 0.0.0.0)
  LOGIN_WEB_BASE_URL   public URL users enter in the bookmarklet, e.g.
                       https://bot.example.com  (needed for the pre-filled link)
Put it behind HTTPS (reverse proxy); the token is sent in the POST body.

Python 3.9 safe (no `X | Y` unions).
"""
import asyncio
import contextlib
import logging
import os
import re
from secrets import SystemRandom
from typing import Dict, Optional

import discord
from aiohttp import web
from discord.ext import commands

import chunithm_net_session as net
from config import load_settings
from i18n import get_guild_language, t

logger = logging.getLogger("DiscordBot")

EMBED_COLOR = 0x008C83
LOGIN_TIMEOUT = 300   # seconds the login flow waits for a token
BOOKMARKLET_URL = "https://chuni-penguin.beerpsi.cc/bookmarklet/"
# The hosted bookmarklet prints `c>login clal=<token>` (the original bot's prefix).
# Accept that, or a bare `login clal=<token>`, when pasted in the bot's DMs.
PASTED_LOGIN_RE = re.compile(r"^(?:c>)?login\s+(clal=\S+|\S{64})$", re.IGNORECASE)


class NotLoggedIn(Exception):
    """Raised by LoginCog.session() when the user has no stored login."""


def _lang(guild) -> str:
    if guild is None:
        return "english"
    return get_guild_language(load_settings(), guild.id)


def _embed(color, title, description) -> discord.Embed:
    return discord.Embed(color=color, title=title, description=description)


class AsyncSession:
    """Awaitable facade over a blocking ChunithmNetSession."""

    def __init__(self, inner: net.ChunithmNetSession):
        self._inner = inner

    @property
    def lwp_cookie_jar(self) -> str:
        return self._inner.lwp_cookie_jar

    async def request(self, method: str, path: str, **kwargs):
        return await asyncio.to_thread(self._inner.request, method, path, **kwargs)

    async def verify(self) -> None:
        await asyncio.to_thread(self._inner.verify)

    async def logout(self) -> None:
        await asyncio.to_thread(self._inner.logout)


# -- SEGA ID modal ------------------------------------------------------------
class SegaIDLoginModal(discord.ui.Modal, title="Login with SEGA ID"):
    username = discord.ui.TextInput(label="SEGA ID username", min_length=1)
    password = discord.ui.TextInput(label="SEGA ID password", min_length=1)
    otp = discord.ui.TextInput(
        label="Two-factor authentication code (if enabled)",
        min_length=6, max_length=6, required=False)

    def __init__(self, code: str, language: str):
        super().__init__(timeout=LOGIN_TIMEOUT)
        self.code = code
        self.language = language

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            clal = await asyncio.to_thread(
                net.sega_id_login, self.username.value, self.password.value,
                self.otp.value or None)
        except net.SessionError as e:
            await interaction.followup.send(
                embed=_embed(discord.Color.red(), "Error", str(e)), ephemeral=True)
            return

        # Hand the token to the waiting `login` command, exactly like the
        # bookmarklet does; it verifies and stores it.
        interaction.client.dispatch("chunithm_login_{}".format(self.code), clal)
        await interaction.followup.send(
            embed=_embed(discord.Color.green(), "Success", t(
                self.language, "Login successful.", "登入成功。")),
            ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        if isinstance(error, discord.NotFound):
            return
        # Deliberately no field values in the log: they hold the password.
        logger.error("error in SEGA ID login modal: %s: %s", error.__class__.__name__, error)


class SegaIDConfirmView(discord.ui.View):
    def __init__(self, code: str, language: str):
        super().__init__(timeout=LOGIN_TIMEOUT)
        self.code = code
        self.language = language

    @discord.ui.button(label="Login with SEGA ID", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SegaIDLoginModal(self.code, self.language))


class LoginFlowView(discord.ui.View):
    """The instructions message; only the user who ran /login can use it."""

    def __init__(self, author_id: int, code: str, language: str):
        super().__init__(timeout=LOGIN_TIMEOUT)
        self.author_id = author_id
        self.code = code
        self.language = language

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                t(self.language, "This login prompt isn't yours.", "這不是你的登入提示。"),
                ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Login with SEGA ID", style=discord.ButtonStyle.danger)
    async def sega_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            content=t(
                self.language,
                "If you use SEGA ID to log in, you can use this method instead of the "
                "normal login process.\n\n"
                "**Your username and password are not stored, logged, or shared; they are "
                "only kept in memory for the duration of the login request.** "
                "You can read the code in `chunithm_net_session.py`.\n\n"
                "If you do not feel safe, please continue with the normal login process.",
                "如果你使用 SEGA ID 登入，可以用這個方法代替一般的登入流程。\n\n"
                "**你的帳號與密碼不會被儲存、記錄或分享，只會在登入請求期間暫存於記憶體中。**"
                "你可以在 `chunithm_net_session.py` 檢視程式碼。\n\n"
                "如果你覺得不安全，請改用一般的登入流程。"),
            ephemeral=True,
            view=SegaIDConfirmView(self.code, self.language))


# -- the cog --------------------------------------------------------------------
class LoginCog(commands.Cog, name="login"):
    def __init__(self, bot):
        self.bot = bot
        self.random = SystemRandom()
        self._locks = {}                      # type: Dict[int, asyncio.Lock]
        self._pending = set()                 # passcodes with a login waiting
        self._web_runner = None               # type: Optional[web.AppRunner]
        self.web_base_url = (os.environ.get("LOGIN_WEB_BASE_URL") or "").rstrip("/") or None

    # -- lifecycle (optional bookmarklet endpoint) --------------------------
    async def cog_load(self):
        port = os.environ.get("LOGIN_WEB_PORT")
        if not port:
            return
        app = web.Application()
        app.add_routes([web.post("/login", self._web_login)])
        self._web_runner = web.AppRunner(app)
        await self._web_runner.setup()
        host = os.environ.get("LOGIN_WEB_HOST", "0.0.0.0")
        await web.TCPSite(self._web_runner, host, int(port)).start()
        logger.info("CHUNITHM login endpoint listening on %s:%s", host, port)

    async def cog_unload(self):
        if self._web_runner is not None:
            await self._web_runner.cleanup()
            self._web_runner = None

    async def _web_login(self, request: web.Request) -> web.Response:
        if request.content_type == "application/json":
            try:
                params = await request.json()
            except ValueError:
                raise web.HTTPBadRequest(reason="Invalid JSON")
        elif request.content_type in ("application/x-www-form-urlencoded",
                                      "multipart/form-data"):
            params = await request.post()
        else:
            raise web.HTTPBadRequest(reason="Invalid Content-Type")

        otp = params.get("otp") if hasattr(params, "get") else None
        clal = params.get("clal") if hasattr(params, "get") else None
        if not isinstance(otp, str) or not isinstance(clal, str):
            raise web.HTTPBadRequest(reason="Missing or invalid parameters")

        clal = net.strip_clal_prefix(clal)
        if not net.is_valid_clal(clal):
            raise web.HTTPBadRequest(reason="Invalid cookie provided")
        # The original only rejected passcodes that were BOTH non-numeric and
        # the wrong length; require a 6-digit number, and only accept passcodes
        # that a /login in Discord is actually waiting on.
        if not (otp.isdigit() and len(otp) == 6) or otp not in self._pending:
            raise web.HTTPBadRequest(reason="Invalid passcode provided")

        self.bot.dispatch("chunithm_login_{}".format(otp), clal)
        return web.Response(
            content_type="text/html",
            text=("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                  "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                  "<title>login</title></head><body><h1>Success!</h1>"
                  "<p>Check the bot's DMs to see if the account has been linked.</p>"
                  "</body></html>"))

    # -- pasted bookmarklet command (DMs only) ---------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is not None:
            return
        m = PASTED_LOGIN_RE.match(message.content.strip())
        if m is None:
            return
        clal = net.strip_clal_prefix(m.group(1))
        if not net.is_valid_clal(clal):
            return await message.channel.send(embed=_embed(
                discord.Color.red(), "Failed to login", "That token doesn't look valid."))
        err = await self._verify_and_login(message.author.id, clal)
        if err is None:
            logger.info("user %s logged in to CHUNITHM-NET (pasted command)", message.author.id)
            await message.channel.send(embed=_embed(
                discord.Color.green(), "Successfully logged in",
                "Your CHUNITHM-NET login is saved."))
        else:
            await message.channel.send(embed=_embed(
                discord.Color.red(), "Failed to login",
                "Invalid cookie: {}: {}".format(err.__class__.__name__, err)))

    # -- session access for other cogs -----------------------------------------
    @contextlib.asynccontextmanager
    async def session(self, user_id: int):
        """Yield an AsyncSession for a logged-in user (see module docstring)."""
        raw = net.get_cookie(user_id)
        if raw is None:
            raise NotLoggedIn(user_id)

        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            # Re-read inside the lock: a concurrent user of this session may
            # have just stored a refreshed jar.
            raw = net.get_cookie(user_id)
            if raw is None:
                raise NotLoggedIn(user_id)
            inner = net.ChunithmNetSession(raw)
            try:
                yield AsyncSession(inner)
            finally:
                refreshed = inner.lwp_cookie_jar
                inner.close()
                # Skip the write-back if they logged out while we held the jar.
                if refreshed != raw and net.get_cookie(user_id) is not None:
                    net.set_cookie(user_id, refreshed)

    async def _verify_and_login(self, user_id: int, clal: str) -> Optional[Exception]:
        """Check the token against CHUNITHM-NET and store it. None = success."""
        inner = net.ChunithmNetSession(net.build_lwp_from_clal(clal))
        try:
            await asyncio.to_thread(inner.verify)
            jar = inner.lwp_cookie_jar
        except net.SessionError as e:
            return e
        finally:
            inner.close()
        net.set_cookie(user_id, jar)
        return None

    # -- /chunithm login ---------------------------------------------------------
    async def login(self, ctx, clal: Optional[str] = None):
        language = _lang(ctx.guild)
        is_slash = ctx.interaction is not None
        in_guild_text = ctx.guild is not None and not is_slash

        if is_slash:
            await ctx.defer(ephemeral=True)

        dm_channel = None
        if in_guild_text:
            # A token pasted in a public channel is a leaked token: delete it.
            warning = ""
            if clal is not None:
                try:
                    await ctx.message.delete()
                except discord.HTTPException:
                    warning = t(
                        language,
                        "\n\nYou sent the command in a public channel and included your "
                        "CHUNITHM-NET token, which leaves your account at risk. Please "
                        "delete the message yourself (I can't), and don't send credentials "
                        "in public places.\nVisit "
                        "https://chunithm-net-eng.com/mobile/home/userOption/logout/ on the "
                        "tab you got the token from to revoke it.",
                        "\n\n你在公開頻道傳送了指令並附上 CHUNITHM-NET 令牌，你的帳號有風險。"
                        "請自行刪除該訊息（我沒有權限），也請不要在公開場合傳送憑證。\n"
                        "請在取得令牌的分頁開啟 "
                        "https://chunithm-net-eng.com/mobile/home/userOption/logout/ 以撤銷令牌。")
            dm_channel = ctx.author.dm_channel or await ctx.author.create_dm()
            await ctx.send(t(
                language,
                "Login instructions have been sent to your DMs "
                "(enable **Privacy Settings → Direct Messages** if you haven't received them).",
                "登入說明已傳送到你的私訊"
                "（若沒收到，請開啟**隱私設定 → 允許私人訊息**）。") + warning)
        elif clal is not None and net.is_valid_clal(clal):
            err = await self._verify_and_login(ctx.author.id, clal)
            if err is None:
                return await ctx.send(embed=_embed(
                    discord.Color.green(),
                    t(language, "Successfully logged in", "登入成功"),
                    t(language, "Your CHUNITHM-NET login is saved.",
                      "你的 CHUNITHM-NET 登入已儲存。")), ephemeral=True)
            return await ctx.send(embed=_embed(
                discord.Color.red(),
                t(language, "Failed to login", "登入失敗"),
                "Invalid cookie: {}: {}".format(err.__class__.__name__, err)),
                ephemeral=True)

        await self._login_flow(ctx, language, dm_channel)

    async def _login_flow(self, ctx, language: str, dm_channel):
        passcode = str(self.random.randrange(10 ** 5, 10 ** 6))
        while passcode in self._pending:
            passcode = str(self.random.randrange(10 ** 5, 10 ** 6))

        server = self.web_base_url
        embed = self._instructions_embed(language, passcode, server)
        view = LoginFlowView(ctx.author.id, passcode, language)
        prefix = ctx.clean_prefix

        try:
            if dm_channel is not None:
                msg = await dm_channel.send(embed=embed, view=view)
            else:
                msg = await ctx.send(embed=embed, view=view, ephemeral=True)
        except discord.Forbidden:
            logger.warning("could not DM login instructions to %s", ctx.author.id)
            return

        self._pending.add(passcode)
        try:
            clal = await self.bot.wait_for(
                "chunithm_login_{}".format(passcode), timeout=LOGIN_TIMEOUT)
        except asyncio.TimeoutError:
            await msg.edit(embed=_embed(
                discord.Color.yellow(),
                t(language, "Login session timed out", "登入已逾時"),
                t(language,
                  "Please use `{}chunithm login` to restart the login process.".format(
                      "/" if ctx.interaction is not None else prefix),
                  "請使用 `{}chunithm login` 重新開始登入流程。".format(
                      "/" if ctx.interaction is not None else prefix))), view=None)
            return
        finally:
            self._pending.discard(passcode)

        err = await self._verify_and_login(ctx.author.id, clal)
        if err is None:
            logger.info("user %s logged in to CHUNITHM-NET", ctx.author.id)
            await msg.edit(embed=_embed(
                discord.Color.green(),
                t(language, "Successfully logged in", "登入成功"),
                t(language, "Your CHUNITHM-NET login is saved.",
                  "你的 CHUNITHM-NET 登入已儲存。")), view=None)
        else:
            await msg.edit(embed=_embed(
                discord.Color.red(),
                t(language, "Failed to login", "登入失敗"),
                "Invalid cookie: {}: {}".format(err.__class__.__name__, err)), view=None)

    @staticmethod
    def _instructions_embed(language: str, passcode: str, server: Optional[str]) -> discord.Embed:
        fragment = "#otp={}&server={}".format(passcode, server) if server else ""
        if server:
            step3 = t(
                language,
                "If the website asks for a passcode, enter **{}**.\n"
                "If the website asks for a server, enter **{}**.".format(
                    passcode, discord.utils.escape_markdown(server)),
                "如果網站要求通行碼，請輸入 **{}**。\n"
                "如果網站要求伺服器，請輸入 **{}**。".format(
                    passcode, discord.utils.escape_markdown(server)))
        else:
            step3 = t(
                language,
                "The website will display a login command. Copy it and paste it in "
                "the bot's DMs.",
                "網站會顯示登入指令，請複製並貼到與機器人的私訊中。")
        e = discord.Embed(color=0xFEE75C, title=t(language, "How to login", "如何登入"))
        e.add_field(
            name=t(language, "Step 1", "步驟 1"),
            value=t(language,
                    "Log into [CHUNITHM-NET](https://chunithm-net-eng.com) in an "
                    "incognito/private window.\n**Remember to enable auto login!**",
                    "在無痕/私密視窗登入 [CHUNITHM-NET](https://chunithm-net-eng.com)。\n"
                    "**請記得開啟自動登入！**"),
            inline=False)
        e.add_field(
            name=t(language, "Step 2", "步驟 2"),
            value=t(language,
                    "Copy [this link](https://lng-tgk-aime-gw.am-all.net/common_auth/{}) "
                    "and paste it in the same incognito window. The site should display "
                    "\"Not found\".".format(fragment),
                    "複製[這個連結](https://lng-tgk-aime-gw.am-all.net/common_auth/{})"
                    "並貼到同一個無痕視窗，網站應顯示「Not found」。".format(fragment)),
            inline=False)
        e.add_field(
            name=t(language, "Step 3", "步驟 3"),
            value=t(language,
                    "(Save the [login bookmarklet]({}) if you haven't already.)\n"
                    "Run the bookmarklet on the \"Not found\" page. It can only access "
                    "CHUNITHM-NET, not your Aime account.\n".format(BOOKMARKLET_URL),
                    "（若尚未儲存，請先儲存[登入書籤小工具]({})。）\n"
                    "在「Not found」頁面執行書籤小工具。它只能存取 CHUNITHM-NET，"
                    "無法存取你的 Aime 帳號。\n".format(BOOKMARKLET_URL)) + step3,
            inline=False)
        e.set_footer(text=t(language,
                            "This prompt expires in 5 minutes.", "此提示將在 5 分鐘後失效。"))
        return e

    # -- /chunithm logout --------------------------------------------------------
    async def logout(self, ctx, invalidate: bool = False):
        language = _lang(ctx.guild)
        await ctx.defer(ephemeral=True)
        if net.get_cookie(ctx.author.id) is None:
            return await ctx.send(t(language, "You are not logged in.", "你尚未登入。"),
                                  ephemeral=True)

        msg = t(language, "Successfully logged out.", "已成功登出。")
        if invalidate:
            signed_out = False
            try:
                async with self.session(ctx.author.id) as client:
                    await client.logout()
                    signed_out = True
            except (net.SessionError, NotLoggedIn) as e:
                logger.warning("could not sign %s out of CHUNITHM-NET: %s", ctx.author.id, e)
            if not signed_out:
                msg = t(language,
                        "There was an error signing out from CHUNITHM-NET. "
                        "However, your login has been deleted from my records.",
                        "從 CHUNITHM-NET 登出時發生錯誤，"
                        "不過你的登入資料已從我的紀錄中刪除。")

        net.set_cookie(ctx.author.id, None)
        await ctx.send(msg, ephemeral=True)

    # -- /chunithm token ------------------------------------------------------------
    async def token(self, ctx):
        language = _lang(ctx.guild)
        # `!` replies are public in a server; only reveal over slash (ephemeral) or DMs.
        if ctx.interaction is None and ctx.guild is not None:
            return await ctx.send(t(
                language,
                "For safety, use `/chunithm token` or run this command in my DMs.",
                "為了安全，請使用 `/chunithm token` 或在與我的私訊中執行此指令。"))

        await ctx.defer(ephemeral=True)
        raw = net.get_cookie(ctx.author.id)
        clal = net.extract_clal(raw) if raw else None
        if raw is None:
            return await ctx.send(t(language, "You are not logged in.", "你尚未登入。"),
                                  ephemeral=True)
        if clal is None:
            return await ctx.send(t(
                language, "Could not find your token. This is probably a bug.",
                "找不到你的令牌，這可能是個錯誤。"), ephemeral=True)
        await ctx.send(t(
            language,
            "Your token: ||{}|| (click to reveal, DO NOT show it to other people.)".format(clal),
            "你的令牌：||{}||（點擊顯示，請勿給其他人看。）".format(clal)), ephemeral=True)


async def setup(bot):
    await bot.add_cog(LoginCog(bot))
