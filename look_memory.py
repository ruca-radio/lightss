#!/usr/bin/env python3
"""Persistent lightweight memory for Lightss looks and user feedback."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import lightctl

MAX_EVENTS = 100


def memory_path() -> str:
    return os.path.join(lightctl._SCENE_DIR, "look_memory.json")


def load() -> dict:
    try:
        with open(memory_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"events": []}
    except Exception:
        return {"events": []}
    if not isinstance(data, dict):
        return {"events": []}
    events = data.get("events")
    if not isinstance(events, list):
        data["events"] = []
    return data


def save(data: dict) -> None:
    events = data.get("events") if isinstance(data.get("events"), list) else []
    data["events"] = events[-MAX_EVENTS:]
    os.makedirs(lightctl._SCENE_DIR, exist_ok=True)
    tmp = memory_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, memory_path())


def _tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",")]
    return [str(tag).strip().lower() for tag in tags if str(tag).strip()]


def record_look(
    *,
    source: str = "dynamic_scene",
    action: str = "dynamic_scene",
    prompt: str = "",
    mood: str = "",
    context: str = "",
    parameters: dict | None = None,
    summary: str = "",
    payload_summary: dict | None = None,
    tags: list[str] | str | None = None,
    notes: str = "",
) -> str:
    data = load()
    look_id = uuid.uuid4().hex[:12]
    event = {
        "id": look_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
        "action": action,
        "prompt": prompt,
        "mood": mood,
        "context": context,
        "parameters": parameters or {},
        "summary": summary,
        "payload_summary": payload_summary or {},
        "feedback_score": 0,
        "tags": _tags(tags),
        "notes": notes,
    }
    data.setdefault("events", []).append(event)
    save(data)
    return look_id


def last_look() -> dict | None:
    events = load().get("events", [])
    return events[-1] if events else None


def add_feedback(
    look_id: str | None = None,
    score: int | None = None,
    tags: list[str] | str | None = None,
    notes: str = "",
    applies_to: str = "last",
) -> str | None:
    data = load()
    events = data.get("events", [])
    if not events:
        return None
    target = None
    if look_id:
        target = next((event for event in events if event.get("id") == look_id), None)
    if target is None and applies_to == "last":
        target = events[-1]
    if target is None:
        return None
    if score is not None:
        target["feedback_score"] = max(-1, min(1, int(score)))
    merged = list(dict.fromkeys([*_tags(target.get("tags")), *_tags(tags)]))
    target["tags"] = merged
    if notes:
        prior = str(target.get("notes") or "")
        target["notes"] = (prior + "\n" + notes).strip() if prior else notes
    save(data)
    return str(target.get("id"))


def memory_summary(limit: int = 8) -> str:
    events = load().get("events", [])[-limit:]
    if not events:
        return "Look memory: no prior feedback yet."
    liked: dict[str, int] = {}
    avoid: dict[str, int] = {}
    lines = ["Look memory (use this for future scene choices):"]
    for event in events:
        score = int(event.get("feedback_score") or 0)
        tags = _tags(event.get("tags"))
        bucket = liked if score > 0 else avoid if score < 0 else None
        if bucket is not None:
            for tag in tags:
                bucket[tag] = bucket.get(tag, 0) + 1
        note = str(event.get("notes") or "").strip()
        if score or note:
            lines.append(f"- {event.get('id')}: score {score}, tags {tags or []}, notes: {note or '(none)'}")
    if liked:
        lines.append("Repeat liked traits: " + ", ".join(sorted(liked)))
    if avoid:
        lines.append("Avoid disliked/problem traits: " + ", ".join(sorted(avoid)))
    return "\n".join(lines)
