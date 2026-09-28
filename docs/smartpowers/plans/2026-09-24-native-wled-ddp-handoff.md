# Smart Director native WLED / DDP handoff

Goal: a model-authored music chapter may use a native audio-reactive WLED effect or Lightss' DDP pixel renderer, without concurrent writers or losing controller-microphone input.

Approval: user asked to fix the remaining native-effect/DDP limitation after reviewing the WLED API. Preserve newly available live-catalog effects, existing four-strip topology, TV mode, manual override, brightness limits, and uncommitted work. Do not change firmware or wiring.

Architecture: obtain a read-only common catalog from both controllers' `/json/eff` and `/json/fxdata`. Offer only currently available 1D-capable audio-reactive effect IDs to the autonomous chapter model; keep broader allowed effects available to explicit WLED tools. A native chapter validates the chosen ID against a fresh common catalog **before** stopping DDP, then applies one `/json/state` payload to every configured segment. A DDP chapter stops native output via an explicit state handoff and starts one renderer. The mic multicast listener stays running in both engines. Failures must not start a second owner or silently claim a successful chapter.

Acceptance:
- Unit RED/GREEN: catalog intersection, reserved/2D exclusion, model engine schema, DDP→native→DDP, same-owner continuity, stale result rejection, native write failure, TV/manual transitions.
- Native effect is applied to both controllers and all four mapped segments; DDP frames stop before native POST and resume only after the return handoff.
- Runtime status exposes the active output engine and effect, not just `renderer.running`.
- Focused/full tests, offline build, and live readbacks on both controllers; restore active music if validation is interrupted.
