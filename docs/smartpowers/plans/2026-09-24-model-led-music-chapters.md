# Model-led music chapters

Goal: Smart Director's existing WLED-mic/DDP show must evolve instead of holding one AI recipe indefinitely.

Approved direction: the user wants controller mic/beat data exposed to the models, with models choreographing creative shows. Keep the existing controller mic, four-strip DDP renderer, manual handoff, and steady TV mode. No extra writer or firmware change.

Architecture: collect a compact rolling audio-feature summary from the active WLED listener and renderer (not raw audio); asynchronously request bounded color, motion, speed, intensity, and spatial-composition chapters from the configured model. Apply chapter updates in place on the current renderer. Request a fresh chapter on a track change and at a bounded interval during extended music; include the previous look to discourage repetition. Never block the frame loop on model latency. Explicit user `music_show` overrides remain highest priority.

Acceptance:
- Two successive chapters with unchanged metadata can produce distinct looks; a track change requests a new one.
- The model receives measured beat/FFT/level information and current/previous look, not merely a song title.
- A delayed response cannot change TV/manual mode or a newer track's show.
- No additional WLED writer is started. Model failure leaves the current show running and retries later.
- Focused RED/GREEN tests, related suite, and live runtime/status readback; do not claim the active process runs new code until restarted and observed.

Tasks:
1. Add a bounded model-chapter function and tests: prompt contract, payload normalization, novelty, malformed response fallback.
2. Wire asynchronous chapter scheduling to Smart Director with rolling audio features and tests for time, ownership, stale results, manual overrides, and TV behavior.
3. Expose chapter status, run focused and broader tests; restart only with safe handoff and verify active mic/DDP and visible recipe change.
