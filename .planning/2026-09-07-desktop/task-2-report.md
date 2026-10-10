# Task 2 report: calibration staging service

## Outcome

- Added `CalibrationService(client, config_path=None)` with locked, bounded,
  five-minute, opaque single-use scan tokens.
- `scan()` reads every configured fleet member through the existing
  `calibrate.collect_probes()` / `build_controllers()` path and stages only a
  complete topology that passes `fleet.load_topology()` validation.
- Raw probe validation rejects invalid/nonpositive LED counts, max-segment
  conflicts, duplicate or malformed segment IDs, invalid/out-of-range bounds,
  and missing/malformed/incompatible bus geometry before parsers can silently
  reduce the report to a valid-looking subset. Disk and runtime fleet
  membership must also agree when controllers are persisted in the config.
- `apply(token)` consumes the token, rejects config-byte drift, re-probes the
  complete fleet, rejects hardware/proposal drift, creates a unique backup,
  and atomically replaces the config. Unknown root and installation keys are
  preserved, as are unknown controller-level and surviving segment-level keys.
- `identify(controller, segment)` requires one explicit configured selection,
  rejects a detectable active realtime renderer plus live/frozen per-pixel
  state, displays a bounded
  low-brightness static marker, and restores the prior state in `finally`.
- Hardened legacy `calibrate.identify()` so exceptions during flashing still
  run its power restoration path.

## Safety boundaries

- Scans and apply validation only read hardware. Tests use fake clients and
  perform no network or live controller writes.
- Apply changes local topology only. It does not write WLED GPIOs, bus types,
  power limits, or electrical configuration, and does not infer physical
  installation geometry.
- Webcam guidance remains optional/advisory. No camera integration was added,
  and declared/controller-observed geometry is explicitly not represented as
  proof of physical order or orientation.
- Apply takes an advisory cross-process `flock` shared by calibration-service
  writers and repeats the config digest immediately before replacement.
  Legacy config writers do not take this lock, so protection against those
  writers remains explicitly best-effort optimistic concurrency rather than a
  universal compare-and-swap guarantee.

## Service schema

- `scan()` returns `ok`, `message`, `token`, `probes`, `proposed`,
  `can_apply`, and `limitations`.
- `apply(token)` returns those fields plus `written` and `config_backup`.
- `identify(controller, segment)` returns `ok`, `message`, `controller`,
  `segment`, and `limitations`.

## Verification

- TDD RED: missing `calibration_service` failed collection; the legacy
  restoration regression failed with the final payload lacking `on`; the
  identification-duration regression failed without `IDENTIFY_SECONDS`.
- Fresh focused suite: `26 passed` for `tests/test_calibrate.py` and
  `tests/test_calibration_service.py`; Ruff F checks, diff-check, and py_compile
  also pass.
- No commit, staging, reset, worktree, or live hardware operation performed.
