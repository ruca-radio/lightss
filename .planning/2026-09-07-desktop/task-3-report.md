# Task 3 report: durable library and provider adapter

## Outcome

- Added stdlib SQLite `Library(path=None)` with durable local playlists and queue.
- Added transactional create/rename/delete/add/remove/reorder and queue add/remove/reorder/clear.
- Validated provider sources, provider IDs, item kinds, text, indexes, and collection bounds.
- Routed active desktop status/transport/search through `desktop_bridge.registry` while preserving explicitly injected legacy transports.
- Desktop catalog search and account-playlist responses are explicitly `delegated`; passive playlist listing does not enqueue navigation.
- Added Apple catalog artists and corrected generic album/playlist/artist playback mapping.

## Payload contract

`Library.snapshot()` and `Library.handle(payload)` return `{ok, message?, playlists, queue}`. A playlist is `{id, name, items}`. Canonical items are `{source, provider_id, kind, title, artist, album, artwork}`. Actions:

- `create{name}`
- `rename{playlist_id,name}`
- `delete{playlist_id}`
- `add{playlist_id,item,index?}`
- `remove{playlist_id,index}`
- `reorder{playlist_id,from_index,to_index}`
- `queue_add{item,index?}`
- `queue_remove{index}`
- `queue_reorder{from_index,to_index}`
- `queue_clear`

## Verification

- RED observed for missing `player_library`, desktop routing/delegation, Apple artist search, album-kind playback, and desktop empty-text normalization.
- Focused tests: `20 passed` (`tests/test_player_library.py`, `tests/test_desktop_player_adapter.py`).
- Combined Task 1/3 run during concurrent Task 1 editing: Task 3 and existing audio tests passed; one Task 1-owned transient failure remained because its test expected `DesktopBridgeRegistry.activate()` before that implementation landed.
- No remote provider calls or account mutations were made.

## Limitations

- Authentication, DRM, and actual provider-page playback remain runtime checks owned by the desktop integration.
- Provider account playlists are intentionally not copied into or mutated by the local SQLite library.
- Local playlist items store provider references and metadata, not media.

## Independent read-only quality review

**Verdict: not approved; partial rework required.**

### Blocking finding

1. Provider item mapping is not compatible with the actual playback consumers.
   `audio_player.player_command()` correctly preserves generic item kinds when
   it queues a desktop `play_id`, but `light_desktop.provider_command_url()`
   sends every YouTube kind other than song/playlist to
   `https://music.youtube.com/channel/<id>`. A YouTube Music album ID is
   therefore treated as a channel ID. In the legacy Apple path,
   `audio_player.player_command()` now returns `client_play.album` and
   `client_play.artist`, while `handleAppleClientCommand()` only calls
   `MusicKit.setQueue()` for song or playlist and then plays the previous
   queue. The focused tests stop at the producer-side mapping and do not
   exercise either consumer.

   Required proof to close: add consumer-level tests for YouTube album and
   Apple album playback, then map each supported kind to a provider-supported
   navigation/queue operation. Unsupported artist playback must return an
   honest unsupported/delegated result rather than playing stale media.

### Reviewed boundaries

- SQLite mutations use `BEGIN IMMEDIATE`, parameterized playlist IDs, foreign
  keys, bounded collections, and delete/reinsert ordering within one
  transaction. Dynamic table names are selected only by internal call sites,
  not request payloads. An additional probe created 12 playlists concurrently
  through 12 independent `Library` instances: 12/12 operations succeeded and
  produced 12 unique durable rows.
- The library path is constructor-owned and is not accepted in `handle()`;
  provider IDs reject slash/path traversal syntax. Queue and playlist item
  source/kind/ID/index limits are validated before mutation.
- An active desktop registry is preferred before the legacy Youtopia/default
  transport path. Explicitly injected transports retain legacy behavior, so
  desktop mode has no mandatory Youtopia companion dependency in these
  adapter branches.
- The mood failure change does not persist ambient fallback payloads, exposes
  failure status, coalesces repeats within a running session, and permits a
  later session to retry. Its three focused tests cover those claims.

### Verification evidence

- `68 passed` across player library, desktop adapter, existing audio player,
  mood failure cache, and existing mood orchestrator tests.
- Ruff `--select F` passed for all reviewed implementation and focused test
  files.
- No source edits, provider requests, account mutations, or hardware access
  were performed by this review.
