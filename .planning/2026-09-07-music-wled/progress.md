# Progress — 2026-09-07

Implemented and exercised local novelty gating, bounded retries, stable-track identity
reuse, stop/reset protection, metadata deduplication, observer clock API/context/UI,
and targeted codebase repairs. All new behaviors had failing regression tests before
implementation. Latest isolated full suite: 830 passed, 1 skipped (33.85s). Ruff F checks and git diff --check pass. Final clock-expiry changes are locally verified; the running service was not restarted by this session.

The live GUI already serves the event-driven changes after an external restart.
Read-only session polling confirmed one recognition attempt over more than three
minutes. No live identification calls or light/configuration writes were made by
the assistant to obtain that evidence.

Rendered Chrome QA passed using an isolated profile, synthetic microphone, and stubbed API boundaries: automatic mode uploaded two samples with zero direct recognition requests; manual identification made one recognition and one mood-match request; no page JavaScript errors. The clock rendered position/duration correctly. Screenshot: `/tmp/lightss-ui-qa.lj2Yij/desktop.png`. The static fixture omitted the logo/favicon assets (404), not a production asset regression. Temporary browser and HTTP server stopped.

Added regression fixes clearing room clocks after sustained silence, invalidating disconnected external clocks, and expiring AI cached external timing after 15 seconds without refresh.

Pending: calibration design answer and desktop architecture approval. User chose desktop app
for the future standalone player. No player rewrite or camera-driven calibration
has been implemented yet.
