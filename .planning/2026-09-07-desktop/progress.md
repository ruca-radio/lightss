# SDD ledger — plan: docs/smartpowers/plans/2026-09-07-desktop-calibration.md

Ruling: Work directly in the existing dirty checkout with nonoverlapping file ownership;
no worktree or commits — preserve user-owned work. Cost: review requires path-scoped
working-tree evidence rather than commit diffs.

Tasks 1-3 independent; task 4 root integration; task 5 integrated review/rebuild.

Tasks 1-3 implemented with regression tests; independent reviews caught and fixed
provider isolation, missing track IDs, album consumer routing, lifecycle and queue
transaction defects. Task 4 browser-boundary Chrome QA passed: scan-only before apply,
reviewed apply token, local playlist create/add/queue, zero automatic Shazam, no JS
exceptions. Screenshots /tmp/lightss-desktop-final-qa/{calibration,library}.png.

Urgent Ride bug: live controllers both report effect56=Tri Fade. Generated-mood path
failed to pass live catalog into validation; request-scoped catalog intersection now
used (avoids unrelated global policy leakage). Failing-generation fallback no longer
cached. User-reported Ride cache was verified byte-for-byte as default ambient payload;
removed ONLY that key after unique backup, preserving440 other entries. Backup:
/home/rucaradio/.config/lightss/song_moods.json.bak-ride-failure-1788822819626698393.
Current in-memory sessions require restart to see repaired persistent cache.

Ruling: Sequential local queue accepts individual tracks only; collection bookmarks
open in the provider — no reliable collection-completion signal exists. Cost: users
must add album tracks individually to mix them in a sequential local queue.

Rebuild in progress; wheel/sdist0.2.0 initially built, final recut required after review.
Full real Qt startup QA caught missing privileged-request header (403 local tab), unlike
constructor-only smoke. Desktop implementer addressing interceptor lifetime and retained
end-event regression before final release.

## Final verification and build
- 925 application tests passed in39.00s with isolated HOME/XDG and plugin autoload
  disabled. All app tests discovered under tests/ (packaged dependency test suites
  are not application tests). Ruff F and git diff --check pass.
- Real full Qt window + protected local backend + three provider tabs rendered
  successfully using fake hardware and isolated browser storage. Retested using
  modules installed from the built wheel under the distro staging prefix with
  /usr/bin/python3.14 -I: exit0, ok:true. No token/auth-boundary or profile-lifecycle
  errors. Public provider JS emits telemetry queue/WebGPU warnings in headless mode;
  upstream pydub0.25 emits Python3.14 regex SyntaxWarnings on first import. These are
  recorded nonfatal dependency/provider warnings, not hidden test failures.
- Browser visual QA confirmed reviewed calibration, local playlist add/current queue,
  safe rendered labels, and no automatic Shazam requests. Screenshots/logs and exact
  replay scripts in /tmp/lightss-desktop-final-qa/. Temporary Chrome/server stopped.
- dist/lightss-0.2.0-py3-none-any.whl: built, includes every current app module
  byte-for-byte plus logo, default lightss entrypoint launches desktop.
- dist/lightss-0.2.0.tar.gz: rebuilt source distribution with tests/scripts/assets.
- dist/lightss_0.2.0_amd64.deb: rebuilt245MiB package with Python dependencies and Qt,
  desktop menu/icon and system-Python launchers; prior0.1.0 artifacts preserved.
- SHA256 wheel:59de315b2953b1194dba209eeaa631e30fdf11f5871f38db42eca0321e105cd8
- SHA256 source:b2f54c14e521962819f6e35bbf7e766c1e435cc988a565c58aba582af060dbe1
- SHA256 deb:04f34d01cbdd4cbe1f9aa3b8fbaafbd0614fdad46aa03b618c850e15b2a4e1fd

Completed implementation/rebuild, not a system install or live app restart. Close the
existing app/server before launching ./light-desktop to avoid two lighting engines and
to load the repaired cache. Authenticated service playback/DRM/autoplay still needs
account-based validation; queued navigation is not claimed as playing. Calibration
changes platform topology only; no live controller writes or account mutations occurred.
