# Song / Account Embed UX Inventory Work Plan

- [x] Trace the current Song and Account interaction trees from commands, views, callbacks, and relevant services.
  - Result: Mapped the slash entrypoints, owner-bound panel/session routes, Song detail/search views, Account source flows, and supporting services from current source.
  - Changed: `workspace/embed-ux/inventory.md`
  - Verified: Cross-checked entrypoint, panel, Song, Account, alias, and review source paths before writing.
- [x] Inventory visible content, buttons, menus, selectable options, dynamic conditions, and context retention for every level.
  - Result: Added Traditional Chinese tables for entrypoints, shared navigation, account/source states, Song search and tabs, forms, pagination, conditionals, and preserved route state.
  - Changed: `workspace/embed-ux/inventory.md`
  - Verified: Re-opened current view definitions and route renderers; marked this-round work separately from baseline behavior.
- [x] Audit Account routes including settings, sources, sync, B50/recent/source scores, login/link/privacy/unlink/delete/upload.
  - Result: Documented NET/Kamaitachi/Chunirec/Chunilib differences, login and link steps, sharing defaults, sync results, import limits, confirmation paths, and source-specific dead ends.
  - Changed: `workspace/embed-ux/inventory.md`
  - Verified: Cross-checked `panel.py`, `v2_players.py`, login/auth, recent/B50 and player-source paths.
- [x] Audit Song routes including search/filter/tags, detail/statistics/links/aliases/compare, processing, and short-query collision/recovery behavior.
  - Result: Documented both current Song Discovery and legacy fuzzy fallback, same-title candidate handling, re-search, aliases, all chart tabs, filter/tag lists, separate rating commands, and processing status.
  - Changed: `workspace/embed-ux/inventory.md`
  - Verified: Cross-checked current `song_search.py`, `song_discovery.py`, `song_details.py`, `catalog.py`, `rating_chart.py`, and related commands.
- [x] Record duplicate controls, dead ends, anonymous review flow/background form, and clearly labeled UX proposals.
  - Result: Recorded public-response privacy exposure, unavailable compare route, hidden jacket branch, list-only find/tags, conditional account actions, review thresholds/private fields, duplicate controls, and a distinct proposal appendix.
  - Changed: `workspace/embed-ux/inventory.md`
  - Verified: Current review UI and service enforce private owner reads, anonymized aggregates, and five-person suppression in source; broader behavior remains labeled pending validation where appropriate.
- [x] Cross-check claims against current source and finalize the inventory with source references; keep changes scoped to this plan and inventory.
  - Result: Finalized the source-linked inventory and recorded the implementation status distinction: current tree, this-round pending validation, and unimplemented proposals.
  - Changed: `workspace/embed-ux/inventory.md`, `workspace/embed-ux/PLAN.md`
  - Verified: Documentation-only cross-check completed. No code, configuration, database, runtime process, or root planning files were changed; no tests were run because this task was a read-only source audit.

## Comprehensive UX Proposal

- [x] Correct inventory claims that do not match current route grouping or command registration.
  - Result: Corrected the false statement that Song More comparison was rejected by the cross-group guard; `group_for("compare")` maps it to Song, so the single-song compare route retains the selected title, difficulty, and source. Also clarified that the startup snapshot has 13 slash roots and the `song_search` / `rating_chart` cogs are disabled, so their code definitions are not current slash entrypoints. The earlier audit result is retained as history; this item records the correction.
  - Changed: `workspace/embed-ux/inventory.md`
  - Verified: Cross-checked `group_for`, `navigate`, Song More routing, `reports/startup.json`, and both command cog decorators.
- [x] Turn the source inventory into a polished Traditional Chinese Song and Account interaction proposal with per-level content, controls, menus, and dynamic conditions.
  - Result: Created a proposal with Account and Song overview tables plus detailed source, search, single-song, More menu, review, and recovery flows.
  - Changed: `docs/EMBED_UX_PROPOSAL.md`
  - Verified: Cross-checked the menu and field choices against `panel.py`, `song_details.py`, `song_search.py`, and `chart_reviews.py`.
- [x] Specify public same-message panels, private credentials and forms, state retention, back/recovery paths, and search/loading/error/empty/expired states; keep Account account-focused, Song single-song-focused, and Poopenguin unchanged.
  - Result: Recorded the requested public panel boundary, private sensitive flows, exact Account/Song menu conditions, state preservation, and recovery behavior; kept Poopenguin outside the proposed changes.
  - Changed: `docs/EMBED_UX_PROPOSAL.md`
  - Verified: Compared the proposal with current owner-bound routing and source-specific conditions.
- [x] Separate deployed behavior from proposed layout; reconcile review privacy and guild-alias rules.
  - Result: Marked this-round behavior as deployed for testing and the full UX layout as not yet applied. Recorded 591 passing regression checks, 21:50 restart validation, the unchanged 28-table/schema data snapshot, and the empty new review tables; Discord visual acceptance remains outstanding.
  - Changed: `docs/EMBED_UX_PROPOSAL.md`, `workspace/embed-ux/inventory.md`
  - Verified: Used the runtime and live migration evidence reported in `reports/embed-features-runtime-20261016.json` and `reports/review-migration-live-20261016.json`; no application tests were run by this documentation subtask.
- [x] Cross-check references and finalize this scoped record without changing app code or the root plan.
  - Result: Finalized the UX proposal and corrected inventory; no source code, database, runtime process, or root planning file was changed by this subtask.
  - Changed: `docs/EMBED_UX_PROPOSAL.md`, `workspace/embed-ux/inventory.md`, `workspace/embed-ux/PLAN.md`
  - Verified: Reviewed the final inventory corrections and proposal status against current source and startup evidence.
