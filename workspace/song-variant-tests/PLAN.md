# Song Variant Discovery Tests

## Plan

- [x] Inspect fixture helpers and the parent service changes to pin the test API.
  - Result: Reused the offline fixture database and confirmed the variant fields and group exclusion API.
  - Changed: `workspace/song-variant-tests/PLAN.md`
  - Verified: Read the fixture helper and current song discovery service.
- [x] Add focused discovery tests for grouping, representative selection, ambiguity, aliases, and correction exclusions.
  - Result: Added seven service tests covering normal plus multiple WORLD'S END rows, WORLD'S END-only availability ordering, separate artists and duplicate normal compositions, duplicate aliases, whole-group correction exclusion, and exact WORLD'S END alias targeting. The grouping test also confirms charts remain loaded for each selectable record.
  - Changed: `tests/test_song_variant_discovery.py`
  - Verified: The new tests exercise the fixture-backed `SongDiscoveryService`.
- [x] Run the new test module and record the actual outcome.
  - Result: All seven tests passed.
  - Changed: `workspace/song-variant-tests/PLAN.md`
  - Verified: `.venv\Scripts\python.exe -m pytest tests\test_song_variant_discovery.py -q` — 7 passed in 4.17s.
