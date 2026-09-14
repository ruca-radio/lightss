# Read-only TV observation

Approved in conversation: independent TV observation without enabling wake/sleep, URL launching, or playback controls. This is stage one of the controller-microphone music / steady TV lighting design.

## Contract
- New `firetv.observation_enabled` boolean, default false, independent of existing `firetv.enabled` control permission.
- `firetv.observe(cfg=None)` returns power, foreground package, active foreground media-session summary, activity hint and reason. No keyevents, activity launches, light writes, raw dumps, or AI calls.
- Only bounded ADB dumpsys power/window/media_session reads and a bounded connection retry are permitted. A missing permission produces an inert result.
- Ignore inactive/background sessions. Known music-only apps may yield music; known video apps may yield tv; mixed YouTube and unfamiliar apps remain unknown even with song-like titles. Unknown power is never reported as off.
- GET `/api/tv-observation` reads; POST with exactly `{"enabled": bool}` changes only the observation permission, preserving all existing configuration.
- GUI has separate Observe TV (read-only) and TV control switches, independent status and an explicit refresh action. Enabling observation does not enable lighting automation.
- No raw dumps or media metadata added to persistent history.

## Future stage, not implemented here
Activity arbitration with manual override, debouncing, controller-mic DDP rendering and a steady TV theme with day/evening/night brightness. Read-only observations are evidence, not permission to change lights.

## Verification
Tests use recorded-shaped ADB fixtures, isolated configuration and HTTP requests. Browser validates the observation toggle independently of the control toggle. Live verification may read TV state but never wake, sleep, launch an app, or alter playback. Existing running GUI is not restarted implicitly.
