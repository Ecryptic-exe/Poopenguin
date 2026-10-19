# Embed Noise Audit Plan

- [x] Inspect the requested Account and Song embed renderers for repeated or unnecessary content and controls.
  - Result: Identified the default-style wrong-song button, duplicated Account status/source label, and duplicated Song search list while retaining unique variant information.
  - Changed: `workspace/embed-noise-audit/findings.md`
  - Verified: Reviewed line-numbered source in `ui/panel.py`, `song_details.py`, `song_info.py`, `components/chart_card_embed.py`, `components/score_card_embed.py`, and `song_search.py`.
  - Notes: No other safe card-content removals; version labels and chart/source metadata describe distinct scopes and should remain.
- [x] Inventory Embed-producing `discord.ui.View` paths that bypass shared Context helpers, with focus on direct interaction responses and close controls.
  - Result: Listed Account/Song views missing Close, close-equivalent controls already present, direct interaction-response bypasses, and additional raw-view paths if scope is bot-wide.
  - Changed: `workspace/embed-noise-audit/findings.md`
  - Verified: Traced `PenguinViewMixin.start`, `prepare_response`, `attach_navigation`, and each identified send/edit path to its view.
  - Notes: Custom `SongDetailsView` rebuilds its children on render; a common Close must be re-added in that rebuild path. Alias “略過”, confirmation “取消”, and review “關閉” already serve as exits.
- [x] Record exact locations, preservation constraints, and practical removal recommendations in `findings.md`.
  - Result: Findings include exact file/line locations and distinguish duplicate text from version/status/source details that must be preserved.
  - Changed: `workspace/embed-noise-audit/findings.md`, `workspace/embed-noise-audit/PLAN.md`
  - Verified: Read back the two work-record files; this was a source-only audit, so no runtime checks or tests were appropriate.
  - Notes: No code edits, environment/DB access, bot restart, or external messages.
