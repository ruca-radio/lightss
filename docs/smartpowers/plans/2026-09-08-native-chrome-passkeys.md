# Native Chrome passkey integration implementation plan

Approved: user approved the single-window replacement on 2026-09-08.
Spec: docs/smartpowers/specs/2026-09-08-native-chrome-passkey-design.md
Goal: show real native phone-passkey QR UX in provider tabs without breaking the desktop control/clock contracts.

Constraints: preserve dirty checkout; no worktree/reset/staging/commit. Python>=3.14,
PySide6>=6.10,<7. Separate app-owned Chrome profiles, no credentials copied or logged,
private inherited debugging pipes, no disabled browser security. Native embedding is
Linux/X11 (including XWayland); explicit limited Qt fallback elsewhere.

1. chrome_transport.py + tests/test_chrome_transport.py: TDD bounded null-framed CDP,
   serial call/event handling, process launch with isolated profiles and private pipes,
   fail-fast EOF/errors/timeouts, owned-process cleanup. Parallel bounded worker.
2. chrome_x11.py + tests/test_chrome_x11.py: TDD native window lookup by exact owned
   Chrome PID, bounded X11 tree traversal; no title matching or shell utilities.
   Parallel bounded worker; no Qt widget ownership in this module.
3. chrome_provider.py + tests/test_chrome_provider.py: TDD background browser worker,
   origin guard, request policy, bounded operation queue, Qt-thread callback delivery,
   PID-owned embedding and cleanup; preserve runJavaScript/setUrl call contract.
   Root integration in light_desktop.py, automatic supported-runtime selection and
   explicit --provider-engine=qt fallback. No provider auth tokens reach backend/AI.
4. Review scoped changes and repair findings. Validate actual app-tab hybrid QR using
   isolated test origin, cancellation, resize/tab lifecycle and browser closure.
   Validate provider public pages without account mutation. Account owner performs
   actual Google/Apple login; report this boundary rather than asserting success.
5. Full isolated pytest, Ruff F, git diff --check. Version0.2.2 wheel/sdist/deb rebuild,
   packaged app smoke, preserve previous artifacts. Update README with prerequisites,
   profile changes, QR flow and unsupported-platform/provider-policy limitations.
