# Music Recognition Polling Reliability Design

## Goal

Keep browser Music Mode checking for a new song every 30 seconds even when an earlier metadata or recognition request stalls or fails.

## Existing Failure

`startMusicMode()` currently awaits the initial `runMusicRecognitionCycle(true)` before it creates the recurring timer. If that cycle never settles, the timer is never installed. A stalled request can also leave the cycle busy indefinitely, causing every later timer tick to be ignored.

## Design

- Keep the browser as the only explicit Shazam owner in Music Mode.
- Install the recurring 30-second scheduler before awaiting the initial recognition cycle.
- Add bounded request handling to the metadata, recognition, and song-matching requests used by the cycle.
- Continue using the existing `musicRecognitionBusy` guard so recognition cycles cannot overlap.
- Always release the busy guard in `finally`, preserving beat analysis and allowing the next scheduled attempt after errors or timeouts.
- Keep the visible next-match status at `Every 30s` between checks.

## Error Handling

A timed-out request is treated like a normal no-result or recognition error. The current cycle ends, Music Mode and beat processing remain active, and the next scheduled check proceeds. Stopping Music Mode clears the scheduler as it does today.

## Testing

Rendered-HTML contract tests will verify that:

1. The recurring timer is installed before the initial recognition cycle is awaited.
2. Recognition-cycle network requests have a timeout/abort path.
3. Browser Shazam remains the fallback when metadata is missing.
4. No competing server-side Shazam session is started.

The focused surface tests and full test suite will run after the implementation.
