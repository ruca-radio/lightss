from __future__ import annotations

import dynamic_scenes
import fleet
import lightctl
import look_memory
import mcp_light
import pytest


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


def _segments(payloads):
    return {controller: payload["seg"] for controller, payload in payloads.items()}


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
    payloads = dynamic_scenes.compose_dynamic_scene(f, strategy="left_to_right", seed=1)
    assert list(payloads) == ["left", "right"]
    left, right = _segments(payloads)["left"], _segments(payloads)["right"]
    assert [seg["id"] for seg in left] == [0, 1]
    assert [seg["id"] for seg in right] == [1, 0]
    # right controller payload follows wall order: seg1 middle-right, seg0 far-right.
    assert [len(seg["i"]) for seg in left + right] == [34, 48, 47, 40]
    assert [(seg["start"], seg["stop"]) for seg in left + right] == [(0, 34), (34, 82), (0, 47), (47, 87)]


def test_payloads_are_safe_bounded_and_reassert_topology_bounds():
    f = RecordingFleet()
    payloads = dynamic_scenes.compose_dynamic_scene(f, mood="party", intensity=1, seed=9)
    for payload in payloads.values():
        assert payload["bri"] <= 180
        assert payload["transition"] >= 10
        assert payload["udpn"] == {"nn": True}
        for seg in payload["seg"]:
            assert seg["fx"] == 0
            assert "i" in seg
            assert "start" in seg and "stop" in seg and "len" not in seg
            assert seg["on"] is True and seg["frz"] is False


def test_auto_strategy_from_mood_energy_motion():
    f = RecordingFleet()
    quiet = dynamic_scenes.compose_dynamic_scene(f, mood="quiet dreamy", engine="effect")
    chase = dynamic_scenes.compose_dynamic_scene(f, energy="party", motion="chase", engine="effect")
    rise = dynamic_scenes.compose_dynamic_scene(f, mood="fire", motion="rise", seed=2, engine="effect")
    assert quiet["left"]["seg"][0]["fx"] == dynamic_scenes.SAFE_EFFECTS["gradient"]
    assert chase["left"]["seg"][0]["fx"] == dynamic_scenes.SAFE_EFFECTS["chase"]
    assert rise["left"]["seg"][0]["fx"] == dynamic_scenes.SAFE_EFFECTS["sine"]
    assert rise["left"]["seg"][0]["rev"] is False


def test_apply_posts_one_payload_per_controller():
    f = RecordingFleet()
    result = dynamic_scenes.apply_dynamic_scene(f, mood="ocean calm", seed=4)
    assert [target for target, _payload in f.posts] == ["left", "right"]
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
    payloads = dynamic_scenes.compose_dynamic_scene(f)
    ids = [seg["id"] for payload in payloads.values() for seg in payload["seg"]]
    assert 9 not in ids


def test_custom_installation_wall_order_is_honored():
    f = RecordingFleet()
    f.installation = fleet.InstallationConfig(wall_order=["middle-right", "middle-left"])
    payloads = dynamic_scenes.compose_dynamic_scene(f, strategy="left_to_right")
    assert [seg["id"] for seg in payloads["right"]["seg"]] == [1]
    assert [seg["id"] for seg in payloads["left"]["seg"]] == [1]


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
    seg = dynamic_scenes.compose_dynamic_scene(f, strategy="top_glow")["left"]["seg"][0]
    low = sum(seg["i"][0])
    high = sum(seg["i"][-1])
    assert high > low


def test_generated_top_glow_inverts_when_pixel_zero_top():
    f = RecordingFleet()
    f.installation = fleet.InstallationConfig(pixel_zero="top")
    seg = dynamic_scenes.compose_dynamic_scene(f, strategy="top_glow")["left"]["seg"][0]
    assert sum(seg["i"][0]) > sum(seg["i"][-1])


def test_composition_modes_and_brightness_vary():
    f = RecordingFleet()
    payloads = dynamic_scenes.compose_dynamic_scene(f, composition_mode="independent", strategy="center_bloom")
    bris = [seg["bri"] for payload in payloads.values() for seg in payload["seg"]]
    assert len(set(bris)) > 1
    unison = dynamic_scenes.compose_dynamic_scene(f, composition_mode="unison")
    assert len({seg["bri"] for payload in unison.values() for seg in payload["seg"]}) == 1


def test_random_groups_shimmer_is_seed_deterministic():
    f = RecordingFleet()
    a = dynamic_scenes.compose_dynamic_scene(f, composition_mode="random_groups", strategy="shimmer", seed=7)
    b = dynamic_scenes.compose_dynamic_scene(f, composition_mode="random_groups", strategy="shimmer", seed=7)
    c = dynamic_scenes.compose_dynamic_scene(f, composition_mode="random_groups", strategy="shimmer", seed=8)
    assert a == b
    assert a != c


def test_mcp_dynamic_scene_posts_per_controller():
    f = RecordingFleet()
    result = mcp_light.call_tool(f, "dynamic_scene", {"mood": "dreamy", "seed": 3}, None)
    assert "Applied dynamic scene" in result["content"][0]["text"]
    assert [target for target, _payload in f.posts] == ["left", "right"]


def test_mcp_segments_info_uses_runtime_wall_order():
    f = RecordingFleet()
    f.installation = fleet.InstallationConfig(wall_order=["middle-right", "middle-left"])
    assert mcp_light._segments_info(f)["wall_order"] == ["middle-right", "middle-left"]


def test_dynamic_scene_records_compact_memory_without_frames():
    f = RecordingFleet()
    dynamic_scenes.apply_dynamic_scene(f, mood="calm", composition_mode="independent", seed=5)
    event = look_memory.last_look()
    assert event["source"] == "dynamic_scene"
    assert event["payload_summary"]["left"][0]["frame_len"] == 34
    assert all("i" not in item for items in event["payload_summary"].values() for item in items)


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
