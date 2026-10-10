# Integrate python-wled

Goal: use pinned `wled==1.1.1` for standard device reads and live observations,
preserving Lightss's public interfaces, raw JSON, fleet mapping, controller
audio, integrated player, and rendering ownership.

Approval: user explicitly requested implementation and parallel agents.
Base: `main` at `31937b0`. Isolated branch: `integrate/python-wled`.

Parallel ownership:

1. Transport worker: `wled_transport.py`, `lightctl.py`, transport regressions.
   Bridge async upstream reads into existing synchronous LightClient methods;
   preserve custom-port/HTTPS fallback and advanced validated JSON writes.
2. Observer worker: `wled_observer.py`, `fleet.py`, `light_gui.py`, observer and
   lifecycle regressions. Connect live observations to fleet state, recover
   connections, reject stale data, close replaced clients and sessions.
3. Independent reviewer: verify actual changes and upstream compatibility;
   flag concrete ownership, cancellation, state-shape, and timeout failures.
4. Root: dependencies/lock, documentation, integration and live read-only QA.

Acceptance: meaningful regressions pass, full suite passes with isolated config,
source/wheel parity holds, both live controllers can be observed through the
production integration, and runtime shutdown releases sessions/worker threads.
No firmware or persistent controller/configuration changes, remote publication,
or real model-authored hardware writes are part of verification.

Existing baseline: 1418 passing tests on merged main; dependency deprecation
warnings remain. Both controllers run WLED 16.0.1. A standalone upstream1.1.1
read-only probe already read both devices and connected their WebSockets.

Additional user scope: fix VU meters and intermittent audio-unavailable status.
Root reproduced fresh audio being cleared by manual/older director status and
immediate EventSource reconnect handling. Regressions cover precedence, expiry,
queued-frame cleanup, visibility with lights off and bounded browser meter glow.

Verification recorded during implementation:

- Independent review approved transport, observers, raw state and lifecycle.
- Both live devices read through production HTTP/WS integration; segment IDs
  and AudioReactive usermod metadata survived; all worker threads closed.
- Initial full suite: 1470 passed. Final meter refinements require final checks.
- Desktop/mobile Chrome: both controllers connected, middle-right targeting,
  a measured test level displayed 60%, brief reconnect preserved it, expired
  readings and peaks reset, no JavaScript errors. Test audio was injected;
  actual UDP audio reception was unavailable during the live probe.
- Live multicast membership, route and socket binding were correct; the left
  device reported active microphone/send mode, but no audio packets arrived
  at this computer during the probe. No router/firmware changes were made.

Final merged-main checks: 1473 tests passed with the existing dependency
deprecations; all 46 wheel modules match source and import outside the checkout;
the pinned dependency is present. Independent review approved the final VU
refinements. Desktop/mobile Chrome checks passed again on merged main.

GUI restarted successfully under transient user unit `lightss-gui.service` on
port 8123 with existing configuration, Music Director enabled and both devices
connected. Repeated live audio checks remained inactive with no packets seen.
UFW is active, but this session cannot authenticate sudo to inspect/apply rules.
The scoped administrator follow-up is documented in `docs/controller-audio-firewall.md`.
