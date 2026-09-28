from __future__ import annotations

import dynamic_scenes
import fleet
import lightctl
import look_memory
import mcp_light
import pytest
import realtime


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(lightctl, "_SCENE_DIR", str(tmp_path))


class RecordingFleet:
    def __init__(self):
        self.installation, self.controllers = fleet.load_topology({})
        self.posts = []
        self._effect_ids = None

    def channels(self):
        return {
            segment.channel: (controller.name, seg_id)
            for controller in self.controllers
            for seg_id, segment in controller.segments.items()
        }

    def resolve(self, target):
        return [(target, None)]

    def names(self):
        return [controller.name for controller in self.controllers]

    def effect_ids(self, controller):
        return self._effect_ids

    def post_state(self, payload, target="all"):
        self.posts.append((target, payload))
        return {target: {"ok": True}}


def test_dynamic_catalog_is_canonical_safe_and_non_strobe_named():
    forbidden = ("strobe", "blink", "flash", "lightning", "fireworks", "sparkle", "2d")
    for effect_id in dynamic_scenes.SAFE_EFFECTS.values():
        assert effect_id in lightctl.SAFE_EFFECTS
        assert not any(word in lightctl.SAFE_EFFECTS[effect_id].lower() for word in forbidden)


def test_compose_is_deterministic_with_seed():
    f = RecordingFleet()
    first = dynamic_scenes.compose_dynamic_scene(f, mood="fire rise", strategy="vertical_rise", seed=123)
    second = dynamic_scenes.compose_dynamic_scene(f, mood="fire rise", strategy="vertical_rise", seed=123)
    assert first == second


def test_uses_calibrated_wall_order_segment_ids_and_pixels():
    f = RecordingFleet()
    composed = dynamic_scenes.compose_dynamic_scene(f, strategy="left_to_right", seed=1)
    assert sorted(composed) == ["brightness", "frames", "segments"]
    left, right = composed["segments"]["left"], composed["segments"]["right"]
    assert [seg["id"] for seg in left] == [0, 1]
    assert [seg["id"] for seg in right] == [1, 0]
    # right controller payload follows wall order: seg1 middle-right, seg0 far-right.
    assert [len(seg["pixels"]) for seg in left + right] == [34, 48, 47, 40]
    assert [(seg["start"], seg["stop"]) for seg in left + right] == [(0, 34), (34, 82), (0, 47), (47, 87)]
    # Whole-controller DDP buffers: full bus, no gaps, bounded bytes.
    assert len(composed["frames"]["left"]) == 82 * 3
    assert len(composed["frames"]["right"]) == 87 * 3
    assert all(0 <= byte <= 255 for frame in composed["frames"].values() for byte in frame)


def test_frames_are_bounded_and_reassert_topology_bounds():
    f = RecordingFleet()
    composed = dynamic_scenes.compose_dynamic_scene(f, mood="party", intensity=1, seed=9)
    assert set(composed["brightness"]) == {"left", "right"}
    assert all(bri <= 180 for bri in composed["brightness"].values())
    for controller, segments in composed["segments"].items():
        assert segments
        for seg in segments:
            assert all(key in seg for key in ("start", "stop", "pixels"))
            assert seg["stop"] - seg["start"] == len(seg["pixels"])
            assert 0 < seg["bri"] <= 180
    for frame in composed["frames"].values():
        assert len(frame) % 3 == 0
        assert all(0 <= byte <= 255 for byte in frame)


def test_auto_strategy_from_mood_energy_motion():
    f = RecordingFleet()
    quiet = dynamic_scenes.compose_dynamic_scene(f, mood="quiet dreamy", engine="effect")
    chase = dynamic_scenes.compose_dynamic_scene(f, energy="party", motion="chase", engine="effect")
    rise = dynamic_scenes.compose_dynamic_scene(f, mood="fire", motion="rise", seed=2, engine="effect")
    assert quiet["left"]["seg"][0]["fx"] == dynamic_scenes.SAFE_EFFECTS["gradient"]
    assert chase["left"]["seg"][0]["fx"] == dynamic_scenes.SAFE_EFFECTS["chase"]
    assert rise["left"]["seg"][0]["fx"] == dynamic_scenes.SAFE_EFFECTS["sine"]
    assert rise["left"]["seg"][0]["rev"] is False


def test_apply_primes_power_then_streams_frames(monkeypatch):
    f = RecordingFleet()
    started = []
    monkeypatch.setattr(
        realtime,
        "frame_start",
        lambda fleet_, frames, fps=6, duration_s=300: started.append((frames, fps, duration_s)) or "ok",
    )
    result = dynamic_scenes.apply_dynamic_scene(f, mood="ocean calm", seed=4)
    # Per controller: an on/bri primer (WLED realtime shows nothing while the
    # controller is off), then one DDP frame stream for the whole wall.
    assert [target for target, _payload in f.posts] == ["left", "right"]
    for _target, primer in f.posts:
        assert "seg" not in primer
        assert primer["on"] is True and "bri" in primer and primer["bri"] > 0
    assert len(started) == 1
    frames, fps, duration_s = started[0]
    assert fps == 6 and duration_s == 300  # bounded defaults
    assert set(frames) == {"left", "right"}
    assert len(frames["left"]) == 82 * 3
    assert len(frames["right"]) == 87 * 3
    assert set(result) == {"left", "right"}
    assert look_memory.last_look()["parameters"]["mood"] == "ocean calm"


def test_missing_pixel_metadata_fails_closed():
    f = RecordingFleet()
    f.controllers[0].segments[0] = "far-left"
    with pytest.raises(ValueError, match="SegmentConfig pixels"):
        dynamic_scenes.compose_dynamic_scene(f)


def test_extra_non_wall_channel_is_not_touched():
    f = RecordingFleet()
    f.controllers[0].segments[9] = fleet.SegmentConfig("ceiling", gpio=4, pixels=12)
    composed = dynamic_scenes.compose_dynamic_scene(f)
    ids = [seg["id"] for segments in composed["segments"].values() for seg in segments]
    assert 9 not in ids


def test_custom_installation_wall_order_is_honored():
    f = RecordingFleet()
    f.installation = fleet.InstallationConfig(wall_order=["middle-right", "middle-left"])
    composed = dynamic_scenes.compose_dynamic_scene(f, strategy="left_to_right")
    assert [seg["id"] for seg in composed["segments"]["right"]] == [1]
    assert [seg["id"] for seg in composed["segments"]["left"]] == [1]


def test_effect_availability_falls_back_to_breathe():
    f = RecordingFleet()
    f._effect_ids = {dynamic_scenes.FALLBACK_FX}
    dynamic_scenes.apply_dynamic_scene(f, strategy="left_to_right", engine="effect")
    for _target, payload in f.posts:
        assert {seg["fx"] for seg in payload["seg"]} == {dynamic_scenes.FALLBACK_FX}


def test_effect_availability_raises_when_fallback_missing():
    f = RecordingFleet()
    f._effect_ids = {dynamic_scenes.SAFE_EFFECTS["chase"]}
    with pytest.raises(ValueError, match="fallback"):
        dynamic_scenes.apply_dynamic_scene(f, strategy="quiet_gradient", engine="effect")


def test_generated_top_glow_maps_to_high_indices_for_bottom_zero():
    f = RecordingFleet()
    seg = dynamic_scenes.compose_dynamic_scene(f, strategy="top_glow")["segments"]["left"][0]
    low = sum(seg["pixels"][0])
    high = sum(seg["pixels"][-1])
    assert high > low


def test_generated_top_glow_inverts_when_pixel_zero_top():
    f = RecordingFleet()
    f.installation = fleet.InstallationConfig(pixel_zero="top")
    seg = dynamic_scenes.compose_dynamic_scene(f, strategy="top_glow")["segments"]["left"][0]
    assert sum(seg["pixels"][0]) > sum(seg["pixels"][-1])


def test_composition_modes_and_brightness_vary():
    f = RecordingFleet()
    composed = dynamic_scenes.compose_dynamic_scene(f, composition_mode="independent", strategy="center_bloom")
    bris = [seg["bri"] for segments in composed["segments"].values() for seg in segments]
    assert len(set(bris)) > 1
    unison = dynamic_scenes.compose_dynamic_scene(f, composition_mode="unison")
    assert len({seg["bri"] for segments in unison["segments"].values() for seg in segments}) == 1


def test_random_groups_shimmer_is_seed_deterministic():
    f = RecordingFleet()
    a = dynamic_scenes.compose_dynamic_scene(f, composition_mode="random_groups", strategy="shimmer", seed=7)
    b = dynamic_scenes.compose_dynamic_scene(f, composition_mode="random_groups", strategy="shimmer", seed=7)
    c = dynamic_scenes.compose_dynamic_scene(f, composition_mode="random_groups", strategy="shimmer", seed=8)
    assert a == b
    assert a != c


def test_mcp_dynamic_scene_posts_per_controller(monkeypatch):
    f = RecordingFleet()
    started = []
    monkeypatch.setattr(realtime, "frame_start", lambda fleet_, frames, fps=6, duration_s=300: started.append(frames) or "ok")
    result = mcp_light.call_tool(f, "dynamic_scene", {"mood": "dreamy", "seed": 3}, None)
    assert "Applied dynamic scene" in result["content"][0]["text"]
    # On/bri primer per controller, then one DDP frame stream (generated engine).
    assert [target for target, _payload in f.posts] == ["left", "right"]
    assert len(started) == 1 and set(started[0]) == {"left", "right"}


def test_mcp_segments_info_uses_runtime_wall_order():
    f = RecordingFleet()
    f.installation = fleet.InstallationConfig(wall_order=["middle-right", "middle-left"])
    assert mcp_light._segments_info(f)["wall_order"] == ["middle-right", "middle-left"]


def test_dynamic_scene_records_compact_memory_without_frames(monkeypatch):
    f = RecordingFleet()
    monkeypatch.setattr(realtime, "frame_start", lambda fleet_, frames, fps=6, duration_s=300: "ok")
    dynamic_scenes.apply_dynamic_scene(f, mood="calm", composition_mode="independent", seed=5)
    event = look_memory.last_look()
    assert event["source"] == "dynamic_scene"
    assert event["payload_summary"]["left"][0]["frame_len"] == 34
    assert all("i" not in item for items in event["payload_summary"].values() for item in items)


def test_custom_colors_override_stock_mood_palette():
    f = RecordingFleet()
    teal = dynamic_scenes.compose_dynamic_scene(
        f,
        mood="party",
        colors=["#00c8b4", "#003c64"],
        strategy="quiet_gradient",
        seed=1,
    )
    frame = teal["segments"]["left"][0]["pixels"]
    assert frame[0][1] >= frame[0][0]
    assert frame[0][1] >= frame[0][2]


def test_generated_palettes_change_with_seed():
    f = RecordingFleet()
    a = dynamic_scenes.compose_dynamic_scene(f, mood="ember dusk", strategy="quiet_gradient", seed=3)
    b = dynamic_scenes.compose_dynamic_scene(f, mood="ember dusk", strategy="quiet_gradient", seed=9)
    assert a["segments"]["left"][0]["pixels"] != b["segments"]["left"][0]["pixels"]


def test_look_memory_feedback_summary_and_bounded_history():
    last = None
    for idx in range(105):
        last = look_memory.record_look(summary=f"look {idx}", tags=["warm"])
    assert len(look_memory.load()["events"]) == 100
    look_memory.add_feedback(look_id=last, score=-1, tags=["too-dim"], notes="too dim on middle strips")
    summary = look_memory.memory_summary()
    assert "too-dim" in summary
    assert "too dim on middle strips" in summary


def test_mcp_feedback_and_summary_tools():
    look_id = look_memory.record_look(summary="test")
    result = mcp_light.call_tool(RecordingFleet(), "look_feedback", {"look_id": look_id, "score": 1, "tags": ["liked-colors"], "notes": "worked"}, None)
    assert look_id in result["content"][0]["text"]
    summary = mcp_light.call_tool(RecordingFleet(), "look_memory_summary", {"limit": 5}, None)["content"][0]["text"]
    assert "liked-colors" in summary


def test_unspecified_composition_rotates_instead_of_always_unison():
    f = RecordingFleet()
    bri_patterns = set()
    unison_seeds = 0
    for seed in range(12):
        composed = dynamic_scenes.compose_dynamic_scene(f, mood="moody lounge", seed=seed)
        bris = tuple(sorted(seg["bri"] for segments in composed["segments"].values() for seg in segments))
        bri_patterns.add(bris)
        if len(set(bris)) == 1:
            unison_seeds += 1
    assert unison_seeds <= 8  # rotation, not a unison monocrop
    assert len(bri_patterns) >= 4
