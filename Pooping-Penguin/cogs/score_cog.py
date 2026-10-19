"""
CHUNITHM score import: /score upload|me|song|pattern|suggest|delete (and !score ...).

Users export their player data JSON (the chunithm-player-data_*.json file),
attach it to /score upload, and the bot stores a compact copy keyed by their
Discord user id in data/chunithm_scores.json.

/score pattern and /score suggest also need data/chart_tags.json, built from the
community chart sheet with tools/build_chart_tags.py (analysis: chart_analysis.py).

Only entries with score > 0 are kept (the export lists every chart, ~6400 rows,
most of them unplayed). One record per user; re-uploading replaces it.
Python 3.9 safe (no `X | Y` unions).
"""
import json
import logging
import os
import threading
from typing import Literal, Optional

import discord
from discord.ext import commands

import chart_analysis
from config import DATA_DIR, _load, _save
from ratingbot.catalog import normalize   # stdlib only

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


class ScoreCog(commands.Cog):
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
    @commands.hybrid_group(name="score", invoke_without_command=True,
                           description="Your imported CHUNITHM scores")
    async def score(self, ctx):
        await ctx.send("Subcommands: `upload`, `me`, `song`, `pattern`, `suggest`, `delete`.")

    @score.command(name="upload", description="Upload your CHUNITHM player-data JSON")
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

    @score.command(name="me", description="Summary of your imported scores")
    async def me(self, ctx):
        rec = self._get(ctx.author.id)
        if not rec:
            return await ctx.send("Nothing on file. Use `/score upload`.", ephemeral=True)
        sc = rec["scores"]
        sss = sum(1 for s in sc if s["score"] >= 1007500)
        ss = sum(1 for s in sc if s["score"] >= 1000000)
        fc = sum(1 for s in sc if s["fc"])
        aj = sum(1 for s in sc if s["aj"])
        e = discord.Embed(title=rec["name"] or "Player", color=EMBED_COLOR)
        e.add_field(name="Rating", value="{:.2f}".format(rec["rating"]))
        e.add_field(name="Level", value=str(rec["level"]))
        e.add_field(name="Played", value=str(len(sc)))
        e.add_field(name="SSS / SS", value="{} / {}".format(sss, ss))
        e.add_field(name="FC / AJ", value="{} / {}".format(fc, aj))
        e.set_footer(text="Last played {} · exported {}".format(
            rec["last_played"][:10], rec["exported_at"][:10]))
        await ctx.send(embed=e, ephemeral=True)

    @score.command(name="song", description="Your scores on a song")
    async def song(self, ctx, *, title: str):
        rec = self._get(ctx.author.id)
        if not rec:
            return await ctx.send("Nothing on file. Use `/score upload`.", ephemeral=True)
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

    @score.command(name="pattern", description="Which note patterns you are strong/weak at")
    async def pattern(self, ctx, scope: Literal["all", "best", "rating"] = "all"):
        rec = self._get(ctx.author.id)
        if not rec:
            return await ctx.send("Nothing on file. Use `/score upload`.", ephemeral=True)
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

    @score.command(name="suggest", description="Songs to push your rating / to practice")
    async def suggest(self, ctx):
        rec = self._get(ctx.author.id)
        if not rec:
            return await ctx.send("Nothing on file. Use `/score upload`.", ephemeral=True)
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

    @score.command(name="delete", description="Delete your stored scores")
    async def delete(self, ctx):
        if self._get(ctx.author.id) is None:
            return await ctx.send("Nothing on file.", ephemeral=True)
        self._put(ctx.author.id, None)
        await ctx.send("Your score data was deleted.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ScoreCog(bot))
