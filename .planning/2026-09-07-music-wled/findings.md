# Findings

## Verified root causes
- Served UI had two automatic recognizers: a 30s browser loop and 5s PCM uploads
  admitted to Shazam every 20s. Identical PCM called recognition four times in four
  cooldown-separated samples before the fix.
- Effect policy retained stale allowed IDs after an all-forbidden or failed refresh;
  raw writes skipped offline safety. Fleet prompts assumed identical catalogs.
- Missing WLED state was rendered as power=off. The system prompt overrode runtime
  orientation with a hardcoded bottom-origin rule.
- Remote library IDs were interpolated into inline JavaScript; DOM construction
  and captured callbacks now preserve remote strings as data.
- Empty search matches returned the entire queue; fixed.

## Baseline and readbacks
- Initial worktree: integrated-audio-player, 39 tracked modified files plus existing
  untracked calibration/audio tests/modules. Preserve all unrelated work.
- Baseline suite: 787 passed, 1 skipped, with 18,789 pytest-asyncio deprecation warnings.
- New validation runs use an isolated HOME and PYTEST_DISABLE_PLUGIN_AUTOLOAD=1:
  the tests are synchronous and explicitly use asyncio.run where needed.
- Both controllers report WLED 16.0.1, 220 effects and 220 fxdata records. Segment
  lengths match current local configuration (34/48 on left, 47/40 on right).
- Live GUI restarted externally during work (PID changed); no assistant restart.
  Its active mood session showed on_change/waiting_for_change, queries=1 over 186s.

## CodeRabbit review of the pre-existing diff
8 issues: 1 critical, 3 major, 4 minor.
- Fixed: library inline-JS injection; inactive/wrong-source audio tap; offline effect
  safety bypass; empty-search fallback; ambient sampling cooldown measured at completion.
- Not reproduced: absent WLED snapshot level/beat keys; get_snapshot always provides them.
- Still open: Apple session dictionary synchronization; legacy sync-wrapper timeout
  does not cover queued pacing plus every retry (manual calls can leave worker running).

## Known limits
Audio novelty is heuristic: chorus changes may look like track changes; similar tracks
and seamless mixes can be missed. No database or LLM is used for novelty detection.
A microphone identification timestamp is NOT a verified within-track position. Room
clock position remains unknown until a validated alignment source is supplied.
