"""The show librarian keeps WLED effect and palette libraries out of the director."""

import json
import stat

import music_chapters
import show_library


EFFECTS = ["Solid", "Breathe", "Fireworks", "Matrix", "RSVD"]
FXDATA = ["!;!;!;1", "!;!;!;1f", "!;!;!;1v", "!;!;!;2", "!;!;!;1"]
PALETTES = ["Default", "* Random Cycle", "Ocean", "Lava"]


def _index():
    return show_library.build_index(EFFECTS, FXDATA, PALETTES)


def test_index_drops_matrix_only_and_placeholder_effects():
    ids = [effect["id"] for effect in _index()["effects"]]

    assert 0 in ids
    assert 1 in ids
    assert 2 in ids
    assert 3 not in ids
    assert 4 not in ids


def test_signature_is_stable_and_changes_with_the_catalog():
    first = show_library.signature(EFFECTS, FXDATA, PALETTES)
    assert first == show_library.signature(list(EFFECTS), list(FXDATA), list(PALETTES))
    assert first == _index()["signature"]
    assert first != show_library.signature(EFFECTS, FXDATA, PALETTES + ["Party"])


def test_candidates_are_a_short_audio_list_without_special_palettes():
    index = _index()

    loud = show_library.candidates(index, "song", {"level_avg": 0.8}, {}, limit=1)
    quiet = show_library.candidates(index, "song", {"level_avg": 0.05}, {}, limit=1)
    full = show_library.candidates(index, "song", {"level_avg": 0.4}, {}, limit=8)

    assert loud["effects"] == [{
        "id": 2, "name": "Fireworks", "audio": "v", "energy": "high", "motion": "punch",
        "description": "Random color blobs light up, then fade again",
        "intensity": "Amount of fireworks",
    }]
    assert quiet["effects"][0]["id"] == 1
    assert quiet["effects"][0]["energy"] == "low"
    assert [effect["id"] for effect in full["effects"]] == [1, 2]
    assert [palette["name"] for palette in full["palettes"]] == ["Default", "Ocean", "Lava"]
    assert full["palettes"][1]["temperature"] == "cold"
    assert full["palettes"][2]["temperature"] == "warm"
    # Wiki descriptions ride along so the chapter model knows what each choice does.
    assert full["palettes"][1]["description"] == "Blue, teal and white colors"
    assert full["palettes"][0]["description"].startswith("The palette is automatically selected")


def test_annotate_keeps_only_known_ids_and_enums():
    def complete(settings, system, user, timeout=8):
        assert "Matrix" not in user
        return {
            "effects": [
                {"id": 1, "energy": "mid", "motion": "flow"},
                {"id": 2, "energy": "low", "motion": "strobe"},
                {"id": 99, "energy": "high"},
            ],
            "palettes": [
                {"id": 3, "temperature": "cold"},
                {"id": 2, "temperature": "hot"},
                {"id": 99, "temperature": "warm"},
            ],
        }

    updated = show_library.annotate(_index(), {"model": "small"}, complete)
    effects = {effect["id"]: effect for effect in updated["effects"]}

    assert effects[1]["energy"] == "mid"
    assert effects[1]["motion"] == "flow"
    assert effects[2]["energy"] == "low"
    assert effects[2]["motion"] == "punch"
    assert 99 not in effects
    assert {palette["id"]: palette["temperature"] for palette in updated["palettes"]}[3] == "cold"
    assert {palette["id"]: palette["temperature"] for palette in updated["palettes"]}[2] == "cold"


def test_annotate_timeout_leaves_the_deterministic_index():
    original = _index()
    assert show_library.annotate(original, {"model": "small"}, lambda *args, **kwargs: None) == original


def test_cache_round_trip_preserves_the_index(tmp_path):
    path = tmp_path / "show-library.json"
    show_library.store_index(_index(), path)
    assert show_library.load_index(path)["signature"] == _index()["signature"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


class _Client:
    def __init__(self, effects=None):
        self.effects = list(effects or EFFECTS)
        self.effect_reads = 0
        self.fx_reads = 0

    def get_effects(self):
        self.effect_reads += 1
        return list(self.effects)

    def get_fxdata(self):
        self.fx_reads += 1
        if self.fx_reads == 1:
            return []
        return list(FXDATA)

    def get_palettes(self):
        return list(PALETTES)


def test_fleet_index_is_cached_until_the_catalog_ttl():
    left, right = _Client(), _Client()
    fleet = type("Fleet", (), {"clients": {"right": right, "left": left}})()

    first = show_library.index_for_fleet(fleet, now=10)
    second = show_library.index_for_fleet(fleet, now=11)

    assert first["signature"] == second["signature"]
    assert {effect["id"] for effect in first["effects"]} == {0, 1, 2}
    assert left.effect_reads == 1
    assert right.effect_reads == 1
    assert left.fx_reads == 2

    show_library.index_for_fleet(fleet, now=10 + show_library.CATALOG_TTL_S + 1)
    assert left.effect_reads == 2


def test_fleet_index_drops_effects_the_controllers_do_not_share():
    left, right = _Client(), _Client()
    right.effects[2] = "Not Fireworks"
    right.fx_reads = 1
    fleet = type("Fleet", (), {"clients": {"left": left, "right": right}})()

    index = show_library.index_for_fleet(fleet, now=50)

    assert 2 not in {effect["id"] for effect in index["effects"]}
    assert 1 in {effect["id"] for effect in index["effects"]}


def test_disk_cache_skips_a_second_annotate_for_the_same_signature(tmp_path):
    calls = []

    def complete(settings, system, user, timeout=8):
        calls.append(json.loads(user))
        return {"effects": [{"id": 1, "energy": "high"}]}

    settings = {"agents": {"librarian": {
        "enabled": True, "model": "small-librarian", "base_url": "http://ollama.test/v1",
    }}}
    path = tmp_path / "show-library.json"
    left = _Client()
    left.fx_reads = 1
    fleet = type("Fleet", (), {"clients": {"left": left, "right": _Client()}})()
    fleet.clients["right"].fx_reads = 1

    first = show_library.index_for_fleet(fleet, settings=settings, complete=complete, cache_path=path, now=0)
    other = type("Fleet", (), {"clients": {"left": _Client(), "right": _Client()}})()
    other.clients["left"].fx_reads = 1
    other.clients["right"].fx_reads = 1
    second = show_library.index_for_fleet(other, settings=settings, complete=complete, cache_path=path, now=5)

    assert len(calls) == 1
    assert calls[0]["effects"]
    assert "RSVD" not in json.dumps(calls[0])
    breathe = {effect["id"]: effect for effect in second["effects"]}[1]
    assert breathe["energy"] == "high"
    assert first["annotated_by"] == "small-librarian"
    assert second["annotated_by"] == "small-librarian"


def test_colorist_does_not_retag_the_catalog():
    calls = []
    left = _Client()
    left.fx_reads = 1
    fleet = type("Fleet", (), {"clients": {"left": left}})()
    settings = {"agents": {"colorist": {"enabled": True, "model": "frontier", "base_url": "http://api.test/v1"}}}

    show_library.index_for_fleet(
        fleet, settings=settings, complete=lambda *args, **kwargs: calls.append(1), now=80,
    )

    assert calls == []


def test_librarian_settings_prefer_an_enabled_librarian_then_the_colorist():
    settings = {
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "agents": {
            "librarian": {
                "enabled": True,
                "model": "qwen-small",
                "provider": "ollama",
                "base_url": "https://ollama.com/v1",
                "api_key_env": "OLLAMA_API_KEY",
                "api_key": "secret-value",
            },
            "colorist": {"enabled": True, "model": "frontier"},
        },
    }

    resolved = show_library.librarian_settings(settings)

    assert resolved["enabled"] is True
    assert resolved["role"] == "librarian"
    assert resolved["model"] == "qwen-small"
    assert resolved["base_url"] == "https://ollama.com/v1"
    assert resolved["api_key_env"] == "OLLAMA_API_KEY"
    assert "api_key" not in resolved
    assert "secret-value" not in json.dumps(resolved)

    settings["agents"]["librarian"]["enabled"] = False
    fallback = show_library.librarian_settings(settings)
    assert fallback["role"] == "colorist"
    assert fallback["model"] == "frontier"
    assert show_library.librarian_settings({})["enabled"] is False


def test_palette_hint_uses_the_librarian_and_rejects_a_bad_palette():
    seen = {}

    def complete(settings, system, user, timeout=8):
        seen["settings"] = settings
        seen["user"] = json.loads(user)
        return {"colors": ["#112233", "#ab12cd"], "mood": "cold glass"}

    settings = {"agents": {"librarian": {
        "enabled": True, "model": "small", "base_url": "http://ollama.test/v1",
    }}}
    hint = show_library.palette_hint(
        settings, "Cash Machine", {"level_avg": 0.4, "bpm": 112},
        {"colors": [[20, 30, 40], [50, 60, 70]]}, complete=complete,
    )

    assert hint == ["#112233", "#ab12cd"]
    assert seen["settings"]["model"] == "small"
    assert seen["user"]["track"] == "Cash Machine"
    assert "effects" not in seen["user"]
    assert show_library.palette_hint(settings, "song", {}, {}, complete=lambda *a, **k: {"colors": ["nope"]}) is None
    assert show_library.palette_hint({}, "song", {}, {}, complete=lambda *a, **k: (_ for _ in ()).throw(AssertionError("called"))) is None


def test_design_chapter_shows_a_palette_hint_without_a_second_model_call():
    seen = {}

    def complete(settings, system, user, *, timeout):
        seen["user"] = json.loads(user)
        seen["calls"] = seen.get("calls", 0) + 1
        return {"colors": ["#214060", "#c04070"], "motion": "flow"}

    result = music_chapters.design_chapter(
        {}, "song", {"level_avg": 0.2},
        {"colors": [[180, 30, 40], [20, 80, 180]], "motion": "punch",
         "palette_hint": ["#214060", "#c04070"]},
        1, complete=complete,
    )

    assert seen["calls"] == 1
    assert seen["user"]["palette_hint"] == ["#214060", "#c04070"]
    assert "palette_hint" not in seen["user"]["previous"]
    assert result["colors"] == [[33, 64, 96], [192, 64, 112]]
    assert result["motion"] == "flow"


def test_chapter_request_uses_the_cached_index_instead_of_downloading(monkeypatch):
    from datetime import datetime

    import smart_director as sd
    from tests.test_smart_director import Clock, FakeFleet, Listener, Renderer, music, tv

    downloads = []
    monkeypatch.setattr(music_chapters, "available_native_effects", lambda fleet: downloads.append(1))
    index = {
        "signature": "cached",
        "annotated_by": "",
        "effects": [
            {"id": 155, "name": "Freqmap", "audio": "f", "energy": "high", "motion": "spectrum"},
            {"id": 9, "name": "Fire 2012", "audio": "v", "energy": "high", "motion": "punch"},
        ],
        "palettes": [{"id": 1, "name": "* Random Cycle", "special": True, "temperature": "neutral"}],
    }
    monkeypatch.setattr(show_library, "index_for_fleet", lambda fleet, settings=None, **kwargs: index)
    monkeypatch.setattr(show_library, "palette_hint", lambda *args, **kwargs: ["#102030", "#405060"])

    seen = []

    def chapter(settings, track, audio, previous, chapter_index):
        seen.append(previous)
        if chapter_index == 1:
            return {"engine": "native", "effect": 155, "colors": [[30, 160, 90], [170, 35, 125]],
                    "native_speed": 170, "native_intensity": 180}
        return {"engine": "ddp", "colors": [[170, 110, 30], [40, 50, 170]], "motion": "chase"}

    clock = Clock()
    fleet = FakeFleet()
    listener = Listener()
    director = sd.SmartDirector(
        fleet, {"enabled": True}, listener=listener, renderer_factory=Renderer,
        clock=clock, wall_clock=lambda: datetime(2026, 9, 12, 23, 0), chapter_fn=chapter,
    )
    try:
        director.configure({"mode": "music"})
        director.step(tv(description="Song A"), music(), listener.get_snapshot())
        clock.advance(16)
        director.step(tv(description="Song A"), music(), listener.get_snapshot())
        director._chapter_future.result(timeout=2)
        director.step(tv(description="Song A"), music(), listener.get_snapshot())
    finally:
        if director.renderer is not None:
            director.renderer.stop()
        director.shutdown()

    assert downloads == []
    assert [effect["id"] for effect in seen[0]["native_effects"]] == [155, 9]
    assert seen[0]["palette_hint"] == ["#102030", "#405060"]
    assert director.status()["native_effect"] == 155
    assert fleet.posts[-1][0]["seg"][0]["fx"] == 155
