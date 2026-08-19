# Integrated Audio Player — Design

Date: 2026-08-19
Status: First slice implemented on `integrated-audio-player`

## Purpose

Play music from the Lightss controller itself — YouTube Music and Apple Music —
so lighting, now-playing, and transport live in one room control surface.

## First slice (this branch)

- Controller GUI card: source picker, now playing, previous / play-pause / next.
- `GET /api/player?source=` and `POST /api/player` `{source, command}`.
- YouTube Music talks to the local Youtopia companion (`/api/v1/state`, `/api/v1/command`).
- Apple Music is a first-class source. Playback waits on a MusicKit developer token
  and user authorization; the controller does not pretend it is playing.

## Later slices

- MusicKit JS authorize + play in the GUI (or a small local MusicKit proxy).
- Search / queue / volume.
- Pair player commands with `design_look` / mood orchestration automatically.
