"""Discord slash commands; searches stay offline and charts are demand-driven."""
from __future__ import annotations

import argparse
import inspect
import json
import logging
import os
import traceback
from datetime import datetime, timezone

import discord
from discord import app_commands

from .catalog import DIFFICULTIES
from .config import Settings, ensure_catalog
from .service import ChartService, ChartError, atomic_json


async def deliver_chart(interaction, song, difficulty, service, already_deferred=False, *, detailed=False):
    if not already_deferred:
        await interaction.response.defer(thinking=True)
    try:
        result = await service.get_chart(song, difficulty, detailed=detailed)
        title = f"{song.title}／{difficulty}"
        lines = result.get('numeric_summary', {}).get('compact_lines', ['個人差：資料不足', '吃分：資料不足'])
        embed = discord.Embed(title=title[:256], url=result['source_url'], color=0x008C83,
            description='\n'.join(lines))
        filename = f'rating_{song.song_id}_{difficulty}.png'
        embed.set_image(url='attachment://'+filename)
        attachment = discord.File(result['image'], filename=filename)
        try:
            await interaction.edit_original_response(content=None, embed=embed, attachments=[attachment], view=None)
        finally:
            attachment.close()
    except ChartError as error:
        await interaction.edit_original_response(content=str(error), embed=None, attachments=[], view=None)


class SongChoiceView(discord.ui.View):
    def __init__(self, songs, difficulty, requester, service, detailed=False):
        super().__init__(timeout=120)
        self.requester = requester
        self.service = service
        self.difficulty = difficulty
        self.detailed = detailed
        self.songs = {s.song_id: s for s in songs}
        self.started = False
        select = discord.ui.Select(placeholder='選擇要產圖的曲目', options=[
            discord.SelectOption(label=s.title[:100], value=s.song_id,
                description=f'{difficulty} · ID {s.song_id}') for s in songs])
        select.callback = self.selected
        self.select = select
        self.add_item(select)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester:
            await interaction.response.send_message('請使用自己的 /rating 指令選曲。', ephemeral=True)
            return False
        return True

    async def selected(self, interaction):
        if self.started:
            await interaction.response.send_message('這個選曲已開始處理。', ephemeral=True)
            return
        self.started = True
        song = self.songs[self.select.values[0]]
        await interaction.response.defer()
        await interaction.edit_original_response(content=f'正在處理 {discord.utils.escape_markdown(song.title)}／{self.difficulty}…', view=None)
        self.stop()
        await deliver_chart(interaction, song, self.difficulty, self.service, already_deferred=True, detailed=self.detailed)


class RatingClient(discord.Client):
    def __init__(self, settings, catalog):
        super().__init__(intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.catalog = catalog
        self.service = ChartService(settings)
        self.tree = app_commands.CommandTree(self)
        register_commands(self)

    async def setup_hook(self):
        def payload(command):
            # discord.py 2.3 uses to_dict(); later versions require the tree.
            return command.to_dict(self.tree) if inspect.signature(command.to_dict).parameters else command.to_dict()
        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            for command in self.tree.get_commands(guild=guild):
                await self.http.upsert_guild_command(self.application_id, guild.id, payload=payload(command))
        else:
            for command in self.tree.get_commands():
                await self.http.upsert_global_command(self.application_id, payload=payload(command))

    async def on_ready(self):
        atomic_json(self.settings.cache/'runtime_status.json', {'status': 'ready', 'pid': os.getpid(),
            'at': datetime.now(timezone.utc).isoformat(), 'application_id': self.application_id,
            'commands': [c.name for c in self.tree.get_commands()], 'songs': len(self.catalog.songs),
            'charts': self.catalog.metadata['charts_count'], 'startup_chart_requests': 0,
            'chart_views': {'default': 'focus', 'detailed': 'full'}, 'compact_plot': True,
            'numeric_decisions': 'python'})
        logging.getLogger('ratingbot').info('Bot ready; songs=%d, charts=%d',
            len(self.catalog.songs), self.catalog.metadata['charts_count'])

    async def close(self):
        await self.service.close()
        await super().close()


def register_commands(client):
    choices = [app_commands.Choice(name=d, value=d) for d in DIFFICULTIES]

    @client.tree.command(name='rating', description='搜尋曲目並產生玩家分布圖；只處理所選譜面')
    @app_commands.describe(song='曲名或片段；可從自動完成選擇', difficulty='譜面難度',
        detailed='顯示完整分數與玩家範圍；預設為 SS 以上、定數 +1.00～+2.15')
    @app_commands.rename(detailed='詳細')
    @app_commands.choices(difficulty=choices)
    @app_commands.checks.cooldown(1, 5, key=lambda interaction: interaction.user.id)
    async def rating(interaction: discord.Interaction, song: str, difficulty: str = 'MASTER', detailed: bool = False):
        exact = client.catalog.exact_matches(song, difficulty)
        if len(exact) == 1:
            await deliver_chart(interaction, exact[0], difficulty, client.service, detailed=detailed)
            return
        other_difficulties = client.catalog.exact_matches(song)
        if other_difficulties and not exact:
            available = sorted({d for s in other_difficulties for d in s.difficulties}, key=DIFFICULTIES.index)
            await interaction.response.send_message('提供的曲目資料未列此難度。可選：'+', '.join(available), ephemeral=True)
            return
        candidates = client.catalog.search(song, difficulty, 25)
        if not candidates:
            await interaction.response.send_message('沒有找到符合的曲目。', ephemeral=True)
            return
        view = SongChoiceView([r['song'] for r in candidates], difficulty, interaction.user.id, client.service, detailed=detailed)
        await interaction.response.send_message('請選擇要查看的曲目：', view=view)

    @rating.autocomplete('song')
    async def complete_song(interaction: discord.Interaction, current: str):
        difficulty = getattr(interaction.namespace, 'difficulty', 'MASTER') or 'MASTER'
        return [app_commands.Choice(name=r['song'].title[:100], value='id:'+r['song'].song_id)
                for r in client.catalog.search(current, difficulty, 25)]

    @client.tree.command(name='song_search', description='搜尋曲目與可用難度；不抓取統計或產圖')
    @app_commands.describe(query='曲名、別名或片段')
    async def song_search(interaction: discord.Interaction, query: str):
        rows = client.catalog.search(query, limit=10)
        content = '\n'.join(f"{i}. **{discord.utils.escape_markdown(r['song'].title)}** · "
                f"{', '.join(r['song'].difficulties)} · `id:{r['song'].song_id}`"
                for i,r in enumerate(rows, 1)) or '沒有找到符合的曲目。'
        await interaction.response.send_message(content[:1950], ephemeral=True)

    @client.tree.error
    async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f'請稍等 {error.retry_after:.0f} 秒再查詢。'
        else:
            original = getattr(error, 'original', error)
            details = ''.join(traceback.format_exception(type(original), original, original.__traceback__))
            if client.settings.token:
                details = details.replace(client.settings.token, '[REDACTED]')
            logging.getLogger('ratingbot').error('Command failure (%s):\n%s', type(error).__name__, details)
            message = '指令處理失敗；請稍後再試。'
        if interaction.response.is_done():
            await interaction.edit_original_response(content=message, embed=None, attachments=[], view=None)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Offline startup check; never logs into Discord')
    args = parser.parse_args(argv)
    settings = Settings.load()
    settings.validate()
    catalog = ensure_catalog(settings.root)
    if args.check:
        print(json.dumps({'status': 'ready_for_token' if not settings.token else 'configured',
            'token_present': bool(settings.token), 'songs': len(catalog.songs),
            'charts': catalog.metadata['charts_count'], 'commands': ['rating', 'song_search'],
            'llm_required': False, 'discord_connected': False, 'charts_generated': 0}, ensure_ascii=False))
        return
    if not settings.token:
        raise SystemExit('請在 rating_bot/.env 設定 TOKEN，再啟動 run_bot.py。')
    client = RatingClient(settings, catalog)
    try:
        client.run(settings.token)
    except discord.LoginFailure:
        raise SystemExit('Discord 登入失敗：請確認 .env 的 bot token。') from None
