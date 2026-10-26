import json

import pytest

import music_chapters
from types import SimpleNamespace


def test_model_receives_mic_features_and_previous_look():
    seen = {}

    def complete(settings, system, user, *, timeout):
        seen.update(settings=settings, system=system, user=json.loads(user), timeout=timeout)
        return {"colors": ["#21c2a0", "#ce3264"], "motion": "ripple",
                "composition_mode": "left_vs_right", "speed": 1.4, "intensity": .8}

    result = music_chapters.design_chapter(
        {"model": "fixture"}, "Cash Machine, Oliver Tree", 
        {"level_avg": .42, "beat_count": 18, "fft_avg": [.1] * 16, "bpm": 112},
        {"colors": [[180, 30, 40], [20, 80, 180]], "motion": "punch"},
        2, complete=complete,
    )

    assert seen["user"]["audio"]["beat_count"] == 18
    assert seen["user"]["audio"]["fft_avg"] == [.1] * 16
    assert seen["user"]["previous"]["motion"] == "punch"
    assert seen["user"]["chapter"] == 2
    assert result == {"colors": [[33, 194, 160], [206, 50, 100]],
                      "motion": "ripple", "composition_mode": "left_vs_right",
                      "speed": 1.4, "intensity": .8}


def test_repeated_or_malformed_model_chapter_does_not_replace_show():
    previous = {"colors": [[180, 30, 40], [20, 80, 180]], "motion": "punch",
                "composition_mode": "pairs"}
    for response in (None, {"colors": [[180, 30, 40], [20, 80, 180]],
                             "motion": "punch", "composition_mode": "pairs"},
                     {"colors": "not a palette", "motion": "strobe"}):
        assert music_chapters.design_chapter(
            {}, "song", {"beat_count": 1}, previous, 1,
            complete=lambda *args, **kwargs: response,
        ) is None


def test_model_can_shape_frequency_response_without_exceeding_renderer_bounds():
    gains = [1.7, 1.5, 1.3, 1.1] + [0.8] * 12
    result = music_chapters.design_chapter(
        {}, "song", {"fft_avg": [.2] * 16},
        {"colors": [[180, 30, 40], [20, 80, 180]], "motion": "flow"}, 2,
        complete=lambda *args, **kwargs: {
            "colors": ["#10a080", "#c02070"], "motion": "spectrum",
            "band_gains": gains, "colorfulness": .9,
        },
    )
    assert result["band_gains"] == gains
    assert result["colorfulness"] == .9


def test_rejects_visual_repetition_across_recent_chapters():
    previous = {"colors": [[20, 170, 180], [180, 30, 120]], "motion": "comet",
                "recent": [{"colors": [[205, 70, 20], [25, 65, 175]], "motion": "punch"}]}
    result = music_chapters.design_chapter(
        {}, "song", {}, previous, 3,
        complete=lambda *args, **kwargs: {
            "colors": [[207, 74, 23], [28, 69, 173]], "motion": "punch",
            "composition_mode": "independent",
        },
    )
    assert result is None


def test_native_catalog_is_common_1d_audio_effects_only():
    def device(extra):
        return {"effects": ["Solid", "Blink", "Freqmap", "Matrix", "RSVD", "Strobe", extra],
                "fxdata": ["", "!;!;!;1", "!;!;!;1f", "!;!;!;2f", "!;!;!;1f", "!;!;!;1f", "!;!;!;1v"]}
    fleet=SimpleNamespace(get_fleet_snapshot=lambda: {"devices": {
        "left": device("Noisemeter"), "right": device("Different name")}})
    # Strobe is audio-reactive by fxdata and is no longer family-filtered, so it
    # is a valid native audio effect alongside Freqmap.
    assert music_chapters.available_native_effects(fleet)==[
        {"id":2,"name":"Freqmap","audio":"f"},
        {"id":5,"name":"Strobe","audio":"f"}]


def test_native_catalog_uses_only_effect_and_fxdata_endpoints():
    class Client:
        def get_effects(self):return ["Solid","Freqmap"]
        def get_fxdata(self):return ["","!;!;!;1f"]
    fleet=SimpleNamespace(clients={"left":Client(),"right":Client()},
                          get_fleet_snapshot=lambda: (_ for _ in ()).throw(AssertionError("slow full snapshot")))
    assert music_chapters.available_native_effects(fleet)==[
        {"id":1,"name":"Freqmap","audio":"f"}]


def test_native_catalog_retries_transient_truncated_fxdata():
    class Client:
        def __init__(self):self.calls=0
        def get_effects(self):return ["Solid","Freqmap"]
        def get_fxdata(self):
            self.calls+=1
            return [] if self.calls==1 else ["","!;!;!;1f"]
    clients={"left":Client(),"right":Client()}
    fleet=SimpleNamespace(clients=clients)
    assert music_chapters.available_native_effects(fleet)==[
        {"id":1,"name":"Freqmap","audio":"f"}]
    assert all(client.calls==2 for client in clients.values())


def test_model_may_choose_valid_native_audio_effect_from_live_catalog():
    catalog=[{"id":155,"name":"Freqmap","audio":"f"}]
    seen={}
    def complete(settings, system, user, *, timeout):
        seen.update(json.loads(user))
        return {"engine":"native","effect":155,"colors":["#a020d0","#08c896"],
                "native_speed":185,"native_intensity":210}
    result=music_chapters.design_chapter(
        {},"song",{"beat_count":22},{"colors":[[180,30,40],[20,80,180]],
        "motion":"punch","native_effects":catalog},2,complete=complete)
    assert seen["native_effects"]==catalog
    assert result["engine"]=="native" and result["effect"]==155
    assert result["native_speed"]==185 and result["native_intensity"]==210


def test_model_cannot_select_unlisted_native_effect():
    result=music_chapters.design_chapter(
        {},"song",{}, {"native_effects":[{"id":155,"name":"Freqmap","audio":"f"}]},1,
        complete=lambda *a,**kw:{"engine":"native","effect":23,
                                  "colors":["#a020d0","#08c896"]})
    assert result is None


CHANNELS = {
    "far-left": {"controller": "left", "segment": 0, "pixels": 34, "gpio": 16},
    "middle-left": {"controller": "left", "segment": 1, "pixels": 47, "gpio": 2},
    "middle-right": {"controller": "right", "segment": 0, "pixels": 47, "gpio": 16},
    "far-right": {"controller": "right", "segment": 1, "pixels": 40, "gpio": 2},
}
STRIP_CATALOG = [
    {"id": 155, "name": "Freqmap", "audio": "f"},
    {"id": 156, "name": "Pixels", "audio": "f"},
]


def test_chapter_may_assign_distinct_native_effects_per_strip():
    seen = {}

    def complete(settings, system, user, *, timeout):
        seen.update(json.loads(user))
        return {"engine": "ddp", "motion": "chase", "speed": 2.0, "band_gains": [1.0] * 16,
                "note": "Outer strips breathe, inner strips pulse.",
                "strips": [
                    {"channel": "far-left", "effect": 155, "native_speed": 170,
                     "native_intensity": 200, "colors": ["#102030", "#405060"]},
                    {"channel": "far-right", "effect": 156,
                     "colors": [[210, 90, 20], [1, 2, 3]]},
                ]}

    result = music_chapters.design_chapter(
        {}, "Song A", {"beat_count": 12},
        {"channels": CHANNELS, "native_effects": STRIP_CATALOG,
         "colors": [[180, 30, 40], [20, 80, 180]], "motion": "punch"}, 2,
        complete=complete,
    )

    assert seen["previous"]["channels"] == CHANNELS
    # ddp-only fields are ignored when strips are present.
    assert result == {
        "engine": "native", "note": "Outer strips breathe, inner strips pulse.",
        "strips": [
            {"channel": "far-left", "effect": 155,
             "colors": [[16, 32, 48], [64, 80, 96]],
             "native_speed": 170, "native_intensity": 200},
            {"channel": "far-right", "effect": 156,
             "colors": [[210, 90, 20], [1, 2, 3]]},
        ],
    }


@pytest.mark.parametrize("strips", [
    [],
    [{"channel": "far-left", "effect": 155, "colors": ["#102030"]}],
    [{"channel": "far-left", "effect": 155, "colors": ["#102030"]},
     {"channel": "ceiling", "effect": 156, "colors": ["#102030"]}],
    [{"channel": "far-left", "effect": 155, "colors": ["#102030"]},
     {"channel": "far-left", "effect": 156, "colors": ["#102030"]}],
    [{"channel": "far-left", "effect": 999, "colors": ["#102030"]},
     {"channel": "far-right", "effect": 156, "colors": ["#102030"]}],
    [{"channel": "far-left", "effect": 155, "colors": []},
     {"channel": "far-right", "effect": 156, "colors": ["#102030"]}],
    [{"channel": "far-left", "effect": 155, "colors": ["not-a-colour"]},
     {"channel": "far-right", "effect": 156, "colors": ["#102030"]}],
    [{"channel": "far-left", "effect": 155, "colors": ["#102030", "#204060", "#306090", "#40a0c0"]},
     {"channel": "far-right", "effect": 156, "colors": ["#102030"]}],
    [{"channel": "far-left", "effect": 155, "colors": ["#102030"], "native_speed": 300},
     {"channel": "far-right", "effect": 156, "colors": ["#102030"]}],
    [{"channel": "far-left", "effect": 155, "colors": ["#102030"]},
     {"channel": "far-right", "effect": 156, "colors": ["#102030"]},
     {"channel": "middle-left", "effect": 156, "colors": ["#102030"]},
     {"channel": "middle-right", "effect": 156, "colors": ["#102030"]},
     {"channel": "far-left", "effect": 156, "colors": ["#102030"]}],
])
def test_invalid_strip_chapter_is_rejected(strips):
    result = music_chapters.design_chapter(
        {}, "Song A", {}, {"channels": CHANNELS, "native_effects": STRIP_CATALOG}, 1,
        complete=lambda *args, **kwargs: {
            "engine": "native", "strips": strips,
            "colors": ["#21c2a0", "#ce3264"]},
    )
    assert result is None


def test_strip_chapter_needs_known_channel_context():
    result = music_chapters.design_chapter(
        {}, "Song A", {}, {"native_effects": STRIP_CATALOG}, 1,
        complete=lambda *args, **kwargs: {"engine": "native", "strips": [
            {"channel": "far-left", "effect": 155, "colors": ["#102030"]},
            {"channel": "far-right", "effect": 156, "colors": ["#102030"]}]},
    )
    assert result is None


def test_wall_wide_chapter_accepts_short_plain_text_note():
    def chapter(note):
        return music_chapters.design_chapter(
            {}, "song", {}, {"native_effects": []}, 1,
            complete=lambda *args, **kwargs: {"colors": ["#21c2a0", "#ce3264"],
                                              "motion": "ripple", "note": note},
        )

    assert chapter("  Slow\nburn  ")["note"] == "Slow burn"
    assert "note" not in chapter("x" * 121)


def test_system_prompt_teaches_youtube_song_chapters():
    assert "youtube.music_mix" in music_chapters.SYSTEM
    assert "youtube.upcoming" in music_chapters.SYSTEM
    assert music_chapters.SYSTEM.startswith("You are directing")


def test_youtube_mix_context_reaches_the_chapter_model():
    seen = {}
    youtube = {"video": {"video_id": "DBuyeSmPXd0", "title": "80's Dance Party Mix"},
               "music_mix": True,
               "current": {"index": 1, "track": "Cher - Believe"},
               "upcoming": [{"title": "Madonna - Vogue"}],
               "progress_s": 200.0}

    def complete(settings, system, user, *, timeout):
        seen.update(json.loads(user))
        return {"colors": ["#21c2a0", "#ce3264"], "motion": "ripple"}

    result = music_chapters.design_chapter(
        {}, "Cher - Believe", {"level_avg": .4},
        {"youtube": youtube, "native_effects": []}, 2, complete=complete)

    assert seen["previous"]["youtube"] == youtube
    assert seen["track"] == "Cher - Believe"
    assert result["motion"] == "ripple"
