import math

import color_lab
from activity_intelligence import classify_context


YOUTUBE = "com.amazon.firetv.youtube"
SPOTIFY = "com.spotify.tv.android"
NETFLIX = "com.netflix.ninja"


def observation(
    *,
    app=YOUTUBE,
    hint="unknown",
    description="An ambiguous title",
    active=True,
    state=3,
):
    return {
        "connected": True,
        "awake": True,
        "foreground_app": app,
        "activity_hint": hint,
        "media_session": {
            "package": app,
            "active": active,
            "state": state,
            "description": description,
        },
    }


def assert_safe_shape(result):
    assert set(result) == {
        "kind",
        "confidence",
        "colors",
        "composition_mode",
        "reason",
    }
    assert result["kind"] in {"music", "tv", "unknown"}
    assert math.isfinite(result["confidence"])
    assert 0.0 <= result["confidence"] <= 1.0
    assert 2 <= len(result["colors"]) <= 5
    assert all(
        len(rgb) == 3 and all(isinstance(channel, int) and 0 <= channel <= 210 for channel in rgb)
        for rgb in result["colors"]
    )
    assert result["composition_mode"] in color_lab.COMPOSITION_MODES
    assert isinstance(result["reason"], str) and result["reason"]


def test_definitive_tv_hint_wins_over_music_like_soundtrack_metadata():
    calls = []
    result = classify_context(
        observation(
            app=NETFLIX,
            hint="tv",
            description="Original Soundtrack - Official Music Video feat. Artist",
        ),
        complete=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert result["kind"] == "tv"
    assert result["confidence"] == 1.0
    assert calls == []
    assert_safe_shape(result)


def test_paused_or_inactive_media_is_unknown_without_classifier_call():
    for active, state in ((True, 2), (False, 3)):
        calls = []
        result = classify_context(
            observation(active=active, state=state, description="Song - Official Audio"),
            complete=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        assert result["kind"] == "unknown"
        assert calls == []
        assert_safe_shape(result)


def test_media_session_for_a_different_package_is_ignored():
    value = observation(app=YOUTUBE, description="Track - Official Audio")
    value["media_session"]["package"] = SPOTIFY
    calls = []

    result = classify_context(value, complete=lambda *args, **kwargs: calls.append((args, kwargs)))

    assert result["kind"] == "unknown"
    assert calls == []
    assert_safe_shape(result)


def test_active_foreground_music_only_app_is_music_without_ai():
    calls = []
    result = classify_context(
        observation(app=SPOTIFY, hint="music", description="Artist - Track"),
        complete=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert result["kind"] == "music"
    assert result["confidence"] == 1.0
    assert calls == []
    assert_safe_shape(result)


def test_mixed_app_classifier_output_is_whitelisted_normalized_and_bounded():
    calls = []

    def complete(settings, system, user, *, timeout):
        calls.append((settings, system, user, timeout))
        return {
            "kind": "music",
            "confidence": 0.93,
            "colors": [[255, -5, 30], "#00ff80"],
            "composition": "pairs",
            "action": "turn_off_all_lights",
            "tool_calls": [{"name": "realtime_start"}],
        }

    result = classify_context(
        observation(description="Nebula Session"),
        settings={"model": "fixture"},
        complete=complete,
    )

    assert result == {
        "kind": "music",
        "confidence": 0.93,
        "colors": [(210, 0, 30), (0, 210, 128)],
        "composition_mode": "pairs",
        "reason": "mixed-app classifier identified music",
    }
    assert calls[0][0] == {"model": "fixture"}
    assert calls[0][3] == 8


def test_classifier_music_below_threshold_is_conservatively_unknown():
    result = classify_context(
        observation(),
        complete=lambda *args, **kwargs: {
            "kind": "music",
            "confidence": 0.849,
            "colors": ["#ffffff", "#010203"],
            "composition": "random_groups",
        },
    )

    assert result["kind"] == "unknown"
    assert result["confidence"] == 0.849
    assert result["composition_mode"] == "random_groups"
    assert_safe_shape(result)


def test_classifier_malformed_nonfinite_or_error_results_fall_back_to_unknown():
    payloads = (
        None,
        [],
        {"kind": "music", "confidence": float("nan")},
        {"kind": "music", "confidence": float("inf")},
        {"kind": "concert", "confidence": 0.99},
        {"kind": "music", "confidence": "very"},
        {"kind": "music", "confidence": "0.95"},
    )
    for payload in payloads:
        result = classify_context(observation(), complete=lambda *args, _payload=payload, **kwargs: _payload)
        assert result["kind"] == "unknown"
        assert_safe_shape(result)

    def raises(*args, **kwargs):
        raise RuntimeError("provider failed")

    assert classify_context(observation(), complete=raises)["kind"] == "unknown"


def test_strong_music_patterns_apply_only_after_disqualifiers_in_mixed_app():
    positive = (
        "Artist - Track (Official Audio)",
        "Artist — Track [Official Music Video]",
        "Track lyric video",
        "Track lyrics",
        "Track remix",
        "Track feat. Guest",
        "Track ft. Guest",
    )
    for description in positive:
        result = classify_context(observation(description=description), complete=lambda *args, **kwargs: None)
        assert result["kind"] == "music", description
        assert result["confidence"] >= 0.85
        assert_safe_shape(result)

    for description in (
        "Artist interview - Official Audio",
        "Album review lyric video",
        "Movie trailer feat. Artist",
        "News episode: remix controversy",
        "Podcast gameplay with lyrics",
    ):
        calls = []
        result = classify_context(
            observation(description=description),
            complete=lambda *args, **kwargs: calls.append(True),
        )
        assert result["kind"] == "tv", description
        assert calls == []


def test_generic_hyphenated_youtube_title_is_not_assumed_to_be_music():
    result = classify_context(
        observation(description="Creator - A Day In The City"),
        complete=lambda *args, **kwargs: None,
    )

    assert result["kind"] == "unknown"
    assert_safe_shape(result)


def test_disconnected_or_sleeping_observation_never_calls_classifier():
    for field in ("connected", "awake"):
        value = observation()
        value[field] = False
        calls = []
        result = classify_context(value, complete=lambda *args, **kwargs: calls.append(True))
        assert result["kind"] == "unknown"
        assert calls == []
        assert_safe_shape(result)
