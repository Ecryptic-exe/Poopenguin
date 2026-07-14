"""
Guild-configuration commands: !setperms/​/setperms (grant a role access
to a channel), !autoreact/​/autoreact (configure/disable per-channel
auto-reactions), and !lang/​/lang (toggle the guild's help/response
language).

These mirror their old on_message.py behaviour exactly, including the
fact that !lang and !autoreact are NOT admin-restricted in the
original bot (only !setperms is) - that's preserved here rather than
"fixed", since tightening permissions wasn't asked for and could lock
people out of features they're used to.

Converted to commands.hybrid_command so the text and slash versions
share one implementation. channel_id/role_id are kept as plain string
IDs (rather than discord.TextChannel/discord.Role converters) so
!setperms keeps accepting raw IDs exactly like before - see GAPS.md
for the option of upgrading these to native slash channel/role pickers.

Also adds `!sync` (bot owner only, prefix-only - see its docstring for
why it's not a hybrid/slash command) for manually re-registering slash
commands with Discord on demand, instead of only on bot startup.
"""
import discord
from discord.ext import commands

from config import load_settings, save_settings
from i18n import t, get_guild_language


class AdminCog(commands.Cog, name="admin"):
    def __init__(self, bot):
        self.bot = bot

    def _lang(self, ctx):
        return get_guild_language(load_settings(), ctx.guild.id)

    @commands.hybrid_command(
        name="setperms",
        description="Grants permissions to a specific role in a specific channel (Admin only).")
    @discord.app_commands.describe(
        channel_id="ID of the channel to grant access to",
        role_id="ID of the role to grant access to")
    @commands.has_permissions(administrator=True)
    async def setperms(self, ctx, channel_id: str, role_id: str):
        """Grants permissions to a specific role in a specific channel (Admin only)."""
        language = self._lang(ctx)

        try:
            channel_id_int = int(channel_id)
            role_id_int = int(role_id)
        except ValueError:
            await ctx.send(t(language,
                "Channel ID and Role ID must be valid numbers.",
                "頻道 ID 和角色 ID 必須是有效數字。"))
            return

        channel = ctx.guild.get_channel_or_thread(channel_id_int)
        role = ctx.guild.get_role(role_id_int)

        if not channel:
            await ctx.send(t(language,
                "Channel not found. Please provide a valid channel ID.",
                "找不到頻道。請提供有效的頻道 ID。"))
            return
        if not role:
            await ctx.send(t(language,
                "Role not found. Please provide a valid role ID.",
                "找不到角色。請提供有效的角色 ID。"))
            return

        if not ctx.guild.me.guild_permissions.manage_channels:
            await ctx.send(t(language,
                "I don't have permission to manage channels!",
                "我沒有管理頻道的權限！"))
            return

        try:
            permissions = {
                role: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )
            }
            await channel.edit(overwrites={**channel.overwrites, **permissions})
            await ctx.send(t(language,
                f"Permissions updated: Role {role.mention} can now view and send messages in {channel.mention}.",
                f"權限已更新：角色 {role.mention} 現在可以在 {channel.mention} 中查看和發送消息。"))
        except discord.Forbidden:
            await ctx.send(t(language,
                "Failed to update permissions. Missing permissions.",
                "無法更新權限。缺少權限。"))
        except Exception as e:
            await ctx.send(t(language, f"An error occurred: {str(e)}", f"發生錯誤：{str(e)}"))

    @commands.hybrid_command(
        name="autoreact",
        description="Sets or disables auto-reactions for messages in this channel.")
    @discord.app_commands.describe(
        emoji="Emoji to auto-react with (omit to disable auto-reactions in this channel)",
        user="Only auto-react to this user's messages (omit for everyone in the channel)")
    async def autoreact(self, ctx, emoji: str = None, user: discord.Member = None):
        """Sets an emoji to auto-react to messages from a specific user or all messages in the channel, or disables auto-reactions."""
        settings = load_settings()
        language = get_guild_language(settings, ctx.guild.id)
        channel_id = str(ctx.channel.id)

        if not ctx.guild.me.guild_permissions.add_reactions:
            await ctx.send(t(language, "I don't have permission to add reactions!", "我沒有添加反應的權限！"))
            return

        settings["autoreact"] = settings.get("autoreact", {})

        if emoji:
            try:
                temp_message = await ctx.send("Testing emoji...")
                await temp_message.add_reaction(emoji)
                await temp_message.delete()
            except discord.HTTPException:
                await ctx.send(t(language, "Invalid emoji. Please provide a valid emoji.", "無效的表情符號。請提供有效的表情符號。"))
                return
            except discord.Forbidden:
                await ctx.send(t(language, "I don't have permission to add reactions!", "我沒有添加反應的權限！"))
                return

            settings["autoreact"][channel_id] = {
                "emoji": emoji,
                "user_id": str(user.id) if user else None,
            }
            save_settings(settings)
            if user:
                await ctx.send(t(language,
                    f"Auto-reactions enabled: Will react with {emoji} to messages from {user.mention} in {ctx.channel.mention}.",
                    f"自動反應已啟用：將對 {ctx.channel.mention} 中 {user.mention} 的消息使用 {emoji} 進行反應。"))
            else:
                await ctx.send(t(language,
                    f"Auto-reactions enabled: Will react with {emoji} to all messages in {ctx.channel.mention}.",
                    f"自動反應已啟用：將對 {ctx.channel.mention} 中的所有消息使用 {emoji} 進行反應。"))
        else:
            if channel_id in settings["autoreact"]:
                del settings["autoreact"][channel_id]
                save_settings(settings)
                await ctx.send(t(language,
                    f"Auto-reactions disabled in {ctx.channel.mention}.",
                    f"已在 {ctx.channel.mention} 中禁用自動反應。"))
            else:
                await ctx.send(t(language,
                    f"Auto-reactions were not enabled in {ctx.channel.mention}.",
                    f"{ctx.channel.mention} 中尚未啟用自動反應。"))

    @commands.hybrid_command(
        name="lang",
        description="Toggles the language of the bot's help panel between English and Chinese.")
    async def lang(self, ctx):
        """Toggles the language of the bot's help panel between English and Chinese."""
        settings = load_settings()
        guild_id = str(ctx.guild.id)
        current_language = get_guild_language(settings, guild_id)
        new_language = "chinese" if current_language == "english" else "english"
        settings["language"] = settings.get("language", {})
        settings["language"][guild_id] = new_language
        save_settings(settings)
        await ctx.send(t(new_language, f"Language set to {new_language.capitalize()}.", "語言設置為中文。"))

    @commands.command(name="sync", hidden=True)
    @commands.is_owner()
    async def sync(self, ctx, scope: str = "guild"):
        """Manually re-registers slash ("/") commands with Discord (bot owner only).

        Deliberately a plain !-only text command, NOT a hybrid/slash
        command: it's the tool you reach for when slash commands
        AREN'T showing up yet, so it can't depend on slash commands
        already being synced to be reachable, and it's an
        owner-maintenance tool rather than something regular server
        admins need in the slash picker.

        VTO.py already runs this once automatically in on_ready(), so
        you normally won't need this - it's here for the times you add
        or edit a command and don't want to wait for a bot restart:

          !sync             - sync to *this* server only (instant)
          !sync global      - sync everywhere the bot is (can take up
                              to ~1h the first time Discord's cache
                              picks it up)

        Discord keeps global-registered and guild-registered commands
        as entirely separate entries. If this bot has ever been synced
        to BOTH scopes (e.g. `!sync global` was run at some point, and
        `!sync`/`!sync guild` at another, possibly from an older code
        version), every command that exists in both scopes shows up
        TWICE in the "/" picker - and if the two syncs happened on
        different code versions, the two copies can even show
        different descriptions, or a command that's since been removed
        from the code (like an old standalone subcommand that got
        turned into a text-only alias) can keep lingering as a ghost
        entry in whichever scope hasn't been re-synced since. Use these
        to clean that up:

          !sync clearguild  - wipes every command registered directly
                              to *this* server, without touching global
                              ones (instant). Follow with `!sync guild`
                              (or `!sync`) to re-register the current
                              set to this server only.
          !sync clearglobal - wipes every globally-registered command,
                              without touching this server's
                              guild-scoped ones (can take up to ~1h to
                              disappear from Discord's cache
                              everywhere). Follow with `!sync global`
                              to re-register the current set globally.
        """
        scope = scope.lower()
        if scope not in ("guild", "global", "clearguild", "clearglobal"):
            await ctx.send("Usage: `!sync [guild|global|clearguild|clearglobal]` (default: `guild`).")
            return

        try:
            if scope == "guild":
                if not ctx.guild:
                    await ctx.send("`!sync guild` has to be run inside a server.")
                    return
                self.bot.tree.copy_global_to(guild=ctx.guild)
                synced = await self.bot.tree.sync(guild=ctx.guild)
                await ctx.send(f"Synced {len(synced)} slash command(s) to this server (should be instant).")
            elif scope == "global":
                synced = await self.bot.tree.sync()
                await ctx.send(
                    f"Synced {len(synced)} slash command(s) globally "
                    f"(can take up to ~1h to appear everywhere the first time).")
            elif scope == "clearguild":
                if not ctx.guild:
                    await ctx.send("`!sync clearguild` has to be run inside a server.")
                    return
                self.bot.tree.clear_commands(guild=ctx.guild)
                await self.bot.tree.sync(guild=ctx.guild)
                await ctx.send(
                    "Cleared every command registered directly to this server "
                    "(should be instant). If commands still show up twice here, "
                    "the duplicates are coming from the global command list - "
                    "try `!sync clearglobal` too, then `!sync global` to "
                    "re-register the current set.")
            else:  # clearglobal
                self.bot.tree.clear_commands(guild=None)
                await self.bot.tree.sync()
                await ctx.send(
                    "Cleared every globally-registered command (can take up "
                    "to ~1h to disappear from Discord's cache everywhere). "
                    "If this server still shows duplicates once that's "
                    "propagated, they were guild-scoped here - try "
                    "`!sync clearguild`, then `!sync guild` to re-register.")
        except discord.Forbidden:
            await ctx.send(
                "Sync failed: I'm missing the `applications.commands` OAuth2 scope. "
                "See GAPS.md - the bot needs to be re-invited with that scope checked.")
        except discord.HTTPException as e:
            await ctx.send(f"Sync failed: {e}")


async def setup(bot):
    await bot.add_cog(AdminCog(bot))