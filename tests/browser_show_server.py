"""Local-only fixture for the browser smoke test; no real WLED or AI requests."""
from copy import deepcopy
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import wave
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import light_gui


class SimulatedWLED:
    def __init__(self):
        self.state = {"on": True, "bri": 128,
                      "seg": [{"id": 2, "start": 0, "stop": 100, "col": [[20, 80, 180, 0]], "fx": 0}]}

    def get_state(self):
        return deepcopy(self.state)

    def get_info(self):
        return {"name": "Simulated WLED", "leds": {"count": 100, "rgbw": True}}

    def get_device_snapshot(self):
        effects = ["Solid"] * 109
        effects[67] = "Colorwaves"
        return {"state": self.get_state(), "info": self.get_info(),
                "effects": effects, "palettes": ["Default"]}

    def post_state(self, payload):
        self.state.update(deepcopy(payload))


def director(song, audio, snapshot, previous):
    assert audio["rms"] > 0
    return {"bri": 180, "seg": [{"fx": 67, "col": [[180, 40, 0, 0],
            [0, 60, 180, 0], [80, 0, 120, 0]]}]}, "Synthetic beat show"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as folder:
        wav = Path(folder) / "beats.wav"
        rate = 44100
        with wave.open(str(wav), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            frames = bytearray()
            for index in range(rate * 16):
                t = index / rate
                beat = math.exp(-(t % 0.5) * 35)
                sample = 0.6 * beat * math.sin(2 * math.pi * 90 * t) + 0.02 * math.sin(2 * math.pi * 550 * t)
                frames.extend(struct.pack("<h", round(sample * 32767)))
            audio.writeframes(frames)
        light_gui.get_now_playing = lambda: None
        light_gui.direct_music_show = director
        state = light_gui.GuiState(SimulatedWLED())
        state.music_show.recognize = lambda _: {"title": "Synthetic 120 BPM", "artist": "Test fixture"}
        server = ThreadingHTTPServer(("127.0.0.1", 0), light_gui.make_handler(state))
        print(json.dumps({"port": server.server_port, "wav": str(wav)}), flush=True)
        server.serve_forever()
