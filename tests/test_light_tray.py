import light_tray


class FakeClient:
    def __init__(self):
        self.payloads = []

    def post_state(self, payload):
        self.payloads.append(payload)


def test_desktop_color_control_turns_light_on():
    client = FakeClient()
    worker = light_tray.LightWorker(client)

    assert worker.set_color(0, 0, 255, 0) == "OK"

    assert client.payloads == [{"on": True, "seg": [{"col": [[0, 0, 255, 0]]}]}]


def test_desktop_brightness_control_turns_light_on():
    client = FakeClient()
    worker = light_tray.LightWorker(client)

    assert worker.set_bri(123) == "OK"

    assert client.payloads == [{"on": True, "bri": 123}]


def test_desktop_effect_control_turns_light_on():
    client = FakeClient()
    worker = light_tray.LightWorker(client)

    assert worker.set_effect(28, 170) == "OK"

    assert client.payloads == [{"on": True, "seg": [{"fx": 28, "sx": 170}]}]


def test_ai_control_prompt_includes_context_and_instruction():
    prompt = light_tray.build_ai_control_prompt(
        "Set brightness to 123",
        state={"on": True, "bri": 50, "seg": [{"fx": 9, "sx": 180}]},
        now_playing={"artist": "M83", "title": "Midnight City", "status": "Playing"},
        device_snapshot={
            "state": {"on": True, "bri": 50, "seg": [{"fx": 9, "sx": 180, "pal": 6}]},
            "info": {"name": "WLED-Gledopto", "ver": "0.15.1", "leds": {"count": 1, "rgbw": True, "cct": 4}},
            "effects": ["Solid", "Rainbow"],
            "palettes": ["Default", "Party"],
            "config": {"light": {"nl": {"dur": 60}}, "um": {"AudioReactive": {"enabled": False}}},
        },
    )

    assert "Desktop AI Mode control request: Set brightness to 123" in prompt
    assert "Current light state:" in prompt
    assert "Brightness: 50" in prompt
    assert "Effect: 9" in prompt
    assert "Now playing: M83 - Midnight City (Playing)" in prompt
    assert "Available AI actions:" in prompt
    assert "fade_off" in prompt
    assert "schedule_add" in prompt
    assert "music_listen" in prompt
    assert "Current WLED device snapshot:" in prompt
    assert "WLED-Gledopto" in prompt
    assert "AudioReactive" in prompt


def test_ai_routable_actions_exclude_system_power_controls():
    assert not light_tray.is_ai_routable_action("on")
    assert not light_tray.is_ai_routable_action("off")
    assert light_tray.is_ai_routable_action("brightness")
    assert light_tray.is_ai_routable_action("scene")
    assert light_tray.is_ai_routable_action("audio_reactive")
    assert light_tray.is_ai_routable_action("schedule")
    assert light_tray.is_ai_routable_action("scene_management")
