"""C's chart/selection presentation, with explicit unified-source provenance."""

import discord

from chuni_penguin.services.rating_analysis import RatingAnalysisService
from chuni_penguin.services.rating_search import RatingSong
from chuni_penguin.ui.owner_view import edit_view_message

from .dismiss import CloseView, ensure_close


async def deliver_rating_chart(
    interaction: discord.Interaction,
    song: RatingSong,
    difficulty: str,
    service: RatingAnalysisService,
    *,
    already_deferred: bool = False,
    detailed: bool = False,
):
    if not already_deferred:
        await interaction.response.defer(thinking=True)
    try:
        result = await service.get_chart(song, difficulty, detailed=detailed)
        lines = result.get("numeric_summary", {}).get(
            "compact_lines", ["個人差：資料不足", "吃分：資料不足"]
        )
        embed = discord.Embed(
            title=f"{song.title}／{difficulty}"[:256],
            url=result["source_url"],
            color=0x008C83,
            description="\n".join(lines),
        )
        scope = service.search.snapshot.provenance.scope
        observed = str(result.get("statistics_observed_at") or "Unknown")[:50]
        embed.set_footer(
            text=f"Catalog: {song.ref.namespace} / {scope.region} / {scope.game_version}; statistics: Chunirec, region/version unknown, collected {observed}. Reviewed association; canonical ID mapping not checked."[
                :2048
            ]
        )
        filename = f"rating_{song.song_id}_{difficulty}.png"
        embed.set_image(url="attachment://" + filename)
        attachment = discord.File(result["image"], filename=filename)
        try:
            await edit_view_message(
                interaction,
                content=None,
                embed=embed,
                attachments=[attachment],
                view=CloseView(interaction.user.id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            attachment.close()
    except service.error_type as error:
        await edit_view_message(
            interaction,
            content=str(error),
            embed=None,
            attachments=[],
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class RatingChoiceView(discord.ui.View):
    def __init__(
        self,
        songs: list[RatingSong],
        difficulty: str,
        requester: int,
        service: RatingAnalysisService,
        *,
        detailed: bool = False,
    ):
        super().__init__(timeout=120)
        self.requester, self.service = requester, service
        self.difficulty, self.detailed = difficulty, detailed
        self.songs = {song.song_id: song for song in songs}
        self.started = False
        self.select = discord.ui.Select(
            placeholder="選擇要產圖的曲目",
            options=[
                discord.SelectOption(
                    label=song.title[:100],
                    value=song.song_id,
                    description=f"{difficulty} · ID {song.song_id}",
                )
                for song in songs
            ],
        )
        self.select.callback = self.selected
        self.add_item(self.select)
        ensure_close(self, requester)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester:
            await interaction.response.send_message(
                "請使用自己的 /rating_chart 指令選曲。", ephemeral=True
            )
            return False
        return True

    async def selected(self, interaction: discord.Interaction):
        if self.started:
            await interaction.response.send_message(
                "這個選曲已開始處理。", ephemeral=True
            )
            return
        self.started = True
        song = self.songs[self.select.values[0]]
        await interaction.response.defer()
        await edit_view_message(
            interaction,
            content=f"正在處理 {discord.utils.escape_markdown(song.title)}／{self.difficulty}…",
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.stop()
        await deliver_rating_chart(
            interaction,
            song,
            self.difficulty,
            self.service,
            already_deferred=True,
            detailed=self.detailed,
        )
