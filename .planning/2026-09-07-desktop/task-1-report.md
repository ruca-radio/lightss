# Task 1 desktop owner/provider bridge report

## Outcome

- Added the `light-desktop` Python/Qt entrypoint and launcher while retaining the existing Python/WLED engine.
- The desktop owns an in-process `ThreadingHTTPServer` bound to `127.0.0.1:0`; this is necessary for the server/player adapter and Qt command consumer to share `desktop_bridge.registry`. Shutdown closes the server, joins its thread, and stops known state services.
- YouTube Music and Apple Music use separate named persistent `QWebEngineProfile` storage roots and render their full official web UIs.
- Remote profiles receive no Qt/local API bridge. Their request interceptor blocks loopback/private/link-local aliases plus `file:`/`qrc:` requests, and main-frame navigation is restricted to provider/auth hosts. The local Lightss page has a dedicated privileged profile restricted to the exact backend origin. Every backend GET/POST/HEAD requires a random per-process capability header injected only for that exact origin, protecting mutations even if a URL-filter alias is missed.
- Provider operations are a bounded FIFO. Full queues reject rather than discard accepted commands. Only fixed commands/data, provider IDs, and provider URLs are accepted. Search/library/item navigation uses application-built official URLs; transport/timing uses fixed application-owned media DOM snippets, never API-supplied JavaScript. Provider observers extract actual playing IDs and latch media `ended` events so an autoplay transition cannot be mistaken for pause or silently miss local queue advancement.
- Local microphone/camera requests are eligible only from the exact privileged Lightss origin and always display an explicit native confirmation dialog. Provider capture permissions are denied.

## Bridge contract

Sources: `youtube_music`, `apple_music`.

Public registry calls: `active()`, `snapshot(source)`, `command(source, command, data=None)`, `publish(source, payload)`, `drain()`, `close()`. `activate()` is a desktop-internal lifecycle hook.

Commands: `play`, `pause`, `toggle`, `next`, `previous`, `library`, `seek {position}`, `volume {volume}`, `search {query}`, and `play_id {id,kind}` where kind is `song|album|playlist|artist`.

Snapshots include `source`, `desktop`, `connected`, `stale`, `playing`, `ended`, `position`, `duration`, `title`, `artist`, `album`, `track_id`, `url`, and `observed_at`. Stale observations clear timing and `ended`, and report not playing.

## Verification

Passed:

```text
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q [desktop, GUI integration, calibration, mood focused files]
Focused desktop/GUI/calibration/mood review suite: 74 passed in 5.04s

ruff check --select F light_desktop.py desktop_bridge.py tests/test_desktop.py tests/test_desktop_bridge.py
All checks passed!

git diff --check -- light_desktop.py desktop_bridge.py tests/test_desktop.py tests/test_desktop_bridge.py pyproject.toml requirements-tray.txt light-desktop
exit 0
```

PySide6 6.11.2 was installed in `.venv`. With a temporary isolated `HOME` and XDG directories, `QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 .venv/bin/python light_desktop.py --smoke-test` exited 0. A separate isolated QtWebEngine probe created distinct named profiles and loaded both unauthenticated public provider pages: YouTube Music completed at `https://music.youtube.com/`; Apple Music completed after its normal public redirect to `https://music.apple.com/us/new`. Offscreen Chromium reported Vulkan/GBM unavailability and used software rendering; these warnings did not prevent either load. No account login, playback, local backend, real configuration, or controller was used.

## Runtime limitations requiring honest checks

- No authenticated provider playback, account mutation, DRM, subscription, or live hardware write was attempted.
- Qt WebEngine codec/DRM support varies by distribution. Apple Music and YouTube Music login/playback must be verified in the built runtime; this implementation does not report simulated success.
- Provider DOM/selectors and auth redirect hosts can change. Fixed media observation/control may degrade while the official provider UI itself still works.
- Popup-based authentication was not exercised. Main-frame redirects for the known Google/Apple auth hosts are allowed, but an unobserved provider popup flow may require a narrowly scoped compatibility adjustment.
- Request interception blocks direct private/loopback URL forms. A random per-process header independently prevents unauthenticated backend use through a missed alias or DNS rebinding; it is injected only by the exact-origin local profile and is never exposed to provider profiles.
- Official provider pages are the catalog/library/playlist experience. There is no Youtopia requirement or provider-account playlist emulation.
