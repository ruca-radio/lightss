from copy import deepcopy
import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import light_gui
import lightctl
from music_show import MusicShow, ShowRenderer


def snapshot():
    effects = ["Solid"] * 109
    effects[67] = "Colorwaves"
    effects[108] = "Flow"
    return {"state": {"seg": [{"id": 2, "start": 0, "stop": 60},
                                {"id": 5, "start": 60, "stop": 120}]},
            "effects": effects, "palettes": ["Default", "Party"]}


class FakeClient:
    def __init__(self):
        self.payloads = []

    def get_device_snapshot(self):
        return snapshot()

    def post_state(self, payload):
        self.payloads.append(deepcopy(payload))


def frame(**kwargs):
    return dict(rms=0.1, drive=0.6, bass=0.8, mid=0.4, treble=0.2,
                bpm=120, beatConfidence=0.9, **kwargs)


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            pytest.fail("Timed out waiting for music show")
        time.sleep(0.01)


def test_theme_is_preserved_across_beats_and_decay():
    renderer = ShowRenderer(snapshot())
    renderer.set_theme({"bri": 190, "seg": [{"fx": 67, "pal": 1, "sx": 100,
                        "col": [[150, 25, 0, 0], [0, 80, 180, 0], [100, 0, 130, 0]]}]})
    hit = renderer.render(frame(beat=True), 10)
    release = renderer.render(frame(beat=False), 10.3)
    assert hit["bri"] > release["bri"]
    assert [s["id"] for s in hit["seg"]] == [2, 5]
    for segment in release["seg"]:
        assert segment["fx"] == 67
        assert segment["pal"] == 1
    assert hit["seg"][0]["col"] == release["seg"][0]["col"]
    assert hit["tt"] == 1
    assert renderer.render(frame(beat=False), 14, fresh=False)["bri"] == 30


def test_colors_change_on_phrases_not_every_beat():
    renderer = ShowRenderer(snapshot())
    first = renderer.render(frame(beat=True), 10)
    for i in range(1, 7):
        payload = renderer.render(frame(beat=True), 10 + i * 0.5)
        assert payload["seg"][0]["col"] == first["seg"][0]["col"]
    eighth = renderer.render(frame(beat=True), 13.5)
    assert eighth["seg"][0]["col"] != first["seg"][0]["col"]


def test_only_live_effects_and_existing_segments_are_used():
    renderer = ShowRenderer(snapshot())
    renderer.set_theme({"bri": 999, "seg": [{"fx": 9, "pal": 999,
                        "col": [[255, 255, 255, 255]]}], "ps": 3})
    payload = renderer.render(frame(beat=True), 10)
    assert payload["bri"] <= 210
    assert payload["seg"][0]["fx"] == 0
    assert payload["seg"][0]["pal"] == 0
    assert max(payload["seg"][0]["col"][0]) == 210
    assert "ps" not in payload
    with pytest.raises(ValueError, match="no active segments"):
        ShowRenderer({"state": {"seg": []}})


@pytest.fixture
def show():
    client = FakeClient()
    show = MusicShow(client, lambda *args: ({"seg": [{"fx": 67}]}, "Ocean pulse"),
                     lambda _: None, lambda: None)
    show.FRAME_INTERVAL = 0.01
    show.AI_INTERVAL = 0.1
    yield show
    show.stop()


def feed(show, count=8):
    token = show.session_id
    for _ in range(count):
        show.ingest(dict(session_id=token, **frame(beat=True)))


def test_live_audio_works_without_song_identification(show):
    show.start()
    assert show.client.payloads == []
    feed(show)
    wait_for(lambda: show.status()["ai_state"] == "directing")
    wait_for(lambda: any(p["seg"][0]["fx"] == 67 for p in show.client.payloads))
    assert show.status()["audio_live"]
    assert show.status()["song"] is None
    assert show.status()["response"] == "Ocean pulse"


def test_ai_failure_is_visible_and_beats_keep_running(show):
    def fail(*args):
        raise ValueError("OPENAI_API_KEY is not set")
    show.director = fail
    show.start()
    feed(show)
    wait_for(lambda: show.status()["ai_state"] == "fallback")
    assert "OPENAI_API_KEY" in show.status()["ai_error"]
    assert show.client.payloads


def test_input_validation_and_stale_session_rejection(show):
    token = show.start()["session_id"]
    with pytest.raises(ValueError, match="finite"):
        show.ingest({"session_id": token, "rms": float("nan")})
    show.stop()
    with pytest.raises(ValueError, match="no longer active"):
        show.ingest({"session_id": token})
    newer = show.start()["session_id"]
    show.stop(token)
    assert show.status()["running"]
    assert show.session_id == newer


def test_stopping_during_ai_call_discards_late_result(show):
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        entered.set()
        release.wait(2)
        return {"seg": [{"fx": 108}]}, "Old response"
    show.director = slow
    show.start()
    feed(show)
    assert entered.wait(2)
    show.stop()
    count = len(show.client.payloads)
    release.set()
    show.director_thread.join(2)
    assert show.status()["response"] == ""
    assert len(show.client.payloads) == count


def test_stale_audio_decays_and_output_failure_is_reported(show):
    show.INPUT_TIMEOUT = 0.02
    show.start()
    feed(show, 1)
    wait_for(lambda: show.sent_frames > 0)
    with show.lock:
        show.received_at = time.monotonic() - 10
        show.renderer.last_sound = time.monotonic() - 10
    wait_for(lambda: show.client.payloads[-1]["bri"] == 30)
    assert not show.status()["audio_live"]
    show.client.post_state = lambda _: (_ for _ in ()).throw(OSError("WLED offline"))
    feed(show, 1)
    wait_for(lambda: "offline" in show.status()["output_error"])


def test_recognition_during_ai_generation_cannot_be_overwritten(show):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def direct(song, *args):
        calls.append(song)
        if len(calls) == 1:
            entered.set()
            release.wait(2)
        return {"seg": [{"fx": 67}]}, "Recognized song" if song else "Unknown song"
    show.director = direct
    show.recognize = lambda _: {"title": "New song"}
    show.start()
    feed(show)
    assert entered.wait(2)
    show.sample(b"wav", show.session_id)
    wait_for(lambda: show.status()["song"] is not None)
    release.set()
    wait_for(lambda: show.status()["response"] == "Recognized song")
    assert calls[1]["title"] == "New song"
    assert show.status()["song"]["title"] == "New song"


def test_director_combines_theme_actions_without_side_effects(monkeypatch):
    def plan(*args):
        assert '"bass": 0.8' in args[0]
        return {"response": "Amber and blue", "actions": [
            {"action": "brightness", "brightness": 180},
            {"action": "color", "red": 180, "green": 40, "blue": 0},
            {"action": "effect", "effect": 67, "speed": 110},
            {"action": "delete_scene", "scene": "must-not-delete"}]}
    monkeypatch.setattr(light_gui, "call_openai_for_plan", plan)
    monkeypatch.setattr(lightctl, "delete_scene", lambda *_: pytest.fail("Unexpected side effect"))
    payload, response = light_gui.direct_music_show(None, {"bass": 0.8}, snapshot(), {})
    assert payload["bri"] == 180
    assert payload["seg"][0]["fx"] == 67
    assert payload["seg"][0]["col"][0] == [180, 40, 0, 0]
    assert response == "Amber and blue"


def test_http_start_features_stop_pipeline(show):
    state = SimpleNamespace(music_show=show, start_music_show=show.start)
    server = ThreadingHTTPServer(("127.0.0.1", 0), light_gui.make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def post(path, data):
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/show/{path}",
                                         json.dumps(data).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    try:
        status = post("control", {"command": "start"})
        token = status["session_id"]
        result = post("features", dict(session_id=token, **frame(beat=True)))
        assert result["ok"] and result["audio_live"]
        wait_for(lambda: show.client.payloads)
        assert post("control", {"command": "stop", "session_id": token})["running"] is False
    finally:
        show.stop()
        server.shutdown()
        server.server_close()
