# ruff: noqa: RUF001
"""Safe, read-only account state for the interactive account panel."""

from dataclasses import dataclass
from datetime import datetime

import discord
from sqlalchemy import func, select

from chuni_penguin.database import Cookie, PlayerSource
from chuni_penguin.services.native_scores import PartialScoreSync
from chuni_penguin.utils.chunithm_i18n import ui_text

ACCOUNT_SOURCES = (
    ("CHUNITHM-NET", "net"),
    ("Chunirec", "chunirec:jp2"),
    ("Chunilib 匯出檔", "chunilib:export"),
)
LEGACY_SOURCES = (("Kamaitachi", "kamaitachi"), ("Chunirec jp", "chunirec:jp"))
CHUNILIB_URL = "https://chuni.tsaibee.org/"


@dataclass(frozen=True, slots=True)
class AccountSourceState:
    """Display and action state that contains no account credentials or payloads."""

    key: str
    label: str
    linked: bool
    status: str
    last_sync: str | None
    can_sync: bool
    can_unlink: bool
    can_logout: bool
    can_token: bool
    primary_label: str
    primary_action: str
    notice: str | None = None
    needs_attention: bool = False


@dataclass(frozen=True, slots=True)
class AccountState:
    sources: tuple[AccountSourceState, ...]
    hidden_sources: tuple[AccountSourceState, ...] = ()

    def source(self, key: str) -> AccountSourceState | None:
        """Return one source's state, or ``None`` when the key is unknown."""
        return next(
            (
                source
                for source in (*self.sources, *self.hidden_sources)
                if source.key == key
            ),
            None,
        )


def _sync_status(status: str | None, ctx=None) -> str | None:
    if status is None:
        return None
    return {
        "partial; recent scores read": ui_text(ctx, "部分同步 · 已讀取最近遊玩"),
        "failed": ui_text(ctx, "同步失敗"),
        "synced": ui_text(ctx, "已同步"),
        "failed; previous snapshot retained": ui_text(ctx, "同步失敗，先前資料已保留"),
        "failed; partial cache possible": ui_text(ctx, "同步失敗，快取可能部分更新"),
    }.get(status)


async def load_account_state(ctx) -> AccountState:
    """Read the caller's account rows and return only safe UI-facing state."""
    async with ctx.bot.begin_db_read() as db:
        rows = await db.execute(
            select(
                PlayerSource.source,
                PlayerSource.status,
                PlayerSource.synced_at,
                (PlayerSource.credential != "").label("has_credential"),
                (PlayerSource.payload != "").label("has_snapshot"),
            ).where(PlayerSource.discord_id == ctx.author.id)
        )
        sources = {row.source: row for row in rows}
        header = "#LWP-Cookies-2.0"
        cookie = (
            await db.execute(
                select(
                    (Cookie.cookie != "").label("linked"),
                    (func.substr(Cookie.cookie, 1, len(header)) == header).label(
                        "usable"
                    ),
                    (Cookie.kamaitachi_token != "").label("kamaitachi"),
                ).where(Cookie.discord_id == ctx.author.id)
            )
        ).one_or_none()

    net_cookie_usable = bool(cookie and cookie.usable)
    net_linked = bool(cookie and cookie.linked)
    kamaitachi_linked = bool(cookie and cookie.kamaitachi)
    get_cog = getattr(ctx.bot, "get_cog", None)
    kamaitachi_ready = get_cog is not None and get_cog("Kamaitachi") is not None

    states = []
    for label, key in (*ACCOUNT_SOURCES, *LEGACY_SOURCES):
        row = sources.get(key)
        last_sync = row.synced_at if row is not None and row.synced_at else None

        status_code = row.status if row else None
        sync_status = _sync_status(status_code, ctx)
        failed = bool(status_code and status_code.startswith(("failed", "partial")))
        if key == "net":
            states.append(
                AccountSourceState(
                    key=key,
                    label=ui_text(ctx, label),
                    linked=net_linked,
                    status=(
                        (sync_status or ui_text(ctx, "已連結"))
                        if net_cookie_usable
                        else ui_text(ctx, "憑證需要更新")
                        if net_linked
                        else ui_text(ctx, "未連結")
                    ),
                    last_sync=last_sync,
                    needs_attention=failed or (net_linked and not net_cookie_usable),
                    can_sync=net_cookie_usable,
                    can_unlink=False,
                    can_logout=net_linked,
                    can_token=net_cookie_usable,
                    primary_label=ui_text(ctx, "同步成績")
                    if net_cookie_usable
                    else ui_text(ctx, "重新登入") if net_linked else ui_text(ctx, "登入 NET"),
                    primary_action="run_sync" if net_cookie_usable else "login",
                    notice=(
                        ui_text(ctx, "NET 憑證格式無效，請重新登入。")
                        if net_linked and not net_cookie_usable
                        else None
                    ),
                )
            )
        elif key == "kamaitachi":
            notice = (
                ui_text(ctx, "Kamaitachi 需由管理員配置並啟用既有整合。")
                if not kamaitachi_ready
                else None
            )
            states.append(
                AccountSourceState(
                    key=key,
                    label=ui_text(ctx, label),
                    linked=kamaitachi_linked,
                    status=(
                        (sync_status or ui_text(ctx, "已連結"))
                        if kamaitachi_linked
                        else ui_text(ctx, "未連結")
                    ),
                    last_sync=last_sync,
                    needs_attention=failed,
                    can_sync=kamaitachi_linked and kamaitachi_ready,
                    can_unlink=False,
                    can_logout=False,
                    can_token=False,
                    primary_label=(
                        ui_text(ctx, "重新整理")
                        if not kamaitachi_ready
                        else ui_text(ctx, "同步")
                        if kamaitachi_linked
                        else ui_text(ctx, "連結")
                    ),
                    primary_action=(
                        "refresh"
                        if not kamaitachi_ready
                        else "run_sync"
                        if kamaitachi_linked
                        else "link_source"
                    ),
                    notice=notice,
                )
            )
        elif key.startswith("chunirec:"):
            linked = bool(row and row.has_credential)
            status = ui_text(ctx, "已連結") if linked else ui_text(ctx, "未連結")
            expired = bool(
                linked and row and row.status == "failed; invalid credential"
            )
            if expired:
                status = ui_text(ctx, "憑證已失效")
            if sync_status is not None:
                status = f"{status} · {sync_status}"
            states.append(
                AccountSourceState(
                    key=key,
                    label=ui_text(ctx, label),
                    linked=linked,
                    status=status,
                    last_sync=last_sync,
                    needs_attention=failed,
                    can_sync=linked and not expired,
                    can_unlink=linked,
                    can_logout=False,
                    can_token=False,
                    primary_label=ui_text(ctx, "同步成績")
                    if linked and not expired
                    else ui_text(ctx, "重新連結")
                    if expired
                    else ui_text(ctx, "連結帳號"),
                    primary_action="run_sync"
                    if linked and not expired
                    else "link_source",
                )
            )
        else:
            imported = bool(row and row.has_snapshot)
            states.append(
                AccountSourceState(
                    key=key,
                    label=ui_text(ctx, label),
                    linked=imported,
                    status=ui_text(ctx, "已匯入")
                    if imported
                    else ui_text(ctx, "未匯入"),
                    last_sync=last_sync,
                    can_sync=False,
                    can_unlink=imported,
                    can_logout=False,
                    can_token=False,
                    primary_label=ui_text(ctx, "匯入檔案"),
                    primary_action="import",
                    notice=ui_text(
                        ctx,
                        "[開啟 Chunilib]({0}) 取得自己的 player-data JSON，再按「匯入檔案」私下傳送。",
                        CHUNILIB_URL,
                    ),
                )
            )

    return AccountState(
        tuple(states[: len(ACCOUNT_SOURCES)]), tuple(states[len(ACCOUNT_SOURCES) :])
    )


def account_embed(
    ctx, source: AccountSourceState, notice: str | None = None
) -> discord.Embed:
    """Present safe metadata for the selected source; other sources live in the selector."""
    title = (
        f"Chunirec · {source.key.split(':')[1]}"
        if source.key.startswith("chunirec:")
        else source.label
    )
    description = f"**{source.status}**"
    if notice:
        description += "\n" + notice[:3000]
    elif source.notice:
        description += "\n" + source.notice

    embed = discord.Embed(
        title=title,
        description=description,
        color=0xB16A12
        if source.needs_attention
        else 0x238477
        if source.linked
        else 0x5865F2,
    )
    embed.set_author(name=ui_text(ctx, "主資料介面"))
    if source.last_sync:
        try:
            instant = datetime.fromisoformat(source.last_sync)
            updated = (
                discord.utils.format_dt(instant, style="f")
                if instant.tzinfo
                else source.last_sync
            )
        except ValueError:
            updated = ui_text(ctx, "來源未提供時間")
    else:
        updated = ui_text(ctx, "尚無成功紀錄")
    label = "最後成功匯入" if source.key == "chunilib:export" else "最後成功同步"
    embed.add_field(name=ui_text(ctx, label), value=updated, inline=False)
    embed.set_footer(text=ui_text(ctx, "只有開啟者能操作 · 逾時請重新開啟 /account"))
    return embed


def partial_sync_notice(ctx, partial: PartialScoreSync) -> str:
    if partial.detail_count is not None and partial.detail_count < partial.recent:
        return ui_text(
            ctx,
            "{0} returned {1} recent plays. Details completed for {2}/{1}. Personal bests have not been requested. Retry sync later.",
            partial.source,
            partial.recent,
            partial.detail_count,
        )
    return ui_text(
        ctx,
        "{0} returned {1} recent plays. Personal bests could not be fully retrieved. Retry sync later.",
        partial.source,
        partial.recent,
    )
