# Full music and UI repair implementation plan

**Goal:** Repair all user-reported regressions: meaningful repeatable EQ control, expressive music geometry without forced rainbow colors, additive AI control, readable model responses, song/artist header.
**Architecture:** Keep controller microphone and one DDP owner. Audio frames stay local; AI adjusts live renderer parameters. Native WLED and existing manual controls remain accessible. UI consumes status and uses a separate transient accent API.
**Constraints:** English only. Preserve all existing dirty work. No commits, firmware, power-limit, wiring or TV playback changes. Agents own disjoint files and do not restart server. Tests run with isolated HOME and PYTHONDONTWRITEBYTECODE=1.
**API contract:** smart_director.control_show(fleet, request:dict)->dict returns {ok:true,status:<full smart-director status>} or raises ValueError/RuntimeError. GET /api/music-show returns status envelope. POST same endpoint accepts action status/tune/accent. accent requires integer band 0..15, optional strength 0..1 default1; every request triggers a new transient even on repeated band. tune accepts motion (auto,flow,punch,chase,spectrum,comet,ripple), speed .25..4, brightness/intensity/colorfulness0..1, colors 1..5 RGB triplets0..210, composition_mode existing modes, band_gains array16 numbers0..3. Tune requires an active music renderer and applies session overrides without restarting. Changes through persistent settings take precedence over matching overrides. API rejects unknown fields and inactive music with actionable error. Renderer status adds band_gains, band_levels, accent_count,last_accent_band,accent_levels. Existing /api/smart-director config adds motions and extends music brightness to1 and speed4.

## Task 1: Runtime/backend (root)
Files realtime.py, smart_director.py, activity_intelligence.py, light_gui.py, tests/test_full_music_runtime.py.
Write failing tests for preserving palette hues, distinct spatial motions, repeated transient EQ accents, gains and range validation, live override retention, API guard/error paths, full model responses. Implement single-owner controls and purposeful palettes. Keep intentional white accents and arbitrary user colors; do not ban rainbow capability. AI default should use a small intentional palette rather than spectrum-spanning colors.

## Task 2: UI (ui_repair)
Files light_gui_html.py, new tests/test_full_music_ui.py. Restore full response text/history, safe text rendering, clear/persistence/error behavior. Report automated decision summaries accurately and deduplicate polls. Replace logo with song/artist/source header using playing local metadata or active foreground TV metadata; unknown and stale states clear correctly. EQ click uses latest rendered bins and POST accent API, with repeated clicks always sent and visible feedback; retain legacy fallback and existing controls. Expose 7 motions, brightness100%, speed400%, real band gains plus reset. Test actual shipped JavaScript and browser.

## Task 3: AI tools (ai_live_control)
Files ai_chat.py,mcp_light.py,new tests/test_ai_live_music_control.py. Add music_show tool to core schemas and dispatch, calling agreed control_show API, no other tools removed. Prompt distinguishes live tuning from explicit native/static handoff and preserves independent strip capabilities. Cover status/tune/accent/schema/errors/no tool pruning.

## Task 4: Integrate and verify
Review disjoint diffs, run focused and full pytest with isolated HOME, syntax/diff checks. Restart only verified workspace server. Verify live API, actual shipped browser UI at desktop/mobile widths, repeated EQ clicks changing renderer accent_count and actual preview frames, full long model reply via browser-only fixture, metadata header from real TV. Restore user settings after live tests. Record screenshots and before/after evidence in private /tmp artifact folder. Obtain final independent review and resolve important issues. Report physical visual quality as user-observed, not falsely camera-verified.

## Final verification — completed
- Runtime, UI and AI-tool tasks implemented; no commits, firmware or power-limit changes.
- Independent reviews found and resolved metadata retention, same-value override precedence, pre-tick cache race, oversized numeric validation, out-of-order EQ writes, mobile metadata overflow, and response history clear/reload regressions.
- Real AI read-only call verified exact renderer motion and speed via music_show; legacy custom prompt remains intact with additive runtime tool guidance. Raw pixel previews no longer truncate AI tool JSON; original model text remains available in raw_response.
- Full isolated suite: 1204 passed in 56.21s. Existing pytest_asyncio deprecation warnings remain (50310 warnings); no test failures.
- Actual Chrome UI: same-band repeated accents, rapid gain edits and reset, motion/speed/brightness, full safe response text, persistence/Clear, real TV title/artist, desktop/mobile, zero page errors.
- Both controllers report live DDP around30fps; outgoing successful frames/preview change. Physical appearance was not camera-verified.
- User settings restored; server PID658551 on port8123, served HTML byte-identical to current source. Artifacts/source backup/scoped patch: /tmp/lightss-full-repair-cabqqo8s.
