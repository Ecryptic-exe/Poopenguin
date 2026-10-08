"""
CHUNITHM command group: /chunithm <subcommand> (and !chunithm <subcommand>).

  Your imported scores   upload | me | song | pattern | suggest | delete
  Song charts            rating | song_search

Users export their player data JSON (the chunithm-player-data_*.json file),
attach it to /chunithm upload, and the bot stores a compact copy keyed by their
Discord user id in data/chunithm_scores.json.

/chunithm pattern and /chunithm suggest also need data/chart_tags.json, built
from the community chart sheet with tools/build_chart_tags.py (analysis:
chart_analysis.py).

Only entries with score > 0 are kept (the export lists every chart, ~6400 rows,
most of them unplayed). One record per user; re-uploading replaces it.

The rating-chart engine lives in cogs/rating_cog.py (RatingCog). A cog can't
own subcommands of a group that lives in another cog, so the two subcommands
here (rating, song_search) are thin wrappers that hand off to RatingCog. If
RatingCog failed to load (missing packages), they reply that the feature is
unavailable instead of disappearing.
Python 3.9 safe (no `X | Y` unions).
"""
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import discord
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands

import chart_analysis
import chunithm_net_session as net
from config import DATA_DIR, _load, _save, load_settings
from i18n import t, get_guild_language
from ratingbot.catalog import DIFFICULTIES, normalize   # stdlib only

logger = logging.getLogger("DiscordBot")

SCORES_FILE = os.path.join(DATA_DIR, "chunithm_scores.json")
CHART_FILE = os.path.join(DATA_DIR, "chart_tags.json")
MAX_BYTES = 5 * 1024 * 1024          # export is ~0.9 MB; 5 MB is generous
EMBED_COLOR = 0x008C83
DIFFS = ("Basic", "Advanced", "Expert", "Master", "Ultima")
_lock = threading.Lock()
SCOPE_LABEL = {"all": "all played charts ≥14", "best": "Best 30",
               "rating": "Best 30 + New 20"}
_chart_cache = {"mtime": None, "table": None}
_db_cache = {"sig": None, "db": None}


def load_chart_table():
    """data/chart_tags.json, re-read only when the file changes. None if absent."""
    try:
        mtime = os.path.getmtime(CHART_FILE)
    except OSError:
        return None
    if _chart_cache["mtime"] != mtime:
        with open(CHART_FILE, encoding="utf-8") as f:
            _chart_cache["table"] = json.load(f)
        _chart_cache["mtime"] = mtime
    return _chart_cache["table"]


FLAG_BADGE = {"high_tolerance": "高容錯", "low_tolerance": "低容錯",
              "personal": "個人差", "practice": "練習向", "easy_score": "好吃分"}


def _note_line(chart, width=45):
    """'[高容錯] sheet note…' for suggestion lines; '' if the chart has neither."""
    badges = "".join("[{}]".format(FLAG_BADGE[f]) for f in chart.get("flags", [])
                     if f in FLAG_BADGE)
    note = chart.get("note") or ""
    if len(note) > width:
        note = note[:width - 1] + "…"
    text = (badges + " " + _esc(note)).strip()
    return "\n" + text if text else ""


def _esc(text):
    return discord.utils.escape_markdown(text)


def _field(lines, limit=1000):
    out = ""
    for ln in lines:
        if len(out) + len(ln) + 1 > limit:
            break
        out += ln + "\n"
    return out.strip() or "—"


def _lang_for(guild) -> str:
    """Guild language ("english"/"chinese"); DMs and unknown guilds -> english."""
    if guild is None:
        return "english"
    return get_guild_language(load_settings(), guild.id)


def parse_export(raw: bytes) -> dict:
    """Validate a player-data export and return the compact record to store.
    Raises ValueError with a user-facing message on bad input."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("That isn't valid JSON.")
    if not isinstance(data, dict) or not isinstance(data.get("score"), list):
        raise ValueError("This doesn't look like a CHUNITHM player-data export "
                         "(no `score` list).")

    def clean(rows, keep_zero=False):
        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            try:
                s = int(r["score"])
                if s <= 0 and not keep_zero:
                    continue
                out.append({
                    "title": str(r["title"]),
                    "difficulty": str(r["difficulty"]),
                    "score": s,
                    "fc": bool(r.get("isFullCombo")),
                    "aj": bool(r.get("isAllJustice")),
                    "chain": int(r.get("fullChainLv") or 0),
                    "idx": int(r["idx"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
        return out

    scores = clean(data["score"])
    if not scores:
        raise ValueError("The export contains no played charts.")
    try:
        rating = float(data.get("rating", 0))
        level = int(data.get("level", 0))
    except (TypeError, ValueError):
        raise ValueError("`rating`/`level` fields are malformed.")
    return {
        "name": str(data.get("name", "")),
        "rating": rating,
        "level": level,
        "last_played": str(data.get("lastPlayed", "")),
        "exported_at": str(data.get("updatedAt", "")),
        "app_version": str(data.get("appVersion", "")),
        "best": clean(data.get("best", []) or [], keep_zero=True),
        "new": clean(data.get("new", []) or [], keep_zero=True),
        "scores": scores,
    }


# -- CHUNITHM-NET sync: scrape the logged-in session into the same record shape ----
# Selectors follow chuni-penguin's parser (adapters/chunithm_net/parser.py).
NET_DIFFICULTIES = ("Basic", "Advanced", "Expert", "Master", "Ultima")
_JST = timezone(timedelta(hours=9))


def _last_part(text: str) -> str:
    return text.split("_")[-1].split(".")[0]


def parse_net_player(html: str) -> dict:
    """name / level / rating / last_played from /mobile/home/playerData."""
    soup = BeautifulSoup(html, "html.parser")
    name_el = soup.select_one(".player_name_in")
    lv_el = soup.select_one(".player_lv")
    digits = soup.select(".player_rating_num_block img")
    if name_el is None or lv_el is None or not digits:
        raise ValueError("Couldn't read your player data page.")
    rating = ""
    for img in digits:
        d = _last_part(img.get("src", ""))
        rating += "." if d == "comma" else d[1]
    last = ""
    last_el = soup.select_one(".player_lastplaydate_text")
    if last_el is not None:
        try:
            last = datetime.strptime(last_el.get_text(strip=True), "%Y/%m/%d %H:%M") \
                .replace(tzinfo=_JST).isoformat()
        except ValueError:
            pass
    return {"name": name_el.get_text(strip=True),
            "level": int(lv_el.get_text(strip=True).replace(",", "")),
            "rating": float(rating),
            "last_played": last}


def parse_net_music_list(html: str) -> list:
    """Played charts from a musicGenre/send<Diff> or ratingDetail page."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for form in soup.select("form"):
        box = form.select_one(".w388.musiclist_box")
        score_el = form.select_one(".play_musicdata_highscore .text_b")
        idx_el = form.select_one("input[name=idx]")
        title_el = form.select_one(".music_title, .musiclist_worldsend_title")
        if box is None or score_el is None or idx_el is None or title_el is None:
            continue
        diff = _last_part(" ".join(box.get("class", [])))
        if diff == "ultimate":
            diff = "ultima"
        if diff not in ("basic", "advanced", "expert", "master", "ultima"):
            continue                      # World's End etc.
        try:
            score = int(score_el.get_text(strip=True).replace(",", ""))
            idx = int(idx_el.get("value"))
        except (TypeError, ValueError):
            continue
        if score <= 0:
            continue
        icons = form.select_one(".play_musicdata_icon")
        has = (lambda frag: icons is not None and icons.select_one(
            "img[src*={}]".format(frag)) is not None)
        aj = has("alljustice")            # also matches alljusticecritical
        chain = 1 if has("fullchain2") else (2 if has("fullchain") else 0)
        out.append({"title": title_el.get_text(strip=True),
                    "difficulty": diff.capitalize(),
                    "score": score,
                    "fc": aj or has("fullcombo"),
                    "aj": aj,
                    "chain": chain,
                    "idx": idx})
    return out


def parse_net_rating_slots(html: str) -> list:
    """(idx, Difficulty) in B30/N20 order from a ratingDetail page."""
    return [(r["idx"], r["difficulty"]) for r in parse_net_music_list(html)]


def build_net_record(player: dict, scores: list, best_keys: list, new_keys: list) -> dict:
    by_key = {(s["idx"], s["difficulty"]): s for s in scores}
    return {
        "name": player["name"],
        "rating": player["rating"],
        "level": player["level"],
        "last_played": player["last_played"],
        "exported_at": datetime.now(_JST).isoformat(timespec="seconds"),
        "app_version": "chunithm-net sync",
        "best": [by_key[k] for k in best_keys if k in by_key],
        "new": [by_key[k] for k in new_keys if k in by_key],
        "scores": scores,
    }


class ChunithmCog(commands.Cog, name="chunithm"):
    def __init__(self, bot):
        self.bot = bot

    # -- storage ----------------------------------------------------------
    @staticmethod
    def _db() -> dict:
        """The whole score store, re-read only when the file changes
        (autocomplete calls this on every keystroke)."""
        try:
            st = os.stat(SCORES_FILE)
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        if sig is None or _db_cache["sig"] != sig:
            _db_cache["db"] = _load(SCORES_FILE, {"users": {}})
            _db_cache["sig"] = sig
        return _db_cache["db"]

    @classmethod
    def _get(cls, user_id: int) -> Optional[dict]:
        return cls._db().get("users", {}).get(str(user_id))

    @staticmethod
    def _put(user_id: int, record: Optional[dict]):
        with _lock:
            db = _load(SCORES_FILE, {"users": {}})
            db.setdefault("users", {})
            if record is None:
                db["users"].pop(str(user_id), None)
            else:
                db["users"][str(user_id)] = record
            _save(SCORES_FILE, db)

    # -- commands ---------------------------------------------------------
    @commands.hybrid_group(name="chunithm", invoke_without_command=True,
                           description="CHUNITHM tools: song charts and your imported scores")
    async def chunithm(self, ctx):
        language = _lang_for(ctx.guild)
        await ctx.send(t(language,
            "Subcommands: `upload`, `me`, `song`, `pattern`, `suggest`, `delete` (your scores); "
            "`rating`, `song_search` (song charts); `login`, `logout`, `token`, `sync` (CHUNITHM-NET).",
            "子指令：`upload`、`me`、`song`、`pattern`、`suggest`、`delete`（你的成績）；"
            "`rating`、`song_search`（曲目圖表）；`login`、`logout`、`token`、`sync`（CHUNITHM-NET）。"))

    @chunithm.command(name="upload", description="Upload your CHUNITHM player-data JSON")
    async def upload(self, ctx, file: discord.Attachment):
        await ctx.defer(ephemeral=True)
        if not file.filename.lower().endswith(".json"):
            return await ctx.send("Attach the `.json` export.", ephemeral=True)
        if file.size > MAX_BYTES:
            return await ctx.send("File too large (max 5 MB).", ephemeral=True)
        try:
            record = parse_export(await file.read())
        except ValueError as e:
            return await ctx.send(str(e), ephemeral=True)
        record["uploaded_by"] = ctx.author.id
        self._put(ctx.author.id, record)
        await ctx.send(
            "Saved **{}** — rating {:.2f}, {} played charts.".format(
                record["name"], record["rating"], len(record["scores"])),
            ephemeral=True)

    @chunithm.command(name="me", description="Summary of your imported scores")
    async def me(self, ctx):
        rec = self._get(ctx.author.id)
        if not rec:
            return await ctx.send("Nothing on file. Use `/chunithm sync` (after `/chunithm login`) or `/chunithm upload`.", ephemeral=True)
        sc = rec["scores"]
        sss = sum(1 for s in sc if s["score"] >= 1007500)
        ss = sum(1 for s in sc if s["score"] >= 1000000)
        fc = sum(1 for s in sc if s["fc"])
        aj = sum(1 for s in sc if s["aj"])
        e = discord.Embed(title=rec["name"] or "Player", color=EMBED_COLOR)
        e.add_field(name="Rating", value="{:.2f}".format(rec["rating"]))
        e.add_field(name="Level", value=str(rec["level"]))
        e.add_field(name="Played", value=str(len(sc)))
        e.add_field(name="SSS+ / SS+", value="{} / {}".format(sss, ss))
        e.add_field(name="FC / AJ", value="{} / {}".format(fc, aj))
        e.set_footer(text="Last played {} · exported {}".format(
            rec["last_played"][:10], rec["exported_at"][:10]))
        await ctx.send(embed=e, ephemeral=True)

    @chunithm.command(name="song", description="Your scores on a song")
    async def song(self, ctx, *, title: str):
        rec = self._get(ctx.author.id)
        if not rec:
            return await ctx.send("Nothing on file. Use `/chunithm sync` (after `/chunithm login`) or `/chunithm upload`.", ephemeral=True)
        q = normalize(title)
        hits = {}
        for s in rec["scores"]:
            if q in normalize(s["title"]):
                hits.setdefault(s["title"], []).append(s)
        if not hits:
            return await ctx.send("No played chart matches that.", ephemeral=True)
        exact = {t: r for t, r in hits.items() if normalize(t) == q}
        if exact:                      # an autocomplete pick: show only that song
            hits = exact
        lines = []
        for t, rows in list(hits.items())[:5]:
            rows.sort(key=lambda r: DIFFS.index(r["difficulty"])
                      if r["difficulty"] in DIFFS else 9)
            lines.append("**{}**".format(discord.utils.escape_markdown(t)))
            for r in rows:
                tag = " AJ" if r["aj"] else (" FC" if r["fc"] else "")
                lines.append("  {} — {:,}{}".format(r["difficulty"], r["score"], tag))
        if len(hits) > 5:
            lines.append("…and {} more titles. Narrow the search.".format(len(hits) - 5))
        await ctx.send("\n".join(lines)[:1900], ephemeral=True)


    @song.autocomplete("title")
    async def song_title_autocomplete(self, interaction: discord.Interaction, current: str):
        rec = self._get(interaction.user.id)
        if not rec:
            return []
        q = normalize(current)
        starts, contains, seen = [], [], set()
        for s in rec["scores"]:
            t = s["title"]
            if t in seen:
                continue
            seen.add(t)
            n = normalize(t)
            if not q or n.startswith(q):
                starts.append(t)
            elif q in n:
                contains.append(t)
        return [discord.app_commands.Choice(name=t[:100], value=t[:100])
                for t in (starts + contains)[:25]]

    @chunithm.command(name="pattern", description="Which note patterns you are strong/weak at")
    async def pattern(self, ctx, scope: Literal["all", "best", "rating"] = "all"):
        rec = self._get(ctx.author.id)
        if not rec:
            return await ctx.send("Nothing on file. Use `/chunithm sync` (after `/chunithm login`) or `/chunithm upload`.", ephemeral=True)
        table = load_chart_table()
        if table is None:
            return await ctx.send("Chart table missing. Run `tools/build_chart_tags.py`.",
                                  ephemeral=True)
        rep = chart_analysis.pattern_report(rec, table, scope)
        if "error" in rep:
            return await ctx.send(rep["error"], ephemeral=True)
        rows = rep["rows"]
        strong = [r for r in rows if r["shrunk"] > 0][:6]
        weak = [r for r in reversed(rows) if r["shrunk"] < 0][:6]
        fmt = lambda r: "`{}` {:+.2f} (n={})".format(r["tag"], r["shrunk"], r["n"])
        e = discord.Embed(
            title="Pattern skill — " + SCOPE_LABEL[scope], color=EMBED_COLOR,
            description="{} charts ≥14 ({} tagged). Value = rating gain vs your own "
                        "average at that difficulty.".format(rep["charts"], rep["tagged"]))
        e.add_field(name="Strong", value=_field([fmt(r) for r in strong]), inline=True)
        e.add_field(name="Weak", value=_field([fmt(r) for r in weak]), inline=True)
        e.set_footer(text="Tags with <{} charts are hidden. Relative to your own level; "
                          "you choose what you play.".format(rep["min_n"]))
        await ctx.send(embed=e, ephemeral=True)

    @chunithm.command(name="suggest", description="Songs to push your rating / to practice")
    async def suggest(self, ctx):
        rec = self._get(ctx.author.id)
        if not rec:
            return await ctx.send("Nothing on file. Use `/chunithm sync` (after `/chunithm login`) or `/chunithm upload`.", ephemeral=True)
        table = load_chart_table()
        if table is None:
            return await ctx.send("Chart table missing. Run `tools/build_chart_tags.py`.",
                                  ephemeral=True)
        res = chart_analysis.suggestions(rec, table)
        if "error" in res:
            return await ctx.send(res["error"], ephemeral=True)
        push_lines = []
        for r in res["push"]:
            c = r["chart"]
            now = "{:,}".format(r["current"]) if r["current"] else "unplayed"
            push_lines.append("**{}** ({} {})\n{} → ~{:,} · rating {:+.4f} · {}{}".format(
                _esc(c["title"]), c["difficulty"], c["const"], now, r["target"],
                r["delta"], ", ".join(r["tags"]), _note_line(c)))
        prac_lines = []
        for r in res["improve"]:
            c = r["chart"]
            now = "{:,}".format(r["current"]) if r["current"] else "unplayed"
            prac_lines.append("**{}** ({} {})\n{} · weak: {}{}".format(
                _esc(c["title"]), c["difficulty"], c["const"], now, ", ".join(r["tags"]),
                _note_line(c)))
        e = discord.Embed(title="Suggestions", color=EMBED_COLOR)
        e.add_field(name="Push your rating (your strong patterns)",
                    value=_field(push_lines) if push_lines else "Nothing clearly beats your B30/N20 floor.",
                    inline=False)
        e.add_field(name="Practice (your weak patterns)",
                    value=_field(prac_lines) if prac_lines else "No weak-pattern charts near your level.",
                    inline=False)
        foot = ("Aim scores are estimates. A chart is assumed to count in B30 "
                "unless the song is already in your N20.")
        if not 16.0 <= rec["rating"] <= 17.25:
            foot += " The sheet's notes come from 16.0–17.25 players."
        e.set_footer(text=foot)
        await ctx.send(embed=e, ephemeral=True)

    @chunithm.command(name="delete", description="Delete your stored scores")
    async def delete(self, ctx):
        if self._get(ctx.author.id) is None:
            return await ctx.send("Nothing on file.", ephemeral=True)
        self._put(ctx.author.id, None)
        await ctx.send("Your score data was deleted.", ephemeral=True)

    # -- song charts (engine in cogs/rating_cog.py) -------------------------
    def _rating_cog(self):
        """RatingCog, or None if it failed to load (missing packages/data)."""
        return self.bot.get_cog("rating")

    async def _rating_unavailable(self, ctx):
        await ctx.send(t(_lang_for(ctx.guild),
            "The CHUNITHM chart feature isn't available right now (its packages or data "
            "failed to load; check the bot log).",
            "CHUNITHM 圖表功能目前無法使用（套件或資料載入失敗；請查看機器人日誌）。"),
            ephemeral=True)

    async def cog_command_error(self, ctx, error):
        # rating / song_search used to be their own commands with their own
        # error handling (cooldown, ChartError, bad arguments); keep it.
        if ctx.command is not None and ctx.command.name in ("rating", "song_search"):
            rating = self._rating_cog()
            if rating is not None:
                await rating.handle_error(ctx, error)

    @chunithm.command(
        name="rating",
        description="Look up a song and draw its player-population rating chart (only the chosen chart is processed).")
    @app_commands.describe(
        song="Song title or fragment (slash: pick from autocomplete)",
        difficulty="Chart difficulty",
        detailed="Full score and player range instead of the default SS-to-MAX focus")
    @app_commands.choices(difficulty=[app_commands.Choice(name=d, value=d) for d in DIFFICULTIES])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rating(self, ctx, song: str, difficulty: str = "MASTER", detailed: bool = False):
        """Look up a song and draw its player-population rating chart."""
        rating = self._rating_cog()
        if rating is None:
            return await self._rating_unavailable(ctx)
        await rating.rating(ctx, song, difficulty, detailed)

    @rating.autocomplete("song")
    async def rating_song_autocomplete(self, interaction: discord.Interaction, current: str):
        rating = self._rating_cog()
        if rating is None:
            return []
        return await rating.song_autocomplete(interaction, current)

    @chunithm.command(
        name="song_search",
        description="Search CHUNITHM songs and their available difficulties (no chart is generated).")
    @app_commands.describe(query="Song title, alias or fragment")
    async def song_search(self, ctx, *, query: str):
        """Search CHUNITHM songs and their available difficulties."""
        rating = self._rating_cog()
        if rating is None:
            return await self._rating_unavailable(ctx)
        await rating.song_search(ctx, query)

    # -- CHUNITHM-NET login (logic in cogs/login_cog.py) --------------------
    def _login_cog(self):
        """LoginCog, or None if it failed to load."""
        return self.bot.get_cog("login")

    async def _login_unavailable(self, ctx):
        await ctx.send(t(_lang_for(ctx.guild),
            "The login feature isn't available right now (it failed to load; check the bot log).",
            "登入功能目前無法使用（載入失敗；請查看機器人日誌）。"), ephemeral=True)

    @chunithm.command(
        name="login",
        description="Link your CHUNITHM-NET account (a cookie session is saved).")
    @app_commands.describe(clal="Only if you already have a token; leave empty to get instructions")
    async def login(self, ctx, clal: Optional[str] = None):
        """Link your CHUNITHM-NET account. Use it in my DMs or as /chunithm login."""
        cog = self._login_cog()
        if cog is None:
            return await self._login_unavailable(ctx)
        await cog.login(ctx, clal)

    @chunithm.command(name="logout", description="Remove your saved CHUNITHM-NET login")
    @app_commands.describe(invalidate="Also sign out of CHUNITHM-NET so the token stops working")
    async def logout(self, ctx, invalidate: bool = False):
        cog = self._login_cog()
        if cog is None:
            return await self._login_unavailable(ctx)
        await cog.logout(ctx, invalidate)

    @chunithm.command(name="token", description="Show your saved CHUNITHM-NET token (private)")
    async def token(self, ctx):
        cog = self._login_cog()
        if cog is None:
            return await self._login_unavailable(ctx)
        await cog.token(ctx)

    @chunithm.command(name="sync", description="Import your scores straight from CHUNITHM-NET")
    async def sync(self, ctx):
        """Fetch your scores with your saved CHUNITHM-NET login (replaces any upload)."""
        language = _lang_for(ctx.guild)
        login = self._login_cog()
        if login is None:
            return await self._login_unavailable(ctx)
        from cogs.login_cog import NotLoggedIn
        await ctx.defer(ephemeral=True)
        status = await ctx.send(t(language,
            "Fetching your scores from CHUNITHM-NET… this takes a minute.",
            "正在從 CHUNITHM-NET 取得你的成績…需要約一分鐘。"), ephemeral=True)
        try:
            async with login.session(ctx.author.id) as client:
                async def get(path):
                    return (await client.request("GET", path)).text

                player = parse_net_player(await get("/mobile/home/playerData"))
                best_keys = parse_net_rating_slots(
                    await get("/mobile/home/playerData/ratingDetailBest/"))
                new_keys = parse_net_rating_slots(
                    await get("/mobile/home/playerData/ratingDetailRecent/"))
                token = None
                for c in net.load_jar(client.lwp_cookie_jar):
                    if c.name == "_t" and c.domain.lstrip(".") == net.NET_HOST:
                        token = c.value
                if token is None:
                    raise net.SessionError("CHUNITHM-NET session token missing; try again.")
                scores = []
                for diff in NET_DIFFICULTIES:
                    resp = await client.request(
                        "POST", "/mobile/record/musicGenre/send" + diff,
                        data={"genre": "99", "token": token})
                    scores.extend(parse_net_music_list(resp.text))
        except NotLoggedIn:
            return await ctx.send(t(language,
                "You're not logged in. Use `/chunithm login` first.",
                "你尚未登入，請先使用 `/chunithm login`。"), ephemeral=True)
        except (net.SessionError, ValueError) as e:
            return await ctx.send("Sync failed: {}".format(e), ephemeral=True)
        if not scores:
            return await ctx.send("CHUNITHM-NET returned no played charts.", ephemeral=True)
        record = build_net_record(player, scores, best_keys, new_keys)
        record["uploaded_by"] = ctx.author.id
        self._put(ctx.author.id, record)
        await ctx.send(t(language,
            "Synced **{}** — rating {:.2f}, {} played charts. Try `/chunithm me`.",
            "已同步 **{}** — Rating {:.2f}，共 {} 張已遊玩譜面。可使用 `/chunithm me`。").format(
                record["name"], record["rating"], len(scores)), ephemeral=True)


async def setup(bot):
    await bot.add_cog(ChunithmCog(bot))
