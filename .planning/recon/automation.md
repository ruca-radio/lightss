# Recon: automation / TV taming / music AI
Read-only recon of `/home/rucaradio/lightss` (live GUI pid 699475 on :8123). No code/config/services touched.
Live persistence is `~/.config/lightsctl/config.json` (lightctl.py:1143-1145,1736); repo has no `lightsctl/config.json`.
Legacy `~/.config/lightss/config.json` is only a one-off migration source (lightctl.py:1157-1180). Live state below is from read-only GETs.

## 1. SmartDirector state machine
- One daemon thread `SmartDirector(threading.Thread)` (smart_director.py:91) started by module `start()` (smart_director.py:601), stopped by `stop(steady=True)` (smart_director.py:630).
- Selected mode `config['mode']` in auto|music|tv|manual (smart_director.py:43); effective `_mode` starts `manual` when persisted mode is manual, else `starting` (smart_director.py:103).
- `run()` calls `step()` every 0.2 s; observer thread polls FireTV every 3 s and classifies asynchronously, caching decisions 300 s keyed by observation tuple (smart_director.py:533-535,160-178,29,78-81,182-189).
- `step()`: manual -> `_manual_locked()` (stops renderer, zero output) (smart_director.py:492,434-435); music -> `_music()` + chapters (493-495); tv -> `_tv()` (496).
- auto decision order (smart_director.py:466-490): obs error/empty -> tv (470-472); connected+awake+`activity_hint=='tv'` -> tv "Video app" (473-474); playing+decision tv -> tv (475-476); playing and (hint music or classifier music>=.85) -> music (477-479); disconnected/asleep -> tv (480-481); hold music through metadata gap while `unknown_hold_s` 12 s (482-483); else tv "Uncertain content" (484); mic quiet `silence_hold_s` 20 s forces tv (462-464,485-486); entering music debounced `debounce_s` 6 s (487-490).
- status payload: running/selected_mode/mode/reason/show_overrides/output_engine/native_effect/chapters{applied,track,pending,last_error}/brightness_percent/audio/renderer/tv/intelligence/last_error (smart_director.py:500-515).
- Process lock: flock(LOCK_EX|LOCK_NB) on `<configdir>/smart-director.lock`, "another instance owns automatic lighting" (smart_director.py:575-584); released on clean shutdown (587-594,550).
- `start()` stops MCP timers, legacy music_director, shows and realtime before creating the director (smart_director.py:608-614); an existing instance is reconfigured in place (605-607).
- Any non-owned fleet write triggers `manual_override()` -> mode=manual and persists it (smart_director.py:650-655,437-444; fleet.py:502-506; owned writes at 84-88,232-233). This silently disables auto.

## 2. TV tame today
- `_tv()` stops the renderer, forces engine ddp, computes scheduled brightness and posts `{on,bri,transition,seg:[{fx:0,frz:false,on,bri:255,col:[theme]}]}` (smart_director.py:239-254). No segment id -> fleet fans the payload to all 4 strips on both controllers (fleet.py:490-511,507-511).
- Color is one of 3 hardcoded themes: warm [210,145,85], neutral, blue (smart_director.py:28; validation 44). No AI colors, no per-strip variation, no motion: "tame" = solid fx 0 at low brightness.
- Brightness by local clock (America/Detroit): day 30%, evening 15%, night 5% (smart_director.py:70-75,22-27). Repost only if (brightness,theme) changed or not already tv; transition 200 when already showing tv, 30 when switching in (246-252).
- TV is also auto's resting fallback (disconnected/asleep/unknown/uncertain, 480-484) and explicit mode tv always applies it (465,496).
- `stop(steady=True)` writes one final TV frame before shutdown (smart_director.py:637-639), so disabling leaves the steady theme on the wall.

## 3. Music path (per-song chapters)
- Music mode starts the DDP renderer `realtime.AudioReactiveRunner` at 30 fps fed by the controller-mic snapshot and classifier palette (smart_director.py:277-290); renderer owns frames, the model only supplies a target look (music_chapters.py:1-4; realtime.py:385).
- Chapter key = FireTV `media_session.description` only (smart_director.py:363-370); SmartDirector consumes no local MPRIS/now-playing (imports 18-20; observe at 155-158). Key change resets look and is due immediately (first ever +15 s); absent description -> key 'ambient' (365-370).
- Cadence: CHAPTER_FIRST_S 15 s, CHAPTER_INTERVAL_S 120 s, CHAPTER_RETRY_S 30 s (smart_director.py:30-32,368,398,432); chapters only run while mic active and level > .025 (401-402).
- Chapter AI context: 16-band/level/beat/bpm summary (smart_director.py:349-360; wled_audio.py:354-394), previous look + last 5 chapters (406-411), up to 8 common audio effects + palette names (music chapters request, 420-430; show_library.py:74-94), measured per-strip lighting (424-426; lighting_context.py:117-131), optional palette_hint (427-429; show_library.py:154-181).
- `music_chapters.design_chapter` accepts: colors 2-5 (119-128), engine ddp|native (130-134), native effect id from the common catalog + optional WLED speed/intensity bytes (135-143,44-86), motion enum (144-146), composition_mode (147-149), speed .25-4 and intensity 0-1 (150-153), colorfulness (154-156), 16 band_gains 0-3 (157-161); near-duplicate looks return None (162-167).
- DDP-controllable dimensions: palette stops, motion (auto/flow/punch/chase/spectrum/comet/ripple), speed, intensity, colorfulness, 16 band gains, composition_mode (smart_director.py:313-322; realtime.py:475-499,MOTIONS 396).
- Composition is the only spatial control: modes group the four wall columns -- unison, independent (per strip), pairs, center_vs_outer, left_vs_right, alternating, random_groups (realtime.py:160-167,364-382; color_lab.py:57-65).
- A chapter cannot set brightness (design_chapter returns none; `_music` hard-sets config `music_brightness`, 313; native sets it at 338), cannot target an individual channel/segment, and cannot choose a WLED palette id. Only UI/MCP `tune` can set brightness (663-709,735-738).
- Native engine: validates a common 1D audio effect (smart_director.py:329-337; show_library.py:183-191), stops DDP, posts one WLED effect to all controllers (338-347); fleet posts carry `udpn.nn` no-notify (fleet.py:482-483,507-511), so left/right effect phase is probably unsynchronized (unverified).
- Track-level AI also runs before chapters: `activity_intelligence.classify_context` returns kind/confidence/colors/composition_mode/show recipe (activity_intelligence.py:109-216,25-38); `_music` merges classifier look <- chapter look <- user overrides (smart_director.py:300-322).
- Live tuning: `control_show()` tune/accent/native/ddp on the running renderer (smart_director.py:663-739); MCP `music_show` (mcp_light.py:780-830,1989-1992); HTTP /api/music-show (light_gui.py:4461-4463,4927-4938).

## 4. Inputs
- FireTV: `firetv.observe()` runs read-only dumpsys power/window/media_session and needs `firetv.observation_enabled` (firetv.py:274-302,258-271), returning connected/awake/foreground_app/media_session/activity_hint/reason (274-281). App sets: video apps -> hint tv, music apps -> music only when state==3; mixed apps (com.amazon.firetv.youtube, com.google.android.youtube.tv) -> unknown (firetv.py:204-208,239-255). Media description is untrusted text (211-236).
- `activity_intelligence.classify_context`: definitive hint tv -> tv 1.0 (144-146); requires an active playing foreground session (148-155); hint music -> music 1.0 (157-158); regex disqualifiers (interview/movie/episode/podcast...) -> tv .95 (16-19,160-163); strong music-title patterns -> music .9 (20-24,164-165); otherwise one AI classifier call, music accepted only at confidence >= .85 (167-204); failure -> unknown 0.0 (184-192).
- wled_audio: `WledAudioListener` binds UDP 11988 multicast 239.0.0.1 (wled_audio.py:38-39,93-148); `summarize_audio` yields active/level/16-band FFT/peak/beat/BPM+confidence with 2/10/30 s windows (354-394). Live `/api/audio` shows packets alive from 10.27.27.110 (handler light_gui.py:4766-4770).
- `lighting_context.observe_lighting` returns measured per-strip pixel samples/dominant colors/lit fraction, used as chapter observation (lighting_context.py:16-57,117-131; smart_director.py:424-426).
- Legacy music_director (the currently running automation): polls MPRIS first, then room-mic Shazam, using FireTV description only as a song-change trigger (music_director.py:336-380,303-334), then applies one of 7 keyword/AI-labelled static mood looks per track (music_director.py:147-150,293-302,378-418). It has no TV-taming path.

## 5. Enablement / config (live)
- Live `smart_director`: enabled=false, mode=manual, tv_theme=warm, music_motion=punch, music_speed=1.25, intensity/colorfulness=1 (read-only GET /api/smart-director; handler light_gui.py:4465-4467).
- Live `music_director`: enabled=true and running; started at boot by main() (light_gui.py:5661-5668) -- GET /api/music-director (light_gui.py:4567-4569).
- Live `firetv`: enabled=true, observation_enabled=true (GET /api/tv-observation; light_gui.py:4452-4455). At recon time it saw foreground `com.amazon.firetv.youtube`, state 3, activity_hint "unknown (mixed app)", connected/awake true -- i.e. YouTube playing on the TV.
- GUI surface: Music Director checkbox (light_gui_html.py:751) and "Start Music Director" button (5323,5377) drive the legacy director via POST /api/music-director (light_gui_html.py:3542-3549); "Observe TV (read-only)" checkbox (758-761; POST light_gui.py:4950-4963). The Smart lighting card is absent by design (tests/test_smart_director_html.py:12-16 asserts it is not rendered); remaining smartDirector* JS has no DOM elements in the served HTML. Auto/TV is therefore API-only (or desktop shell).
- POST /api/smart-director -> `configure_smart_director` (light_gui.py:4927-4945,1079-1120): validates keys (1088-1089), persists under CONFIG_LOCK (1091-1101), forces music_director.enabled=false and stops it (1097-1102), stops conflicting writers mode1/mood_session/schedule/fade_timer/_cycle/_sunrise/autonomous (1068-1076), starts + reconfigures with posted updates (1104-1109); disabling calls stop(steady=True) (1114-1115).
- Mutual exclusion: smart start stops legacy music director/shows/realtime (smart_director.py:610-611); enabling legacy music director while smart owns the wall raises "Smart Director owns the wall" (light_gui.py:3820-3833,4206-4217). MCP `music_director` only starts the legacy director (mcp_light.py:2047-2061); no MCP/CLI enables smart director.
- Auto-start persistence is uneven: light_desktop.py:148 starts persisted smart director, but `light_gui.py` main only restarts legacy music director (5661) -- the live process is `.venv/bin/python light_gui.py`, so enabled=true would not resume after restart in this launch mode.
- Enabling auto today would acquire the lock, bind the mic listener, poll adb, stop legacy director/writers, then act on the persisted mode. With live mode=manual it would emit nothing until mode is also set auto (smart_director.py:103,492).
- AI key availability unverified: main loads repo .env (light_gui.py:5632); that file is blocked by secret-path policy. AI helpers degrade to None (look_agents.py:99-133; music_chapters.py:113-118; show_library.py:176-181). Live AI: default gpt-6-luna, colorist/critic/motion gpt-4o, librarian enabled on ollama deepseek-v4.1-flash (live config `ai`; preferred by palette_hint, show_library.py:142-152).

## 6. Gaps vs goals
(a) TV/YouTube on -> lights tame themselves
- Not active now: smart director off, legacy music director running, wall bright/effect-driven (left bri 200/fx 175/pal 37 orange; right bri 128/fx 110 purple via GET /api/state, handler light_gui.py:4662) while YouTube plays. Legacy director has no tame path.
- Even enabled, taming is generic: fixed theme + schedule brightness; classifier TV colors are produced (activity_intelligence.py:100-106) but ignored as `_tv` uses THEMES only (smart_director.py:245-251). No dim/soft-motion tame option.
- YouTube is a mixed app -> activity_hint unknown (firetv.py:208,246-247); classification then depends on title regexes or one AI call, with music accepted at >=.85 (activity_intelligence.py:160-204). The live description contains "review", so it would hit the tv disqualifier at .95 (16-19,160-163) -- but smart director is not running to apply it. Videos without those keywords can flip to music and animate.
- Local/PC YouTube (Chrome) is invisible: inputs are FireTV + controller mic only (smart_director.py:155-158); no MPRIS/now-playing consumption.
- Taming needs firetv.observation_enabled; if off, auto pins to steady tv forever (firetv.py:277-283; smart_director.py:480-481).
- Any manual light write flips smart director to manual and persists mode=manual (smart_director.py:650-655,437-444); the live config already carries mode=manual, so re-enabling needs explicit mode=auto.
- No GUI to enable/inspect auto (card removed, tests/test_smart_director_html.py:12-16), so the requirement cannot be switched on from the UI today.

(b) AI picks colors/palettes/effects from mood/song/beat
- Already implemented for music: classifier palette/composition/show recipe, AI chapters every 120 s using mic spectrum/BPM/palette hint, plus native effect selection from real catalogs. Inputs are limited to 30 s mic summary + FFT + previous look + TV description + measured pixels (smart_director.py:349-360,406-429; music_chapters.py:103-167).
- No genre/lyrics/mood analysis beyond regex + one classifier call on app/description (activity_intelligence.py:160-204); track metadata is untrusted display text (firetv.py:211-236; music_chapters.py:39).
- Per-song chapters only for FireTV descriptions; local playback collapses to one 'ambient' key (smart_director.py:363-370), so per-song is not achieved for the local player.
- Chapters cannot set brightness or target individual channels/segments; DDP palette is stops only, no WLED palette id (music_chapters.py:129; realtime.py:160-167).
- Cadence is fixed 120 s, not beat/section driven; collected beat history `_chapter_beats` is only appended, never read (smart_director.py:460).
- TV never gets AI color (see a). Native effect phase across the two controllers is likely unsynced (fleet.py:482-483) -- unverified.

## 7. Recommended minimal changes
- Add a media/`tame` path to `_tv()` that consumes `decision['colors']`/`composition_mode` for kind tv, keeping THEMES as fallback (smart_director.py:239-251; activity_intelligence.py:100-106).
- Make YouTube deterministic without an AI round-trip: treat mixed-app + video-ish description as tv via firetv/activity_intelligence regexes; or add a confidence-gated shortcut (activity_intelligence.py:160-165; firetv.py:208).
- Feed local now-playing/MPRIS into SmartDirector as an extra music input and chapter key (smart_director.py:155-158,363-370; music_recognizer.now_playing light_gui.py:850-886).
- Stop persisting mode=manual on incidental external writes (or add an explicit Auto resume), and restore a one-click auto/TV toggle + auto-start in the light_gui main path (smart_director.py:437-444,650-655; light_gui.py:4219-4227,5632-5668; light_desktop.py:148).
- Extend chapter schema + validation with brightness and per-channel/segment targeting mapped through fleet channels (music_chapters.py:150-161; smart_director.py:663-709; fleet.py:490-511; realtime.py:160-167).
- Optionally render TV tame as very low-intensity DDP motion instead of solid fx 0 (smart_director.py:239-254; realtime.py:385-499).
- Use beat/section events to trigger chapters earlier (smart_director.py:30-32,460; wled_audio.py:354-394).
Uncertain / not verified: .env AI keys (blocked), native left/right phase sync, origin of persisted mode=manual, whether chapter AI calls succeed now (no show-library.json cache exists yet, show_library.py:493-499).
