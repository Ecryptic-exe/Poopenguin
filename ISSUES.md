# Engineering constraints and resolutions

## Clean checkout omitted the reviewed annotation baseline

**Problem**
The first staged Git archive passed 697 tests but failed the actual reviewed-source case and fresh annotation import with `INPUT_MISSING:review_base`.

**Root Cause**
The publication allowlist included the reviewed workbook, review manifest and BM25 mapping, but omitted `outputs/chart-mapping-20261015/ChunithmChart_with_ID.xlsx`. The importer deliberately verifies this baseline against the review manifest before accepting changes. Existing local files hid the packaging dependency.

**Solution**
Include the existing baseline workbook in the narrow output allowlist. Preserve its original bytes and provenance checks; its SHA-256 matches the reviewed manifest (`f49528945007780994a2dc886a6f39314b5a86cc7262e556e037740e2f9a8b5a`).

**Verification**
The initial clean-archive failure reproduced in both tests and actual import. After including the baseline, the corrected Git-index archive passes all 698 tests (80.25s). Sequential fresh bootstrap creates 2,288 songs / 8,312 charts, imports all 834 annotations and passes offline startup with zero network attempts. The original running database is untouched.

**Prevention / Notes**
Validate an archive made from the Git index, rather than only the original working directory, before publishing. Required reviewed inputs are exceptions to the otherwise ignored output directory; do not weaken the importer's hash or path checks.

## Score distribution was demo-only, and focus cropping could hide the empirical line

**Problem**
User reports the promised score-distribution page is not implemented. Normal selected songs show missing-snapshot text or jump back to overview after difficulty changes, despite the menu being visible and generic tests passing.

**Root Cause**
`Phase1Charts.analysis` returned None unless a chart appeared in a twelve-row MASTER-only snapshot index; its C worker was explicitly offline. Song difficulty recovery also used membership in that index as an availability gate. Prior validation only rendered those bundled samples and did not cover live acquisition or a real non-bundled chart through the Embed attachment path. After acquisition was added, visual inspection of actual Gengaozo charts showed that C's focus range clipped 82/100 and 107/115 eligible/raw points respectively and hid the empirical mean line.

**Solution**
Collect the selected public chart by its exact catalog Chunirec ID, validate source title/difficulty/native ID and preserve source evidence, then seed unchanged C ChartService/engine. Production enables this lazily; offline startup remains network-free. Use C's full view, 24-hour verified disk cache, same-key work sharing, bounded queue, cancellation/reaping and short failure cooldown. Retain the old twelve snapshots only as labelled, accurately dated fallback. Remove the index-presence UI gate and attach generated PNG bytes as `score-distribution.png`.

**Verification**
Fifteen new service/UI cases cover acquisition outside the index, identity mismatch rejection, cache/no-fetch, fallback/date retention, work sharing, cancellation, real menu/difficulty callbacks and image lifecycle. Seventy-two focused regressions and the complete 698-test suite pass. Actual Gengaozo MASTER/EXPERT and Air ULTIMA, all absent from the old index, produce valid full-view PNGs from their real public statistics. All three images were inspected; empirical lines are visible. The actual cached service output also passes SDK Embed/image serialization with source acquisition forbidden on repeat render. Results/images are recorded in `reports/distribution-live-20261019.json` and `outputs/distribution-check-20261019/`. Restarted controller 16532 is healthy at 23:37:15 +08, with 21 modules/Gateway, zero runtime/FK errors and all 834 annotations preserved.

**Prevention / Notes**
A visible menu, a graceful empty state, a sample-only render and a large unrelated regression count do not establish feature availability. Future acceptance must include an ordinary chart outside all seed fixtures, source acquisition/provenance, actual image inspection and the real message attachment path. C's source-model sample/constant constraints remain explicit; player review thresholds are unrelated.

## Remote command schemas include harmless metadata normalizations

**Problem**
The /score registration preflights stopped before any writes with unexpected differences for unchanged permissions and View your score commands.

**Root Cause**
Discord's current permission-command options and the SDK serialization contained exactly the same `channel_types` numeric filters in a different order. Discord also includes an empty description for context menus, while the SDK omits this inapplicable field. The recursive comparison treated these as functional differences. Real option/choice order still matters and must not be sorted wholesale.

**Solution**
Normalize only each `channel_types` list and omit the inapplicable context-menu description/options from semantic comparison. Preserve ordering of commands, subcommands and choices. Continue rejecting actual unrelated schema differences and unknown commands before registration, with all current scopes backed up first. Existing registration metadata takes precedence for commands that already exist.

**Verification**
Readonly diff showed only six reordered indexes in two channel type filters and the context menu's absent-versus-empty description. Both rejected preflights performed no writes. Three unit cases verify normalization and detection of actual required/filter/choice changes. Successful registration reread and validated complete current schemas, preserved existing scopes and metadata (zero before/proposed metadata differences), and confirmed unchanged Poopenguin schemas. Final full regression passes 683 tests.

## Select-only Song cards must reserve a row for Close

**Problem**
The requested select-only Song interface can need function, difficulty, review-writing, player-group and pagination controls at the same time. Independent selects for all five would occupy every Discord row, leaving no place for the required Close action.

**Root Cause**
A select consumes a complete row, and a normal Discord View has only five rows. A layout valid on short empty-state cards can overflow when community data is paginated and player groups become available.

**Solution**
Combine review writing and available player-group statistics in one contextual select. Keep navigation as a select, only show source controls on scores, and reserve the remaining row for Close. Search and alias actions use contextual selects as well.

**Verification**
A real discord.py grouped two-page review view uses five valid rows with one Close, canonical chart ownership and a working group-transition callback. Other Song tabs, both entry landings, three Account sources and Chunirec region switches pass SDK row checks.

## Moving extensions must preserve module identity and configuration keys

**Problem**
Poopenguin non-CHUNITHM functions moved from `legacy_b` to `cogs/poopenguin`, while old env settings, extension lifecycle names, imports and tests can still refer to the old path. Loading two copies would split shared globals and break monkeypatch/state expectations; serializing both aliases would inflate module counts.

**Root Cause**
Python treats different import paths as different module identities unless explicitly aliased. Extension specs also use their full module paths as configuration and report keys.

**Solution**
Keep old modules as `sys.modules` identity shims, add explicit legacy aliases to canonical extension specs, and resolve aliases for selection/lifecycle/status lookup. Iterate and serialize only canonical extension status rows. Persistent JSON paths remain `data/poopenguin`.

**Verification**
All 15 legacy imports are the exact corresponding canonical module object. Tests verify all nine specs, old env-key selection, defaults, status get/item compatibility and canonical-only JSON; default/optional behavioral and existing phase1 regressions pass. A stale serialized legacy-key assertion was intentionally updated to the new canonical name.

**2026-10-19 full-suite follow-up**
The first full run passed 677 tests but failed the import-identity check after preceding canonical extension lifecycle tests. Earlier unload/reload removed the canonical module while leaving a legacy `sys.modules` alias attached to the prior object. The isolated import test therefore passed but was insufficient; the failed run is retained in `reports/select-ui-full-before-alias-fix-20261019.txt`.

ExtensionManager now synchronizes module aliases and both canonical/legacy parent-package attributes after load, reload, failed-reload rollback and unload, only for specs that declare aliases. A real discord.py Bot regression verifies canonical and legacy reimports, stale-reference cleanup and a single canonical extension registration. The resulting 48-case package/phase1/Poopenguin regression and final full 683-case integration pass before deployment. Existing core A modules without aliases keep their previous lifecycle behavior.

## Dismissal must distinguish a private follow-up from the public panel session

**Problem**
Adding a close button only to landing views would miss direct private replies, controls rebuilt during pagination/gacha, processing placeholders and result-only cards. Deleting through a channel Message also fails for ephemeral responses. A delayed result could recreate a deleted panel, and treating an alias reminder's context as the public session could close the wrong interface.

**Root Cause**
Components belong to messages, whereas a context can also be reused by an independent private follow-up. Views have different lifecycles; closing a message does not automatically cancel login waiting, release readers or invalidate late responses. The normal response fallback intentionally creates a new message after NotFound, which is inappropriate for an explicitly closed session.

**Solution**
Use one owner-bound dismissal helper and component-response webhook deletion for both visibilities, with a cleared-message fallback. Inject a single close action through Context/base-view entry points and after custom control rebuilds; retain existing native close actions. Only close the session when its message anchor matches; private alias/review follow-ups use independent dismissal. Mark main sessions closed before cleanup, cancel pending login, deactivate/release readers and stop old views. Context responders reject late output; cancelled initial delivery also releases its new reader. Private form results keep close-only controls and closed public parents are not refreshed.

**Verification**
Actual Discord Interaction adapter tests verify type-6 acknowledgement and webhook deletion for public and ephemeral messages, with channel deletion forbidden. Lifecycle tests cover owner denial, idempotent injection, pending login cancellation, active reader release, late-response rejection, cancelled initial delivery, private/public isolation, delete fallback and regular/layout timeout close controls. Existing transport/ownership/feature regressions remain included in the full suite.

**Prevention / Notes**
Reserve a component slot for close. Removed the redundant Song position button and chart-result page indicator; position is still in the footer. Components-v2 timeout replacements preserve layout content. Closing a UI never calls player/review deletion services. The close action applies to the complete message when it contains multiple Embeds.

## Discord validates decorated button rows before dynamic controls are rebuilt

**Problem**
Adding the new Song retry button caused View construction to fail with `item would not fit at row 3`, even for normal pages which never display a retry button.

**Root Cause**
Discord's View constructor instantiates and places every decorated button before `_controls()` can clear unused items. Five existing decorated buttons already occupied row 3; declaring retry on that same row exceeded the limit during construction.

**Solution**
Declare retry on the spare initial row 4. Move it to row 3 only when rebuilding a retryable error page, where unused controls have already been removed. Preserve the five-row limit and only show retry for failed content.

**Verification**
Full 617-case suite passes, including Song normal/error pages, row limits and navigation. Real read-only catalog rendering and SDK component serialization preserve the gengaozo version labels and valid controls.

**Prevention / Notes**
Account for both initial decorator placement and final dynamic layout when adding Discord controls.

## Returning from related panels must recreate the Song reader with its exact state

**Problem**
Leaving Song for alias management, comparison or a filter result closes the previous view and its network reader. Reusing that view or searching the title again can lose the chosen WE version, difficulty, tab or source.

**Root Cause**
View lifecycle and navigation state were tied together. A title alone cannot identify a specific variant; fuzzy comparison can also match unrelated records.

**Solution**
Keep a session snapshot of the actual Song records, selected variant/difficulty/tab/source/page and query. Return by creating a new reader/view from that snapshot. Filter/tag selections revalidate the exact Chart ID against the read-only catalog and retain the original conditions/page. Contextual alias additions and Song comparisons use the selected canonical Song ID.

**Verification**
Integration tests pass for filter page 2 → selected chart → original page/conditions, tag → selected chart → same tag, contextual one-field alias → original tab/source, and comparison which excludes unrelated source rows and returns to the exact Song state. The full 617-case suite passes.

**Prevention / Notes**
Keep UI snapshots inside the owner-bound session; they do not merge or rewrite catalog IDs. Private account forms refresh a public parent through the safe status reader only; regression coverage verifies that submitted and encrypted credentials never enter that public response.

## One song was presented as four ID candidates because WE rows were counted as songs

**User clarification / follow-up**
Multiple versions are expected; the UX failure is making players identify them from raw IDs. The follow-up adds shared labels to candidate text, a complete overview version list, difficulty options and every selected-page footer. Labels include normal/WE type and stars, catalog version and availability; the currently selected overview version has a ▶ marker. Matching/grouping and all canonical associations are unchanged by this labeling follow-up. Actual gengaozo rendering is captured in `reports/song-version-labels-20261016.json` and checked against the existing readonly catalog.

**Problem**
Searching `gengaozo` opened a candidate prompt containing four Song IDs instead of one song card. The rows were normal G e n g a o z o (506) plus WORLD'S END records 8108, 8137 and 8203. Other affected titles include Invitation, The ether, volcanic and Random.

**Root Cause**
The new discovery pipeline ranked titles, loaded every same-title Song row, then treated each row as an independent candidate for its ambiguity check. The canonical catalog intentionally stores WE variants under separate IDs; a record-count check therefore mistook one composition with variants for genuinely different songs. Exposing IDs did not explain the musical difference.

**Solution**
Group rows by normalized title and artist only when the family has at most one ordinary row; preserve distinct artists and multiple ordinary editions as genuine candidates. Open the ordinary record by default, while specific existing WE aliases retain their target. Carry every original variant into the card's difficulty selector with human-readable WE type/stars/version/availability. The selector changes both actual song index and difficulty, so scores and player-review buttons retain the correct chart IDs. Wrong-song recovery excludes the entire previous family. IDs are a last-resort discriminator for otherwise identical genuine candidates.

**Verification**
Read-only verification covers all 248 duplicate catalog titles, preserving exactly the expected canonical record sets and genuine exact-title/global-alias collisions. The reported `gengaozo` query yields one candidate, normal 506, with three WE variants. Maximum actual difficulty menu size is 10. Seven new grouping tests cover normal/WE-only families, genuine collisions, alias targets and corrections; SDK UI testing switches normal → WE → normal and checks the WE review's actual chart/song IDs and component limits.

**Prevention / Notes**
Do not merge or delete canonical rows to simplify the UI. Review/score associations continue using actual Chart IDs. A first verifier attempt exposed the legitimate Dreaming → DRE4M1N9 global alias, so verification now includes exact aliases rather than incorrectly forcing every title to one candidate. None of this requires a schema change or rewriting alias/player data.

## A saved process record and successful startup log can outlive the bot

**Problem**
At this feature cutover, saved controller PID 18576 no longer existed and no matching workspace launcher was running, although its old log still contained a successful Gateway/startup record.

**Root Cause**
The old runtime checker only inspected persisted logs and database health, so it could not distinguish historical success from a currently alive bot. The reason the earlier process terminated is unresolved; no termination cause is inferred from the stale record.

**Solution**
Runtime checks now query the saved PID and require both a living process and matching workspace Python executable/launcher arguments in addition to the existing log/database checks. Restart validates identity before stopping a process and safely starts a fresh controller when the saved PID is absent; unrelated processes are not stopped.

**Verification**
The authorized startup created controller 27232. The latest runtime report verifies its current process identity, Gateway/startup, 21 extensions, empty stderr, zero logged errors and healthy DB. A nonexistent-PID probe verifies that stale records fail the process check.

**Prevention / Notes**
An on-demand health report is a point-in-time observation, not an automatic watchdog. No recurring monitor, scheduled task or new OS service was installed.

## Delayed response wrappers must not follow a copied context into private actions

**Problem**
A delayed processing helper temporarily replaces a context's responder with a closure. A shallow context copy made while that helper is active could retain the responder bound to the public parent, even after a sensitive action creates a separate private session.

**Root Cause**
Copying a bound method or closure does not rebind its captured context. The response wrapper also needs to finish any already-started progress edit before a final response, or a late progress edit can overwrite the result.

**Solution**
Keep the underlying responder explicitly, rebind bound methods to the cloned context, and clear processing state in the clone. The helper delays feedback by 0.6 seconds, suppresses nested helpers, cancels an unstarted timer and drains an in-flight progress edit before any final response. Song transitions also check request revision under the message edit lock. Wrong-song recovery retires the previous Song view before searching again.

**Verification**
Targeted regressions verify public-to-private cloning while processing is active, same-public-message progress/result ordering, fast completion without flashing, nested helpers, exceptions/cancellation and stale Song results. The 76-case panel/Song/processing/form run passes.

**Prevention / Notes**
Use `component_context` / `sensitive_context` for related interactions. A private follow-up such as alias confirmation or reviewer forms must never bind itself as the public parent anchor.

## Anonymous reviews need private ownership and historical background snapshots

**2026-10-16 policy follow-up**
The user now requests automatic reuse of user data and temporarily removes the five-review minimum. Current public aggregates appear from one review, including one-person groups; missing background values do not create fabricated groups. Native sources read the owner's actual in-game Rating with a bounded account context, imported sources read only the selected owner's saved payload, and existing reviewer fields are reused. Main level, experience and skills are not inferable from account level/play count and remain `未提供` when absent. Background confirmation is optional. No identity/comment exposure, schema migration or rewriting of historical background snapshots is introduced. The original five-person/complement policy described below is historical and is no longer active.

**Problem**
Publishing reviewer background beside individual comments could identify players. Updating a player's current profile could also change how old reviews are interpreted, while simultaneous edit windows could overwrite newer opinions.

**Root Cause**
Public anonymity, private edit ownership and historical analysis are distinct requirements. Removing the owner ID would prevent reliable editing/deletion; joining every old review to today's background would lose the background at submission time.

**Solution**
Store a private Discord owner key, one current review per canonical chart/user, a profile snapshot for each submission and private revision history. Validate the expected revision inside the serial writer transaction. Public queries select only numerical ratings and background snapshots, never account IDs, comments or dates. Only publish overall/group aggregates with at least five reviews, suppressing groups whose remaining complement has one to four people. No intersected subgroup filtering is offered. Review/profile/history deletion follows the existing full-local-data deletion action.

**Verification**
Service tests cover uint64 ownership, atomic validation, immutable old-chart snapshots, optimistic edit conflicts, scoped deletion/cascades and small-group/complement suppression. SDK form tests cover two five-component modals, background confirmation without early persistence and owner/chart/revision preservation through submission. Real database rehearsal adds only three tables and preserves fingerprints of all 28 existing tables.

**Prevention / Notes**
This is anonymity in the public UI, not anonymity from the local database operator. Text comments remain private under the user's confirmed aggregate-only default. Background is self-reported. Strengths/weaknesses, familiarity and goal are retained privately; current public grouping dimensions are Rating bucket, main level and experience. The live database remains unversioned: use the additive `review-schema` tool rather than replaying or stamping historical Alembic revisions.

## Historical duplicate aliases cannot be treated as a single scalar row

**Problem**
Legacy add-source, conflict and removal lookups used `scalar_one_or_none()`. Historical global/current-guild duplicates can yield multiple rows, raising `MultipleResultsFound`; selecting an arbitrary row instead could resolve or delete the wrong song association.

**Root Cause**
The alias table does not enforce a normalized-name uniqueness constraint across global/current-guild scope, while the old code assumed one eligible row. SQL `lower()` also does not provide the NFKC/casefold behavior used by the new search.

**Solution**
The new guild-learning transaction normalizes and rechecks all visible aliases and canonical titles immediately before insertion, with writes serialized by the existing queue. Manual guild addition uses that same transaction. Legacy source resolution deduplicates rows only when they identify one song; differing targets produce a readable ambiguity error with titles and stable IDs. Eligible duplicate removals are rejected without deleting any row. Song discovery presents all near-tied/exact duplicate candidates for explicit selection, and the wrong-song action searches again without modifying the original association.

**Verification**
The 24-case learning/legacy regression suite covers normalization, duplicate targets, guild visibility, title conflicts, concurrent confirmation, ownership and non-destructive ambiguous removal. An additional actual cog test verifies a normalized manual guild addition and refusal of a second equivalent name. Existing canonical IDs and global alias promotion behavior are preserved.

**Prevention / Notes**
Do not silently clean or remap old aliases while searching. Learned aliases are current-guild shared as confirmed by the user; global alias administration retains its existing restricted policy. Normal search does not increment legacy alias usage counts as a side effect of ranking candidates.

## Root discovery can descend into workbook-tool dependencies

**Problem**
An unscoped pytest run and an overly broad public-file scan stalled before producing useful project results.

**Root Cause**
The workbook authoring workspace contains `workspace/chart-mapping/node_modules`. Default recursive discovery was not limited to project tests, and the first file-scan implementation did not prune dependency directories.

**Solution**
Set pytest `testpaths` to `tests`, run the full suite against that project directory, and prune dependency/cache/environment/data directories from the public-file scan. The incomplete broad runs are not counted as verification.

**Verification**
The explicit full project run passes 515 tests. Default collection is separately checked against that same suite; no dependency tests are part of delivery acceptance.

## Unversioned local schema and later Alembic upgrades must agree

**Problem**
The running local database has the existing catalog/identity/player tables but no `alembic_version`. Replaying the historical migration chain would attempt to recreate existing tables. Independently adding annotation tables could also make a later normal Alembic upgrade fail with `table chart_annotations already exists`.

**Root Cause**
This installation was initialized using the ORM metadata and public seeds rather than the historical Alembic chain. Schema presence is not evidence that an arbitrary Alembic revision may safely be stamped.

**Solution**
The annotation importer validates the existing parent schema and creates only `chart_annotations` and `chart_annotation_tags`, inside the same explicit transaction as its source batch. It does not seed, replay old migrations or stamp an unverified version. Migration `4c6f8b1a0d92` can create these tables for a versioned installation, or verify an existing schema against its frozen columns, keys, checks and indices before accepting it. Partial or different schemas fail closed. Populated history cannot be discarded by a downgrade; batch rollback only changes annotation activation.

**Verification**
Isolated tests cover fresh Alembic installation, importer-first installation followed by the migration, unchanged history and refusal to downgrade populated tables. The real 834-row rehearsal preserves all 25 original non-snapshot tables and every pre-existing source snapshot; integrity/FK checks pass.

**Prevention / Notes**
Do not run the entire Alembic chain or stamp head on this unversioned live database. Schema/version reconciliation beyond the two annotation tables remains a separate maintenance task. Stop the workspace bot for an import so its serial writer is not waiting behind the import's `BEGIN IMMEDIATE` transaction.

## Two original worksheet rows are outside the reviewed 834-row mapping

**Problem**
The five workbook data tabs contain 836 nonempty song-title rows, while the completed legacy snapshot and reviewed ID manifest contain 834. Treating every nonempty title as an importable chart would either fail the import or silently invent two chart identities.

**Root Cause**
`14!335` (Imprinting) and `15以上!38` (PhenomenoN) have no completed L:R mapping. Imprinting has no rating constant; PhenomenoN has a source value differing from its legacy title match. They were not part of the previously reviewed 834-row mapping. Rich-text colors alone do not constitute a reviewed database chart-ID assignment.

**Solution**
The manifest defines the associated annotation set. Preserve both excluded rows, full A:J values, rich-text metadata and original links in `source_snapshots.provenance_json.excluded_unmapped_rows`, with explicit reasons, without creating chart links. All 834 manifest rows must be unique, complete and validated. Any extra row containing L:R mapping content is rejected even if its song title is empty.

**Verification**
Actual-source and synthetic tests verify both excluded rows, complete raw cells, missing/duplicate/changed mapping refusal and unsafe hyperlink retention in raw metadata only. The real dry run and rehearsal import 834 chart-linked annotations, 765 tag relations, 600 notes and 103 safe links; both unlinked original rows remain in source provenance.

**Prevention / Notes**
Do not label agent-reviewed associations as human authorship. The 826 automatic exact associations and 8 Luna-reviewed associations retain separate match methods and evidence. Old `bind-manual` writes archived JSON only; the production UI now reads the annotation database, so source updates must use the explicit importer.

## Removing slash entries must retain access to unmapped source scores

**Problem**
The single-song score tab reads canonical chart-linked personal bests. After consolidating `/score` into that tab, records imported with source-only IDs had no visible query route even though the source service still preserved them.

**Root Cause**
A source record and a canonical chart-linked record have different lookup paths. Reusing only the Song score tab cannot represent imported records that deliberately have no guessed canonical association.

**Solution**
Keep a distinct `主資料 → 來源成績` action using the existing source-aware `matching_rows` handler. Its query/source selection stays in the data category and displays the source's own records without creating a mapping. Song continues to display actual chart-linked records.

**Verification**
A regression enters this action through the real panel navigation, supplies an unmapped source record and confirms its original title/score are delivered for the selected source. Existing source isolation and import tests pass. The complete run is 487 passed.

## Community XLSX rich-text difficulty colors must survive ID annotation

**Problem**
The community sheet encodes Expert/Master/Ultima in the rich-text color of the square in column A. A generic workbook import/export flattened those runs. The sheet also has existing data in column J, so blindly appending there would overwrite source information.

**Root Cause**
The renderer/importer exposes cell values, but does not preserve every original rich-text run during an XLSX round trip. Visible source columns extend farther than the original JSON builder's A:H input.

**Solution**
Author the new song/chart ID columns in L:R with artifact-tool, then transplant only those new cells and their styles into the original ZIP package. Preserve original worksheets' existing cells, original relationships, hyperlinks, rich text, and all other original package parts. Leave K as a spacer and preserve J.

**Verification**
834 unique source rows map to 834 unique worksheet locations. All original populated/style cells outside the added range compare unchanged. The original file hash is unchanged; 826 filled chart primary keys join back to the exact song ID and difficulty in the read-only existing database; 8 unresolved rows have blank IDs. `outputs/chart-mapping-20261015/validation.json` records the result. The first overwritten-J attempt was replaced before handoff or delivery.

**Prevention / Notes**
Do not reconstruct source difficulty from a rendered black square or from a plain-value workbook copy. Keep original colors, source row indices and proposed-vs-reviewed statuses. Parent BM25 results are a workbook aid; no runtime bindings were silently approved.

## Public menus and sensitive account actions need separate response anchors

**Problem**
An earlier policy forced nearly all menus and ordinary results into ephemeral messages. The user clarified that privacy referred to running their own bot and requested original chuni-penguin comparison behavior. Removing ephemeral flags alone could edit an existing public message with credential output.

**Root Cause**
Public/ephemeral delivery and access permissions were treated as one concern. Discord cannot convert an existing public message to ephemeral, and an `ephemeral=True` argument does not protect an edit to an already public response.

**Solution**
Keep ordinary panels/results public and owner-controlled. Clone the UI session into a private response for login, credentials, import and confirmed destructive actions. Keep the public parent anchor separate and clear its busy flag. Continue respecting explicitly saved sharing preferences. New-user sharing defaults match the original A behavior; existing preferences are not bulk-changed. Reuse A's actual recent-message comparison helper, prefix command and message context action.

**Verification**
Actual SDK webhook tests cover both public and ephemeral component updates to the same message ID. A credential regression asserts that only the requesting owner's value is fetched, the private response contains it, the public message stays untouched and the parent remains usable. Upload tests reject other users/oversize inputs before reading/importing, and successful imports use the owner's ID. Original regression suite passes.

**Prevention / Notes**
Retain account access checks independently from response visibility. Do not add automatic ephemeral delivery merely because a menu belongs to a personal feature. The historical private-panel entry below describes the previous policy and is superseded by this user clarification.

## Ephemeral panel edits fell back to new messages

**Problem**
The user observed Embed updates creating additional messages. Token linking explicitly sent a followup, and modal submissions could omit their source message. The earlier tests used an editable fake Message, masking a second transport problem in real Discord components.

**Root Cause**
Discord constructs `Interaction.message` as a regular `discord.Message`. Its `.edit()` uses the channel message API, which cannot edit ephemeral responses. The earlier component adapter assigned this object to Context.response; editing could return NotFound and trigger a new reply. The component's webhook-backed `Interaction.original_response()` returns an editable InteractionMessage for the same source ID. Separately, modal context reconstruction relied only on interaction.message, and Chunirec binding used followup.send unconditionally.

**Solution**
Bind the private response to the panel session, acknowledge each update with deferred message update when a private response exists, obtain the webhook-backed original response and retain the same message ID. Private view tab edits use the current interaction's edit_original_response. Modal submissions reuse the bound anchor, including when message is absent. A direct slash-opened link modal creates one initial private original response. Account, sync, login and binding results update the data landing page. Token values are cleared from both TextInput and nested modal data before any resulting view retains a response.

**Verification**
Added a regression using actual SDK Interaction, Message and InteractionMessage classes with a mocked Discord webhook adapter. It asserts response type 6, the original message ID, one webhook edit and zero channel edits. Additional tests exercise real Context response routing, a source-less modal, initial account linking, data status, same-message sync and category boundaries. Public source messages remain public and unchanged while personal operations receive their own private response.

**Prevention / Notes**
Do not substitute a generic Message.edit mock for the ephemeral transport boundary. Keep the original private response or refresh it through the current interaction's webhook. Historical records describing navigation within one message predate this user-reported transport correction. A deleted/expired response still requires reopening its command.

## B50 rendered score cards without song artwork

**Problem**
After the Windows attachment fix, the user could receive B50 but song jackets were absent.

**Root Cause**
The reused renderer only opens local `assets/jackets/<song.id>.webp`; this workspace did not contain that artwork collection. Having jacket URLs in the catalog did not cause the renderer to download them.

**Solution**
Add a bounded public-artwork cache before both existing BestPlays rendering paths. Resolve candidates from the record's existing song and the exact same canonical ID in public seeds, with no title-based association. Use an independent credential-free HTTPS client, allowlisted redirects, six concurrent downloads, five-second request timeouts, a 25-second overall budget, 5 MiB/4096-pixel input limits, decoded WebP conversion and atomic replacement. Valid cached files are reused; unavailable artwork is temporarily negatively cached. Per-song locks deduplicate fills without giving one request ownership of another request's tasks.

**Verification**
Actual public jackets for canonical IDs 3, 6 and 7 downloaded and appeared in the original B50 renderer's output; a second call used all three cached images. Visually inspected `reports/b50-jackets-smoke.png`; scores are synthetic. Tests cover public-URL fallback, invalid images, redirects to untrusted hosts, absent player credentials, atomic cache reuse and cancelling one of two requests for the same song.

**Prevention / Notes**
Keep renderer attachment checks and artwork preparation checks separate. Remote artwork outages still use the existing missing-art fallback; no cache failure should change player scores or canonical song identities. Repeated calls can complete artwork missed by a bounded first request.

## Existing slash callbacks need an explicit component adapter

**Problem**
Early panel tests exposed `Interaction._baton` assumptions when reusing HybridCommand checks, a six-button row in the song view constructor, and duplicate custom IDs for two gacha entry buttons. These fail before Discord can display a usable panel.

**Root Cause**
HybridCommand.can_run expects a slash-generated Context, while buttons and modals have component interactions. Decorated Discord buttons are allocated during View construction, before dynamic controls replace them. The initial panel custom ID did not distinguish two valid buttons for the same action.

**Solution**
Use an explicit safe command whitelist, original Command checks/converters/cooldowns/concurrency/hooks, and the original application-command checks. Keep mirror's constructor row separate and place it in the final paging row only when visible. Include the row in panel button IDs. Do not mutate Discord's private Interaction._baton or manufacture new command registrations.

**Verification**
Panel tests invoke actual loaded calculator and selected Poopenguin commands with component-shaped interactions. They verify the original guild-only check and sync cooldown, and serialize every panel route and song tab to assert at most five rows, valid row widths, no duplicate custom IDs and at most 25 select options. Existing privacy and song-view regressions remain in the full suite.

## Replacing an interactive page must retire its previous work and controls

**Problem**
A rebuilt gacha submenu lost added navigation. An in-flight player lookup or login prompt could otherwise finish after another group had replaced its message, or keep the whole session busy while waiting five minutes for user input.

**Root Cause**
The reused views have independent lifecycles: gacha clears controls during each rebuild, recent keeps a native reader open, song tabs load asynchronously, and NET login waits for an event inside the original callback.

**Solution**
Reattach the single related menu in gacha's existing control builders. Mark replaced views inactive, close their existing native readers, deactivate song data and stop the old view. Common view edits/timeouts ignore inactive panels. Allow navigation while NET login waits, cancelling only that waiting prompt when leaving; original verification/storage still runs under its existing checks. Login completion retains account navigation.

**Verification**
Regression tests cover all six gacha control states, recent-reader release, a blocked song score load completing after navigation to home, ignored stale edits, cancellation of a waiting login and a usable session afterward. Real Context.send/reply are checked to edit the existing panel. Public results open a new private message for personal operations, and another user cannot operate it.

## Windows B50 rendered successfully but could not become a Discord attachment

**Problem**
Live `/b50` calls failed after generating the PNG. Safe diagnostics showed TypeError in `discord.File.__init__`, called from `_best50_respond`; existing renderer-only tests did not exercise attachment creation.

**Root Cause**
On Windows, `tempfile.TemporaryFile` returns `_TemporaryFileWrapper`, which is not an `io.IOBase`. Discord treats non-IOBase arguments as paths and calls `open(wrapper, 'rb')`, causing TypeError. The wrapper's underlying `.file` is a valid readable/seekable IO stream.

**Solution**
The existing `AsyncTemporaryFile` helper now yields the underlying IO stream when wrapped, while retaining and closing the owning temporary-file wrapper to remove its file. This keeps B50 rendering and Discord upload on the same stream, and applies to both existing B50 rendering routes without platform-specific command duplication.

**Verification**
Reproduced the live stack with synthetic player scores. Three regression cases failed before the change and passed after: native B50's real PNG-to-Discord attachment path using the native backend and a forced named-file wrapper, plus temporary-file cleanup on failure. The attachment is readable from offset zero, can reset for transport retries, and closes after delivery. Full suite: 456 passed. Restarted bot PID 7344 loads all 21 modules and connects to Gateway with empty stderr. A successful real-player upload after this fix remains for the user's retest.

**Prevention / Notes**
Include attachment construction in renderer integration checks. The forced Windows-style backend preserves coverage when tests run on Unix. No player credentials, scores or remote command registrations need to change for this fix.

## Original player defaults permitted sharing

**Problem**
A created absent user preferences with privacy_mode=false and sent several player commands publicly. A public info card could later switch into personal scores.

**Root Cause**
The original product default was public, while response visibility was chosen independently by each command.

**Solution**
Default new and absent preferences to private. Apply a shared delivery policy to personal contexts and reject sensitive guild prefix commands. The unified chart card is also private. Add explicit `/privacy share` and keep source access checks for comparisons.

**Verification**
Temporary SQLite/interaction tests pass for private defaults, blocked public prefix operations, denied unshared comparisons, encrypted source credentials and deletion isolation. Full suite: 443 passed; real Discord interaction behavior remains unverified.

**Prevention / Notes**
This new workspace has no migrated players. Existing production preferences must be backed up and reviewed before any future migration; no existing sharing choices have been changed.

## B annotation rows cannot safely establish canonical identity

**Problem**
The 834 manual source rows contain title/difficulty/constant but no official song ID. A title-based join would look convincing while violating the identity requirement.

**Root Cause**
B's community-sheet storage uses a textual key, independent from A and Chunilib namespaces.

**Solution**
Keep source rows searchable through `/tags`. Attach only explicitly reviewed bindings containing canonical song/difficulty, source file hash, source row index, reviewer and evidence. Statistics use exact A.chunirec_id/source URL IDs instead.

**Verification**
Bundled bindings start empty; all 12 statistical associations resolve uniquely by official native IDs. Binding/difficulty isolation, explicit review, duplicate refusal and stale-hash tests pass. Inspection of the original XLSX relationships found general index links and video references, with no direct official chart-ID mapping.

## Chunilib protected player APIs and absent semantic results

**Problem**
Website player endpoints require an official logged-in/verified session. The existing v3 workflow database currently has zero semantic rows.

**Root Cause**
Chunilib public catalog access is independent of player authentication. Existing chart metadata is not itself a completed LLM analysis.

**Solution**
Support user-supplied player-data JSON import in its own source namespace, with no title-based canonical join and no external upload. Clearly report unsupported online Chunilib synchronization, unavailable recent history, and absent semantic results. Retain an explicit reviewed semantic adapter for future artifacts.

**Verification**
Read-only source inventory and official Chunirec API documentation checked. No protected endpoints called and no player data uploaded.

## B persistence and message logging require privacy fixes

**Problem**
B's listener printed every observed message, and JSON parse failures silently fell back to defaults that could overwrite damaged data.

**Root Cause**
Debug logging and permissive storage recovery were part of the original implementation.

**Solution**
Remove message-body logging; keep automated message, moderation, feed and AI extensions disabled by default. Fail closed on unreadable JSON; use backup, flushed temporary file and atomic replacement. Shared content/pool mutations require the bot owner, since they affect every guild.

**Verification**
Default and all optional B modules load offline without network or remote command registration. Storage backup/corruption and ownership/access checks pass in the final 443-test suite.

## Sparse A checkout omitted the real B50 renderer assets

**Problem**
The BestPlays module imported successfully using fonts, but the image renderer also needs 128 WebP assets that were missing from the sparse checkout. Module-load tests alone would miss this failure.

**Root Cause**
The prior reference checkout included source and fonts, but not `assets/b50`.

**Solution**
Read the exact public assets from the reference Git tree into the new workspace. Preserve A's original renderer instead of replacing its graphics or requiring the older text-only view.

**Verification**
Generated `reports/b50-smoke.png` from a synthetic player/score using the actual renderer. The output is 3,266,529 bytes. Added a renderer test that opens the real PNG and verifies its dimensions.

**Prevention / Notes**
Keep public B50 assets under version control and retain the renderer smoke test. No live player account was used for this check.

## Exclusive account locks would block concurrent recent-score views

**Problem**
The initial mutation guard held a user lock for the entire native account context. Recent-score views retain that context, so a second lookup could wait until the first view closed.

**Root Cause**
A long-lived read/view was being treated as an exclusive account mutation.

**Solution**
Lock native context entry briefly, count active readers, then release the mutation lock during reads. Login changes, logout and deletion check active readers under the same mutation guard. Concurrent reads continue to reuse A's original reference-counted network sessions.

**Verification**
A regression test opens two same-user native contexts concurrently and verifies count cleanup on normal and exceptional exit. Deletion refuses an active recent view and preserves stored data. Full suite: 443 passed, offline shutdown has no pending tasks.

## Valid Discord login does not make the new slash commands available

**Problem**
The authorized existing Bot Token successfully authenticated and reached Gateway READY, but `/chart`, `/score`, `/account` and other v2 commands are absent from the remote schema. The app currently has 25 existing global commands; its sole existing guild has zero guild commands.

**Root Cause**
The remote application retains its previously registered schema. v2 startup deliberately leaves registration to an explicit owner operation, as requested; loading local command objects does not register them remotely.

**Solution**
Keep registration unchanged. Added a bounded official REST/Gateway checker that inspects the existing schema, uses temporary data, blocks messages/command writes and user callbacks, and records the distinction between successful connection and pending interaction acceptance. A later authorized test-guild registration is required for live slash testing.

**Verification**
`reports/discord-live.json`: authenticated=true, gateway_ready=true, startup complete=true, one shard, zero command writes/messages/blocked requests/pending tasks; clients closed and fixture integrity verified. `reports/source-live.json` separately records the missing owner Chunirec token. Final full suite: 453 passed.

**Prevention / Notes**
No extra Windows permissions are needed. Do not replace existing global commands as a workaround for a missing test configuration; the original no-remote-registration instruction still applies. `.env.example` provides an optional TEST_GUILD_ID and owner-source test parameters without silently enabling services.
