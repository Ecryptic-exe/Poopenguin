# ruff: noqa: RUF001
"""Safe, read-only account state for the interactive account panel."""

from dataclasses import dataclass

from sqlalchemy import select

from chuni_penguin.database import Cookie, PlayerSource
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


def _sync_status(row: PlayerSource | None, ctx=None) -> str | None:
    if row is None:
        return None
    return {
        "synced": ui_text(ctx, "已同步"),
        "failed; previous snapshot retained": ui_text(ctx, "同步失敗，先前資料已保留"),
        "failed; partial cache possible": ui_text(ctx, "同步失敗，快取可能部分更新"),
    }.get(row.status)


def _row_status(row: PlayerSource | None, fallback: str, ctx=None) -> str:
    return _sync_status(row, ctx) or fallback


async def load_account_state(ctx) -> AccountState:
    """Read the caller's account rows and return only safe UI-facing state."""
    async with ctx.bot.begin_db_read() as db:
        rows = (
            await db.scalars(
                select(PlayerSource).where(PlayerSource.discord_id == ctx.author.id)
            )
        ).all()
        cookie = await db.get(Cookie, ctx.author.id)

    sources = {row.source: row for row in rows}
    raw_net_cookie = cookie.cookie if cookie is not None else ""
    net_cookie_usable = raw_net_cookie.startswith("#LWP-Cookies-2.0")
    net_linked = bool(raw_net_cookie)
    kamaitachi_linked = bool(cookie and cookie.kamaitachi_token)
    get_cog = getattr(ctx.bot, "get_cog", None)
    kamaitachi_ready = get_cog is not None and get_cog("Kamaitachi") is not None

    states = []
    for label, key in (*ACCOUNT_SOURCES, *LEGACY_SOURCES):
        row = sources.get(key)
        last_sync = row.synced_at if row is not None and row.synced_at else None

        if key == "net":
            states.append(
                AccountSourceState(
                    key=key,
                    label=ui_text(ctx, label),
                    linked=net_linked,
                    status=(
                        _row_status(row, ui_text(ctx, "已連結"), ctx)
                        if net_cookie_usable
                        else ui_text(ctx, "憑證需要更新")
                        if net_linked
                        else ui_text(ctx, "未連結")
                    ),
                    last_sync=last_sync,
                    can_sync=net_cookie_usable,
                    can_unlink=False,
                    can_logout=net_linked,
                    can_token=net_cookie_usable,
                    primary_label=ui_text(ctx, "同步")
                    if net_cookie_usable
                    else ui_text(ctx, "登入"),
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
                        _row_status(row, ui_text(ctx, "已連結"), ctx)
                        if kamaitachi_linked
                        else ui_text(ctx, "未連結")
                    ),
                    last_sync=last_sync,
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
            linked = bool(row and row.credential)
            status = ui_text(ctx, "已連結") if linked else ui_text(ctx, "未連結")
            sync_status = _sync_status(row, ctx)
            if sync_status is not None:
                status = f"{status} · {sync_status}"
            states.append(
                AccountSourceState(
                    key=key,
                    label=ui_text(ctx, label),
                    linked=linked,
                    status=status,
                    last_sync=last_sync,
                    can_sync=linked,
                    can_unlink=linked,
                    can_logout=False,
                    can_token=False,
                    primary_label=ui_text(ctx, "同步")
                    if linked
                    else ui_text(ctx, "連結"),
                    primary_action="run_sync" if linked else "link_source",
                )
            )
        else:
            imported = bool(row and row.payload)
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
