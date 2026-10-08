"""
Feed tracker: /feed add | remove | list | check   (and !feed ...)

Watches social media accounts and posts new items to a Discord channel.
Currently supported: Bluesky (public API, no key or login needed).

Adding another platform later = write one Provider subclass (see
BlueskyProvider) and register it in PROVIDERS. Nothing else changes.

State lives in data/feeds.json:
  {"<guild_id>": [{"platform", "account", "channel_id", "last_seen"}, ...]}
`last_seen` is the newest post timestamp already handled, so restarts
never repost, and a freshly added feed does not flood the channel.

Admin only (Manage Server). Python 3.9 safe. Needs: requests.
"""
import asyncio
import json
import logging
import os
import re
from typing import Dict, List, Literal, Optional

import discord
import requests
from discord import app_commands
from discord.ext import commands, tasks

from config import DATA_DIR

logger = logging.getLogger("DiscordBot")

FEEDS_FILE = os.path.join(DATA_DIR, "feeds.json")
POLL_MINUTES = 5
MAX_POSTS_PER_POLL = 5        # safety cap per feed per poll
EMBED_COLOR = 0x1185FE        # Bluesky blue


# ----------------------------------------------------------------------
# Providers
# ----------------------------------------------------------------------
class Post:
    """Normalized post, the same shape for every platform."""
    def __init__(self, uid, created, url, author, text, image=None, kind="post"):
        self.uid = uid            # unique id
        self.created = created    # ISO-8601 string (sortable)
        self.url = url
        self.author = author
        self.text = text
        self.image = image        # first image URL or None
        self.kind = kind          # "post" | "repost"


class Provider:
    name = ""

    def parse_account(self, raw: str) -> Optional[str]:
        """Return a normalized account id from a URL/handle, or None."""
        raise NotImplementedError

    def fetch(self, account: str) -> List[Post]:
        """Blocking. Return recent posts, newest first. Raise on error."""
        raise NotImplementedError


class BlueskyProvider(Provider):
    name = "bluesky"
    API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
    _HANDLE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")

    def parse_account(self, raw):
        raw = raw.strip().lstrip("@")
        m = re.search(r"bsky\.app/profile/([^/?#\s]+)", raw)
        if m:
            raw = m.group(1)
        raw = raw.lower()
        return raw if self._HANDLE.match(raw) else None

    def fetch(self, account):
        r = requests.get(
            self.API,
            params={"actor": account, "limit": 20,
                    "filter": "posts_no_replies"},
            timeout=15,
        )
        r.raise_for_status()
        posts = []
        for item in r.json().get("feed", []):
            p = item["post"]
            rec = p.get("record", {})
            handle = p["author"]["handle"]
            rkey = p["uri"].rsplit("/", 1)[-1]
            repost = item.get("reason", {}).get("$type", "").endswith("reasonRepost")
            created = (item["reason"]["indexedAt"] if repost
                       else rec.get("createdAt") or p.get("indexedAt", ""))
            image = None
            emb = p.get("embed") or {}
            if emb.get("images"):
                image = emb["images"][0].get("fullsize")
            elif emb.get("thumbnail"):
                image = emb["thumbnail"]
            posts.append(Post(
                uid=p["uri"] + ("#rp" if repost else ""),
                created=created,
                url="https://bsky.app/profile/%s/post/%s" % (handle, rkey),
                author=p["author"].get("displayName") or handle,
                text=rec.get("text", ""),
                image=image,
                kind="repost" if repost else "post",
            ))
        posts.sort(key=lambda x: x.created, reverse=True)
        return posts


PROVIDERS = {"bluesky": BlueskyProvider()}


# ----------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------
def _load_feeds() -> Dict[str, list]:
    try:
        with open(FEEDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_feeds(data):
    os.makedirs(os.path.dirname(FEEDS_FILE), exist_ok=True)
    tmp = FEEDS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FEEDS_FILE)


# ----------------------------------------------------------------------
# Cog
# ----------------------------------------------------------------------
class FeedCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.poll.start()

    def cog_unload(self):
        self.poll.cancel()

    # ---------------- commands ----------------
    @commands.hybrid_group(name="feed", invoke_without_command=True,
                           description="Track social media accounts.")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def feed(self, ctx):
        await ctx.send("Subcommands: `add`, `remove`, `list`, `check`.\n"
                       "Example: `!feed add https://bsky.app/profile/performaien.bsky.social #news`")

    @feed.command(name="add", description="Track an account in a channel.")
    @app_commands.describe(
        account="Profile URL or handle, e.g. performaien.bsky.social",
        channel="Where new posts go (default: this channel)",
        platform="Which site the account is on")
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def feed_add(self, ctx, account: str,
                       channel: Optional[discord.TextChannel] = None,
                       platform: Literal["bluesky"] = "bluesky"):
        # When adding a platform: add its name to the Literal above too.
        provider = PROVIDERS.get(platform.lower())
        if not provider:
            return await ctx.send("Supported platforms: " + ", ".join(PROVIDERS))
        acct = provider.parse_account(account)
        if not acct:
            return await ctx.send("Couldn't read that account. Use a profile URL or handle.")
        channel = channel or ctx.channel

        await ctx.defer()
        try:
            posts = await asyncio.to_thread(provider.fetch, acct)
        except Exception as e:
            return await ctx.send("Couldn't fetch that account: %s" % e)

        data = _load_feeds()
        entries = data.setdefault(str(ctx.guild.id), [])
        if any(e["platform"] == provider.name and e["account"] == acct
               and e["channel_id"] == channel.id for e in entries):
            return await ctx.send("Already tracking that here.")
        entries.append({
            "platform": provider.name,
            "account": acct,
            "channel_id": channel.id,
            # Start from the newest existing post: only NEW posts get sent.
            "last_seen": posts[0].created if posts else "",
        })
        _save_feeds(data)
        await ctx.send("Tracking **%s** (%s) in %s. New posts only, checked every %d min."
                       % (acct, provider.name, channel.mention, POLL_MINUTES))

    @feed.command(name="remove", description="Stop tracking an account.")
    @app_commands.describe(account="Profile URL or handle you added")
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def feed_remove(self, ctx, account: str):
        data = _load_feeds()
        entries = data.get(str(ctx.guild.id), [])
        keep = []
        for e in entries:
            prov = PROVIDERS.get(e["platform"])
            target = prov.parse_account(account) if prov else None
            if not (target and e["account"] == target):
                keep.append(e)
        if len(keep) == len(entries):
            return await ctx.send("Not tracking that account.")
        data[str(ctx.guild.id)] = keep
        _save_feeds(data)
        await ctx.send("Removed.")

    @feed.command(name="list", description="Show tracked accounts.")
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def feed_list(self, ctx):
        entries = _load_feeds().get(str(ctx.guild.id), [])
        if not entries:
            return await ctx.send("Nothing tracked.", ephemeral=True)
        lines = ["`%s` **%s** -> <#%s>" % (e["platform"], e["account"], e["channel_id"])
                 for e in entries]
        await ctx.send("\n".join(lines), ephemeral=True)

    @feed.command(name="check", description="Poll now instead of waiting.")
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def feed_check(self, ctx):
        await ctx.defer()
        await self._poll_once()
        await ctx.send("Checked.")

    # ---------------- polling ----------------
    @tasks.loop(minutes=POLL_MINUTES)
    async def poll(self):
        await self._poll_once()

    @poll.before_loop
    async def _before_poll(self):
        await self.bot.wait_until_ready()

    async def _poll_once(self):
        data = _load_feeds()
        changed = False
        for guild_id, entries in data.items():
            for e in entries:
                provider = PROVIDERS.get(e["platform"])
                if not provider:
                    continue
                try:
                    posts = await asyncio.to_thread(provider.fetch, e["account"])
                except Exception as err:
                    logger.warning("feed fetch failed (%s/%s): %s",
                                   e["platform"], e["account"], err)
                    continue
                fresh = [p for p in posts if p.created > e.get("last_seen", "")]
                if not fresh:
                    continue
                channel = self.bot.get_channel(e["channel_id"])
                # Oldest first so the channel reads in order.
                for p in list(reversed(fresh))[-MAX_POSTS_PER_POLL:]:
                    if channel:
                        try:
                            await channel.send(embed=self._embed(p, provider))
                        except discord.HTTPException as err:
                            logger.warning("feed send failed: %s", err)
                e["last_seen"] = fresh[0].created
                changed = True
        if changed:
            _save_feeds(data)

    @staticmethod
    def _embed(p: Post, provider: Provider) -> discord.Embed:
        title = ("%s reposted" if p.kind == "repost" else "New post from %s") % p.author
        emb = discord.Embed(title=title, url=p.url,
                            description=p.text[:4000] or None, color=EMBED_COLOR)
        if p.image:
            emb.set_image(url=p.image)
        emb.set_footer(text=provider.name.capitalize())
        return emb


async def setup(bot):
    await bot.add_cog(FeedCog(bot))
