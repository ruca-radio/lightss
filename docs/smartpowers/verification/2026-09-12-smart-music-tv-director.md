# Smart music / TV director verification

## Implemented

Controller microphone multicast receiver -> normalized FFT/energy/beat snapshots ->
30-fps, topology-aware DDP renderer. A separate read-only TV observer and cached,
tool-free metadata classifier choose music or steady TV mode. Music and TV manual
modes are available; manual Lightss writes stop the automatic renderer. A process
lock prevents a second Lightss instance from starting another automatic renderer.

Steady TV defaults: warm theme, day 07–18 at 30%, evening 18–22 at 15%, night
22–07 at 5%, America/Detroit. Music output is capped at 65%. Observation permission
is enabled in this installation; TV control permission remains false.

## Live evidence

Artifacts: `/tmp/lightss-smart-live/` (configuration backup contains private settings;
keep it local). The baseline source backup is recorded in
`/tmp/lightss-smart-director-baseline-path`. Existing user changes were preserved.

- Both WLED 16.0.1 controllers `.110` and `.112` reported `live=true`, `lm=DDP`,
  `lip=10.27.27.96` during music.
- Across a three-second interval: 90 rendered frames and 129 fresh controller-mic
  packets. Microphone reception continued while both controllers displayed DDP.
- TV mode: both controllers returned `live=false`, brightness 13/255, all four
  segments `fx=0`, `frz=false`, warm RGB `[210,145,85]`.
- Manual RGB/brightness command stopped streaming and remained applied on both
  controllers. A late browser beat request was rejected. Auto was restored.
- Actual YouTube media metadata was classified as music through both a bounded
  configured-model call and the strong music-title path on different tracks.
- A second process was refused automatic-output ownership without sending frames.
- Hardware configuration, microphone pins, firmware, and TV playback were not changed.

## Browser and build

Browser plugin unavailable; used installed Playwright with Chrome. Tested desktop
1440x1000 and mobile 390x844. Apply changes, mode/brightness settings, unsaved-edit
preservation, and primary controller-mic Music action passed without JavaScript
errors. Screenshots and JSON results are in the artifact directory. Wheel and sdist
build successfully with both new modules included.

## Boundaries

TV classification uses foreground app/media metadata, not a perfect acoustic
speech-versus-music classifier. Ambiguous YouTube content can require the explicit
TV or Music selector. Forced TV behavior was checked on the physical controllers;
video-app and uncertain-content decisions were exercised with controlled tests,
without changing the user's current TV playback. Beat/BPM values are estimates.
Controller readback proves receipt and state, not a subjective visual assessment
of synchronization. The app must remain running for its microphone-driven show.

## Final gate

- Isolated full suite: **1121 passed** (49.37 seconds; existing warning volume retained).
- Final wheel module hashes match current source; `git diff --check` passes.
- Active GUI restarted after all module edits, PID 142107, port 8123.
- Real-backend browser TV Apply -> Auto -> running renderer passed on desktop/mobile,
  with no page errors. Final physical mode checks were repeated after this restart.
- Final readback has **Music** override selected, microphone active, renderer running,
  and no director error. Select **Auto** for automatic TV/music switching; explicit
  Music intentionally bypasses content classification. TV control remains disabled.
