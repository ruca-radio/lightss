# Desktop player and calibration implementation plan

**Goal:** Deliver the approved desktop/provider/calibration flow and rebuild release artifacts.
**Architecture:** Existing Python engine + Qt WebEngine desktop/provider pages + bounded in-process bridge. Durable local playlists and queue, reviewed topology calibration.
**Tech Stack:** Python >=3.14, PySide6 >=6.10,<7, stdlib SQLite, existing server/HTML.
**Spec:** docs/superpowers/specs/2026-09-07-desktop-calibration-design.md

## Global constraints
Preserve user-owned/uncommitted work; do not reset a checkout, create a worktree, or
stage/commit existing changes. Do not perform live controller writes, account playlist
mutations or authenticated provider playback in tests. New behaviors need failing
regression tests before code. Keep provider truth separate from simulated boundaries.

## Task 1: Desktop owner and provider bridge
Files: light_desktop.py, desktop_bridge.py, tests/test_desktop.py,
tests/test_desktop_bridge.py, pyproject.toml, requirements-tray.txt, desktop launcher.
Interface: desktop_bridge.registry exposes active, snapshot(source), command(source,
command,data=None), publish(source,payload), drain(), close(). Commands queue bounded
validated provider operations; snapshot timing expires. GUI imports are lazy for tests.
Test first: lifecycle, queue bound, URL/ID allowlists, stale timing, per-provider state.
Implement Qt window/local server lifecycle with persistent isolated provider profiles.
Test built desktop offscreen launch; document login/DRM compatibility truth.

## Task 2: Calibration staging service
Files: calibration_service.py, calibrate.py (only required safety fixes),
tests/test_calibration_service.py. Interface: CalibrationService(client, config_path=None),
scan(), apply(token), identify(controller,segment). Payloads are JSON-ready with ok,
message, token, probes, proposed config and can_apply. State is locked, bounded and
expiring. Test offline/invalid/replay/stale config and hardware drift before atomic
backup/apply. No speculative electrical hardware writes. Return explicit limitations.

## Task 3: Durable library and provider adapter
Files: player_library.py, audio_player.py, tests/test_player_library.py,
tests/test_desktop_player_adapter.py. Library(path=None).handle(payload) and snapshot()
return JSON-ready ok/playlists/queue; actions create, rename, delete, add, remove,
reorder, queue_add, queue_remove, queue_clear. Validate provider IDs and ordered indexes;
SQLite transactions and bounded lists. Route desktop active status/search/transport
through desktop_bridge.registry; preserve legacy explicit non-desktop integrations.
Search delegated to official provider UI is marked delegated rather than empty-success.
Add artists to MusicKit search and album playback. Test fail then pass, no remote calls.

## Task 4: Integrate UI/server and verify
Files: light_gui.py, light_gui_html.py, new focused server/JS tests, README.md.
Endpoints: /api/calibration (POST scan/apply/identify), /api/player/library (GET/POST).
Initialize services lazily on GuiState. Render scan facts + reviewed Apply; no automatic
apply or identification. Add local playlist/queue controls and provider search status.
Prefer desktop observed timing in playback-clock payload without launching providers.
Test JSON errors, command dispatch, safe DOM strings, no extra Shazam/LLM loops.

## Task 5: Review and rebuild
Run isolated HOME full pytest with PYTHONDONTWRITEBYTECODE=1 and
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1; ruff check --select F *.py; git diff --check.
Independent final review of task files and cross-task boundaries. Rebuild wheel/sdist
and available distro package using project tooling, list exact paths/hashes, install
wheel into isolated environment, desktop smoke test and rendered browser boundary QA.
Record provider authentication/DRM, live hardware changes and packaging gaps honestly.
