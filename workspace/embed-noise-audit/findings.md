# Account and Song Embed Noise Audit

Read-only source audit. No code, runtime state, environment variables, or database data were changed or read; no tests were run.

## Safe, small content changes

- **Wrong-song control should be red.** `chuni_penguin/ui/song_details.py:909-910`, `SongDetailsView.correct_song`, declares `找錯歌／重搜` without a style, so Discord renders the default secondary button. Set it explicitly to `discord.ButtonStyle.danger`.
- **Remove only the duplicated Account status and source name.** In `chuni_penguin/ui/panel.py:907-921`, the `帳號連結` field already lists every source and its status. The `目前來源` field repeats `selected.status`; keep its selected label, last-sync time, and notice, but omit the repeated status. The footer repeats `selected.label`; keep the owner/expiry text and omit that label. This preserves every source status and the selected source.
- **Trim the duplicated Song search list carefully.** `SongCandidateSelect` (`chuni_penguin/ui/song_search.py:21-45`) already lists candidates and their `record_label`/artist details. `SongSearchView._before_start` (`:60-81`) repeats the first ten titles and descriptions. The embed also has distinct `可切換` variant labels (`:65-72`), which fully identify normal/WE variants, catalog version, and availability through `record_label`. If shortening this embed, keep the search query and those unique variant labels; remove the repeated candidate title/description blocks. Do not remove duplicate-candidate IDs in select options (`:35-40`), since they disambiguate same-title results.
- **No other safe removals stood out in the requested song cards.** `SongInfoEmbed` has a song-level version and optional per-chart versions (`chuni_penguin/ui/song_info.py:50-80,115-165`), which describe different scopes. `ChartCardEmbed`'s category, difficulty/level/constant and border/note fields (`components/chart_card_embed.py:51-105`), `ScoreCardEmbed`'s rank/lamp/score/rating/OP/attempt and detailed judgement fields (`components/score_card_embed.py:39-185`), and `SongDetailsView`'s footer/navigation context (`song_details.py:785-800`) add distinct information. Preserve the explicit normal/WE, stars/rank, version, availability/status, source labels, and source IDs/provenance; community chart annotations at `song_details.py:393-424` include the source, review method, sheet, and row.

## Close-control coverage

The shared Context path is not currently a shared close-button path. `PenguinViewMixin.start` calls `attach_navigation` (`chuni_penguin/ui/_base.py:44-64`); `prepare_response` can also attach panel navigation (`ui/panel.py:525-550`). Neither adds Close. `attach_navigation` returns early for `_panel_own_navigation` views (`panel.py:499-521`), so it will not cover the custom controls rebuilt by `SongDetailsView._controls` (`song_details.py:685-783`). A common close control needs to be added independently of panel navigation, and custom views that rebuild their children need to re-add it after each rebuild.

Account and Song interactive embed views with no explicit close control:

- `PanelView` (`ui/panel.py:618-749`) backs `show_account` and `show_panel` (`:828-930`); its Account and Account Settings views have no close action.
- `SongDetailsView` (`ui/song_details.py:277-280,685-783`) backs the main Song card and also has no close action.
- `SongSearchView` (`ui/song_search.py:51-59,109-121`) displays an embed with its candidate selector and has no explicit close action.
- `EmbedPaginationView` uses the shared paginator (`ui/embeds.py:27-29`, `ui/_pagination.py:117-250`) without Close. Account panel routes use it for imported-source score pages and Best 50 (`ui/panel.py:1194-1206,1233-1245`).
- `AliasOfferView` is a raw `discord.ui.View` and its ephemeral embed is sent directly with `ctx.interaction.followup.send` (`ui/song_search.py:199-269,324-352`), bypassing Context. Its `略過` button already removes the view and replaces the prompt (`:264-269`), so it is a close-equivalent; reuse or relabel that action instead of adding a duplicate button.

Direct interaction responses and other raw views to account for:

- `PanelView` is sent directly with an embed from `V2Players.send_result` for roots outside its `respond_or_edit` branch (`chuni_penguin/cogs/v2_players.py:144-183`); the `followup.send` at `:181-183` bypasses Context and the view has no close control. The common Context helper will not catch this path.
- Account destructive-confirmation embeds are sent/edited directly with `ConfirmPanel` (`ui/panel.py:1537-1563`), but the view already has `取消` (`:1731-1739`), an exit action.
- The private Song review embed is installed by direct `interaction.edit_original_response` (`ui/chart_reviews.py:77-95`). `ReviewPrivateView` already has `關閉` (`:173-179`); `ReviewDeleteView` already has `取消` (`:384-388`).
- If “every Embed” means bot-wide, additional raw-view outputs outside Account/Song need coverage: Linked VERSE detail embeds with link-only controls (`cogs/chunithm/linked_verse.py:121-150`), Kamaitachi import/deorphan embeds with link-only controls (`cogs/chunithm/kamaitachi.py:488-515,554-576`), the voice now-playing embed with a favorite button (`cogs/voice.py:363-385`), and game-end recap embeds with retry buttons sent via `channel.send` (`cogs/gaming/states/end_game.py:66-155`). The last path also bypasses Context. These are raw/in-line `discord.ui.View` instances, not subclasses of the shared view base.

Existing `CopypastaMenu`/`CopypastaInfoMenu` embed views already provide Close. `RatingChoiceView` sends plain prompt text with a selector, not an embed (`cogs/chunithm/rating_analysis.py:72-83`); it does not need an embed close control.

- Additional raw view path: `ui/profile.py:329-346` replaces a timed-out `ProfileView` embed's controls with a raw persistent `discord.ui.View` for friend-code actions; that timeout replacement has no Close button. Cover it if the close requirement is bot-wide.
