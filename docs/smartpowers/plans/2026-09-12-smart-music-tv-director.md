# Smart Music / TV Director Plan

Approved: finish and activate controller-mic music shows, automatic TV-versus-music detection, steady TV theme and day/evening/night dimming, manual override, and live verification.

## Scope and ownership
1. Audio worker owns wled_audio.py and CLI gate/tests: multicast membership, source filter, local receive sequence, finite data and freshness.
2. Renderer worker owns realtime.py and tests/test_audio_reactive_renderer.py: AudioReactiveRunner consuming normalized snapshots, smooth layered FFT/beat animation, safe brightness and silence handling.
3. Intelligence worker owns activity_intelligence.py and tests: tool-free metadata classification and bounded palette recipe, unknown defaults steady.
4. Root owns smart_director.py, fleet ownership guard, GUI API/UI integration, packaging and integration tests. One active wall writer; external/manual commands suspend automatic control until explicitly resumed.

## Runtime contracts
- One controller microphone is primary. No desktop/browser microphone or Shazam required.
- TV observation remains separately enabled. Never enable TV wake/sleep/playback controls.
- Auto: known video foreground stays TV, confident music starts a show after debounce, uncertain content defaults steady. Short pauses/metadata transients use hold time. Explicit Music/TV overrides available.
- Night22–07 brightness5%, evening18–22 15%, day07–18 30%, America/Detroit, editable; static themes warm/neutral/blue. Transitions fade gradually.
- Audio and observation workers never directly issue light commands. Classifier results only apply to their current media key.
- UI continuous status uses backend audio feed, offers Auto/Music/TV/Manual and disable; legacy browser beat requests rejected while smart controller owns wall.
- AI director is optional tool-free palette/composition designer, not per-frame control.

## Verification
TDD per component, independent review, isolated full suite and browser UI, backup config/controller state, restart exact active GUI, verify sustained mic packets + DDP on both controllers, TV steady fx0 and correct local-time brightness, manual override and return-to-auto. Preserve original uncommitted work, no commits.

## Rulings
- Work in current feature checkout with file backups because deployed code includes extensive uncommitted/untracked dependencies; a clean HEAD worktree would test a different application. No existing edits discarded.
