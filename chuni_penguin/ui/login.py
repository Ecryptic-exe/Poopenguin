from collections.abc import Sequence
from http.cookiejar import CookieJar
from typing import TYPE_CHECKING, override

import discord.ui
import httpx
import httpx_aiohttp
from discord import Interaction
from discord.abc import MISSING
from discord.ext.commands import Context
from discord.utils import escape_markdown

from chuni_penguin.adapters.chunithm_net._hooks import _AUTHENTICATION_URL
from chuni_penguin.logging import logger
from chuni_penguin.utils.chunithm_i18n import get_chunithm_language, t, ui_text

from ._pagination import FormatPageReturn, PaginationView
from .dismiss import CloseView
from .embeds import EmbedPageSource
from .owner_view import send_view_message

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class SegaIDLoginModal(discord.ui.Modal, title="Login with SEGA ID"):
    def __init__(
        self,
        code: str,
        *,
        title: str = MISSING,
        timeout: float | None = None,
        custom_id: str = MISSING,
        ctx=None,
    ) -> None:
        super().__init__(
            title=ui_text(ctx, "Login with SEGA ID") if title is MISSING else title,
            timeout=timeout,
            custom_id=custom_id,
        )
        self.ctx = ctx
        self.username = discord.ui.TextInput(min_length=1)
        self.password = discord.ui.TextInput(min_length=1)
        self.otp = discord.ui.TextInput(min_length=6, max_length=6, required=False)
        for text, component in (
            ("SEGA ID username", self.username),
            ("SEGA ID password", self.password),
            ("Two-factor authentication code (if enabled)", self.otp),
        ):
            self.add_item(
                discord.ui.Label(text=ui_text(ctx, text), component=component)
            )

        self.code = code

    # The client attached to the interaction is definitely ChuniBot.
    @override
    async def on_submit(self, interaction: Interaction["ChuniBot"]) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        await interaction.response.defer(ephemeral=True)

        jar = CookieJar()

        async with httpx.AsyncClient(
            cookies=jar,
            timeout=httpx.Timeout(timeout=60.0),
            transport=httpx_aiohttp.AIOHTTPTransport(retries=5),
        ) as client:
            await client.get(_AUTHENTICATION_URL)

            resp = await client.post(
                "https://lng-tgk-aime-gw.am-all.net/common_auth/login/sid",
                data={
                    "retention": "1",
                    "sid": self.username.value,
                    "password": self.password.value,
                },
                follow_redirects=False,
            )

            location = resp.headers.get("location")
            correct_username_password = False

            if location == "https://lng-tgk-aime-gw.am-all.net/common_auth/login/otp":
                correct_username_password = True

                if not self.otp.value:
                    await send_view_message(
                        interaction,
                        embed=discord.Embed(
                            color=discord.Color.red(),
                            title=ui_text(self.ctx or interaction, "Error"),
                            description=ui_text(
                                interaction,
                                "Two-factor authentication was enabled, but a code was not provided.",
                            ),
                        ),
                        ephemeral=True,
                        view=CloseView(
                            interaction.user.id, ctx=self.ctx or interaction
                        ),
                    )
                    return

                resp = await client.post(
                    "https://lng-tgk-aime-gw.am-all.net/common_auth/login/otpauth",
                    data={"password": self.otp.value},
                    follow_redirects=False,
                )
                location = resp.headers.get("location")

            if location is None or "https://chunithm-net-eng.com" not in location:
                if correct_username_password:
                    description = ui_text(
                        interaction, "Invalid two-factor authentication code."
                    )
                else:
                    description = ui_text(
                        self.ctx or interaction, "Invalid username or password."
                    )

                await send_view_message(
                    interaction,
                    embed=discord.Embed(
                        color=discord.Color.red(),
                        title=ui_text(self.ctx or interaction, "Error"),
                        description=description,
                    ),
                    ephemeral=True,
                    view=CloseView(interaction.user.id, ctx=self.ctx or interaction),
                )
                return

            clal = client.cookies.get("clal", domain="lng-tgk-aime-gw.am-all.net")

            if clal is None:
                await send_view_message(
                    interaction,
                    embed=discord.Embed(
                        color=discord.Color.red(),
                        title=ui_text(self.ctx or interaction, "Error"),
                        description=ui_text(
                            interaction,
                            "Login was successful, but could not retrieve token.",
                        ),
                    ),
                    ephemeral=True,
                    view=CloseView(interaction.user.id, ctx=self.ctx or interaction),
                )
                return

            interaction.client.dispatch(f"chunithm_login_{self.code}", clal)

            await send_view_message(
                interaction,
                embed=discord.Embed(
                    color=discord.Color.green(),
                    title=ui_text(self.ctx or interaction, "Success"),
                    description=ui_text(self.ctx or interaction, "Login successful."),
                ),
                ephemeral=True,
                view=CloseView(interaction.user.id, ctx=self.ctx or interaction),
            )

    @override
    async def on_error(self, interaction: Interaction, error: Exception, /) -> None:
        if isinstance(error, discord.NotFound):
            return

        await logger.aexception(
            "error in SEGA ID login modal",
            tag="error_sega_id_login_modal",
            exc_info=error,
        )


class LoginWithSegaIDView(discord.ui.View):
    def __init__(
        self, code: str, *, owner_id: int, timeout: float | None = 180, ctx=None
    ):
        super().__init__(timeout=timeout)

        self.code = code
        self.ctx = ctx
        self.login_with_sega_id.label = ui_text(ctx, "Login with SEGA ID")
        from .dismiss import ensure_close

        ensure_close(self, owner_id, ctx=ctx)

    @discord.ui.button(label="Login with SEGA ID", style=discord.ButtonStyle.green)
    async def login_with_sega_id(self, interaction: Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(
            SegaIDLoginModal(self.code, timeout=self.timeout, ctx=self.ctx)
        )


class LoginFlowPageSource(EmbedPageSource):
    def __init__(self, code: str, server: str | None, ctx=None) -> None:
        self.ctx = ctx
        language = get_chunithm_language(ctx)
        step_3_description = t(
            language,
            "**Step 3**:\n"
            "(Save the [login bookmarklet](https://chuni-penguin.beerpsi.cc/bookmarklet/) if you haven't already.)\n\n"
            'Run the login bookmarklet on the "Not found" page opened previously.\n\n'
            "This script cannot access your Aime account! It can only access CHUNITHM-NET.\n"
            "\n",
            "**步驟 3**：\n"
            "（如尚未儲存，請先儲存[登入書籤](https://chuni-penguin.beerpsi.cc/bookmarklet/)。）\n\n"
            "在剛才開啟、顯示「Not found」的頁面執行登入書籤。\n\n"
            "此腳本只能存取 CHUNITHM-NET，不能存取你的 Aime 帳號。\n\n",
        )

        if code is None or server is None:
            step_3_description += t(
                language,
                "The website will display the login command. Copy it and paste it in the bot's DMs.",
                "網站會顯示登入指令，請複製並貼到機器人的私人訊息。",
            )
        else:
            step_3_description += t(
                language,
                f"If the website asks for a passcode, enter **{code}**.\n"
                f"If the website asks for a server, enter **{escape_markdown(server)}**.\n",
                f"如網站要求驗證碼，請輸入 **{code}**。\n如要求伺服器，請輸入 **{escape_markdown(server)}**。\n",
            )

        fragment = f"#otp={code}&server={server}" if server is not None else ""
        entries: list[discord.Embed] = [
            discord.Embed(
                color=discord.Color.yellow(),
                title=ui_text(getattr(self, "ctx", None), "How to login"),
                description=t(
                    language,
                    "**Step 1:**\n"
                    "Log into [CHUNITHM-NET](https://chunithm-net-eng.com) in an incognito/private window.\n"
                    "(right click and copy link on desktop, long press and copy link on mobile)\n"
                    "\n"
                    "**Remember to enable auto login!**",
                    "**步驟 1**：\n在無痕／私人視窗登入 [CHUNITHM-NET](https://chunithm-net-eng.com)。\n"
                    "（電腦可按右鍵複製連結；手機可長按複製連結。）\n\n**記得啟用自動登入！**",
                ),
            ).set_image(
                url="https://chuni-penguin.beerpsi.cc/assets/images/enable-auto-login.png"
            ),
            discord.Embed(
                color=discord.Color.yellow(),
                title=ui_text(getattr(self, "ctx", None), "How to login"),
                description=t(
                    language,
                    "**Step 2**:\n"
                    f"Copy [this link](https://lng-tgk-aime-gw.am-all.net/common_auth/{fragment}) and paste it in the incognito window.\n"
                    'The website should display "Not found".',
                    f"**步驟 2**：\n複製[此連結](https://lng-tgk-aime-gw.am-all.net/common_auth/{fragment})，並貼到無痕視窗。\n網站應會顯示「Not found」。",
                ),
            ).set_image(
                url="https://chuni-penguin.beerpsi.cc/assets/images/login-not-found.png"
            ),
            discord.Embed(
                color=discord.Color.yellow(),
                title=ui_text(getattr(self, "ctx", None), "How to login"),
                description=step_3_description,
            ),
        ]

        super().__init__(entries, per_page=1)

    async def format_page(
        self, menu: "PaginationView", page: Sequence[discord.Embed]
    ) -> FormatPageReturn:
        result = await super().format_page(menu, page)

        assert isinstance(result, dict)
        result["content"] = ""

        return result


class LoginFlowView(PaginationView):
    def __init__(self, ctx: Context, code: str, server: str | None = None):
        super().__init__(
            ctx,
            LoginFlowPageSource(code, server, ctx),
        )
        self.code = code
        self.login_with_sega_id.label = ui_text(ctx, "Login with SEGA ID")
        self.add_item(self.login_with_sega_id)

    @override
    async def show_page(self, interaction: Interaction, page_index: int):
        if page_index != 0:
            self.remove_item(self.login_with_sega_id)
        else:
            self.add_item(self.login_with_sega_id)

        return await super().show_page(interaction, page_index)

    @discord.ui.button(
        label="Login with SEGA ID", style=discord.ButtonStyle.danger, row=1
    )
    async def login_with_sega_id(self, interaction: Interaction, _: discord.ui.Button):
        await send_view_message(
            interaction,
            content=t(
                get_chunithm_language(self.ctx),
                "If you use SEGA ID to log in, you can use this method instead of the normal login process.\n"
                "\n"
                "**Your username and password will not be stored, logged, or shared, and is only kept in temporary memory "
                "for the duration of the login process. If you wish, you may inspect the [source code](https://github.com/beer-psi/chuni-penguin).**\n"
                "\n"
                "If you do not feel safe, please continue with the normal login process.",
                "若你使用 SEGA ID 登入，可以使用此方法取代一般登入流程。\n\n"
                "**帳號與密碼只會在登入期間暫存於記憶體，不會儲存、記錄或分享。"
                "你也可以查看[原始碼](https://github.com/beer-psi/chuni-penguin)。**\n\n"
                "若不放心，請繼續使用一般登入流程。",
            ),
            ephemeral=True,
            view=LoginWithSegaIDView(
                self.code,
                owner_id=self.ctx.author.id,
                timeout=self.timeout,
                ctx=self.ctx,
            ),
        )
