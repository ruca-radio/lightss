# Lightss music and WLED work

## User priorities and decisions
1. Fix timer-driven Shazam calls first; detect likely track changes locally.
2. Keep a playback observer clock independent of being the player. Avoid ongoing AI token burn.
3. Audit and repair WLED capability/context defects while preserving existing uncommitted work.
4. Redesign as a fully featured desktop player, not an external companion-server wrapper.
5. Add a visible Calibrate workflow with optional webcam/vision assistance.

## Status
- [x] Baseline audit and live read-only controller inspection.
- [x] Event-driven recognition gate and removal of duplicate automatic browser recognition.
- [x] Observer clock foundation and external MPRIS timing; unknown mic alignment is explicit.
- [x] Targeted WLED catalog, missing-state, prompt, typing, player-search and HTML injection fixes.
- [x] Isolated suite: 830 passed, 1 skipped (33.85s); Ruff F checks and git diff --check pass.
- [x] Isolated rendered Chrome UI verification and final gates.
- [ ] Calibration scan/review/apply design approval (question sent).
- [ ] Desktop player architecture/spec approval, provider feasibility and implementation plan.

## Boundaries
No hardware configuration changes, destructive cleanup, commits, or credential changes.
Existing calibration reads controller-declared configuration; it cannot measure physical
LED count or placement without guided identification. Full desktop player and webcam
calibration are not implemented by the recognition fixes.
