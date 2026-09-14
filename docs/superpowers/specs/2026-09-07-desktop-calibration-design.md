# Lightss standalone desktop and calibration

Approved direction: user accepted retaining the Python/WLED engine with integrated
playback, provider adapters, local timing, cached song context, and scan/review/apply
calibration, and requested a full rebuild on completion.

## Architecture
A PySide6 desktop window owns the existing local GUI server and isolated persistent
Qt WebEngine provider pages for YouTube Music and Apple Music. No Youtopia daemon is
required by desktop mode. The existing browser/tray entrypoints remain compatible.
Provider pages expose their own full catalog/library UI; a bounded in-process bridge
supplies transport and observed timing to Lightss, never arbitrary JavaScript or URLs
from HTTP clients. Local playlists/queue survive restarts in SQLite and are separate
from provider-account playlists. Unsupported provider actions are reported honestly.
Apple MusicKit remains available as the existing official API integration.

## Player contract
Sources: youtube_music and apple_music. Search covers tracks, artists, albums and
playlists. Provider web navigation is an explicit delegated result, not fabricated
catalog data. Local playlist create/rename/delete/add/remove/reorder and queue CRUD
are available within Lightss; playing entries dispatches validated provider IDs.
An observed playback clock is used directly, independent of player ownership. Audio
novelty/clock/beat work remains local; no recurring LLM or lyrics requests.

## Calibration
The UI has a Calibrate action. A scan reads every configured controller, reports bus
GPIOs, declared LED counts, segments and errors, and stages a reviewed proposal.
Apply requires the scan token, unchanged local config, fresh compatible hardware
facts and a complete valid fleet; it backs up and atomically writes local topology.
Missing controllers must never disappear from config. Guided identification restores
prior device state. Declared topology is not proof of physical geometry. Webcam use
is opt-in and cannot silently overwrite verified mappings. Never change GPIOs, power
limits or electrical bus types based on guesses.

## Isolation, failures and release
Remote provider pages receive no local filesystem/API bridge. Credentials stay in
isolated provider browser storage, not model prompts. Deny unexpected navigation and
permissions. Authentication/DRM compatibility and subscription requirements remain
visible runtime checks; no simulated success. Preserve existing dirty work; no git
reset, worktree replacement or commit. Test with fake hardware/provider boundaries.
Build wheel/sdist, desktop launcher and existing distro artifacts where tooling
permits, then smoke-test the built artifact in isolated settings. Do not stop the
existing lighting process merely to prove a build.
