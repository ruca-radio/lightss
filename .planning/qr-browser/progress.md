# QR browser integration
Plan: docs/smartpowers/plans/2026-09-08-native-chrome-passkeys.md
- Transport: complete; bounded nonblocking private pipes, owned process cleanup.
- X11 discovery: complete; exact PID, visible usable surface, worker thread.
- Qt/provider integration: complete; native tab QR, origin guard, navigation epoch.
- Review: approved after repairing stale callbacks, GUI discovery, network permissions,
  hidden Chrome helper selection and selected-page detach handling.
- Full regression: 976 passed, 38.93s; Ruff F and diff-check clean.
- Native controlled-origin runtime: both-tab QR screenshots, resize/tab switch,
  abort/cancellation, real muted audio play/seek/pause, clock 42.249s of 60s,
  owned Chrome processes closed. /tmp/lightss-native-qa/verified/result.json
- Packaging: 0.2.2 wheel, source archive and Debian package rebuilt. Packaged native
  check passed (QR/cancel/resize/tab switch/audio clock/cleanup), clock 42.2495s;
  /tmp/lightss-native-qa/packaged/result.json. No system install or user app restart.
- Public Chrome provider pages: YouTube Music and Apple Music loaded in tabs in
  isolated profiles; /tmp/lightss-native-qa/public-smoke.log.
- Live account boundary: no phone scan, Google/Apple account authentication or DRM
  playback completed. Public-page check is not account-authentication proof.
Ruling: preserve current shared dirty checkout; no worktree or commits. Existing user work must not be reset.
