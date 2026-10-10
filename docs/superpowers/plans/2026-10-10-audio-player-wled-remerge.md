# Combine the audio player with reliable WLED and song intelligence

Goal: merge the latest `integrated-audio-player` into `main`, preserving its
player and controller-audio features while repairing demonstrated regressions
in connectivity and song intelligence.

Authorization: the user requested combining and remerging these branches.
Execution is local; no remote publication or firmware/configuration changes.

Base: `main` at `7eb3e25`; latest player branch at `5f4048f`. The former is
already an ancestor of the latter, so retaining old files wholesale would
discard newer interfaces. Reconcile behavior and regression coverage instead.

1. Establish both baselines with the declared dependencies. Preserve local
   `.env`, build directories, logs, and modified tracked bytecode.
2. Trace HTTP requests, fleet state, rendered connection indicators, song
   identification, and the model/director context. Compare with `main` and
   reproduce concrete failures before changing implementation.
3. Add failing regressions for each demonstrated incompatibility; implement
   minimal repairs while retaining the latest player and four-strip controls.
4. Run the full suite, rendered JavaScript checks, package build/smoke checks,
   and read-only live controller probes. Perform a labeled self-review.
5. Commit the combined result and merge it locally into `main`. Recheck the
   resulting tree; keep a rollback reference to the original `main`.

Acceptance: latest player features retained; WLED state survives transient
transport failures; song context reaches the lighting decisions; test/build
checks pass; both original branch heads remain ancestors of the result.
Hardware writes and real song recognition require separate evidence and must
not be claimed solely from tests.

Verification before integration: `main` passed 145 existing tests. The latest
player baseline passed 1409 tests with one flaky Chrome pipe-peer test; its
fixture discarded coalesced commands and was corrected. Eight reconciliation
regressions cover interrupted reads/writes, player metadata, stale observations,
ambient-song context, genre, and runtime package completeness. The isolated
combined suite passed 1418 tests, and the related integration suite passed 103.
Python 3.14 dependency deprecations remain (primarily NumPy/ShazamIO).

The wheel and source distribution build successfully; all 45 installed modules
import from the wheel outside the checkout. Read-only runtime/browser checks at
127.0.0.1:8124 show both controllers connected, the latest player interface, and
working channel selection at 1440x1000 and 390x844, with no page exceptions.
Review is an inline self-review. Real provider playback, room-song accuracy,
and live model-authored lighting were not exercised.
