# Native Chrome provider tabs for phone-passkey QR

Status: approved by the user and implemented in 0.2.2; account-free native integration verification and packaging gates recorded in the implementation plan.

## Verified on this machine

- Google Chrome 152.0.7977.82, a separate temporary profile, and an ordinary localhost
  WebAuthn `get()` request with the `hybrid` transport display Chrome's native
  "Use your phone or tablet" QR prompt. No authenticator emulator was used.
- The same prompt renders with Chrome controlled through private inherited pipes
  (no listening debugging port).
- A Chrome application window embedded with `QWindow.fromWinId()` and
  `QWidget.createWindowContainer()` renders that QR prompt inside a Qt tab under X11.
- The host session is Wayland with XWayland available. The prototype uses Xvfb/X11;
  native Wayland embedding is not established.
- No Google or Apple account authentication, phone scan, or Bluetooth handshake was
  completed. This is evidence for a real QR prompt, not a successful service login.
- Both debugging transports report `navigator.webdriver === true`. Provider policy
  may still reject controlled browser sessions; do not conceal this or claim compatibility.

## Recommended integration

Preserve the existing Qt Lightss control page and authenticated loopback backend.
Replace only provider renderers on supported Linux/X11 systems with full Chrome
application windows embedded into the existing provider tabs. Keep separate
Lightss-owned persistent Chrome profiles; never import personal browser credentials
or migrate cookies through the backend or model.

Use inherited pipes for a private, bounded CDP command channel. Keep existing fixed
navigation, transport, metadata, end-event and observer-clock contracts. No general
script-evaluation or debugging API becomes available to web pages or local HTTP clients.
The profile remains the player profile after sign-in: there is no auth-only handoff to Qt.

A browser worker must keep pipe reads and browser startup off the Qt GUI thread,
reject unsolicited/cross-origin observations, time out commands, invalidate stale
navigation callbacks, and terminate only its own process tree during shutdown.
Native-window discovery must match the owned Chrome process, not arbitrary window
names, and must gracefully handle missing display/embedding/browser support.

Retain Qt as an explicitly limited fallback, with clear identification of the active
provider engine. Do not disable CORS, certificate checking, or browser sandboxing.

## Alternatives

1. Dedicated full-Chrome player windows: same authentication engine, simpler window
   lifetime, but changes the single-window interaction. The initial approval question
   offered this before native tab embedding was proven.
2. Implement hybrid FIDO inside Qt or maintain a Qt/Chromium fork: substantially larger
   cryptographic, Bluetooth, browser-maintenance and packaging responsibility. Not the
   preferred repair for this desktop app.

## Required acceptance gates

- Regression tests for pipe framing, bounded queues, failure/timeout cleanup, profile
  isolation, origin checks, and command/observation compatibility.
- Actual app-tab QR screenshot from a controlled WebAuthn origin; explicit distinction
  between rendered QR and phone/provider authentication.
- Manual Google and Apple authentication by the account owner, including phone scan
  and proximity verification, followed by playback, pause, seek and clock checks.
- Existing full Python suite; wheel, source and Debian rebuild; packaged desktop smoke.

## Reproduction artifacts (temporary, no account data)

`/tmp/lightss-qr-spike/index.html`: controlled WebAuthn page.
`/tmp/lightss-qr-spike/pipe_probe.py`: isolated Chrome/private-pipe reproduction.
`/tmp/lightss-qr-spike/embed_probe.py`: Qt-tab embedding reproduction.
`/tmp/lightss-qr-spike/embedded-qr-screen.png`: native QR inside Qt tab.

These are disposable probes, not production modules. Their timer-based screenshot
capture and external-window lookup are deliberately not proposed as application code.
