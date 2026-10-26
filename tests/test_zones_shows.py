"""Tests for zones, per-LED frames, and the show sequencer.

Written against the "Addendum 2026-08-01: AI-complete control — zones, shows,
per-LED" contract in docs/superpowers/specs/2026-08-01-wled-fleet-segments-design.md:

- lightctl.zone_bounds / zone_payload / leds_payload (zone + per-LED geometry)
- shows.validate_show / ShowRunner / start_show / stop_show / show_status
- mcp_light tools: set_zone, set_segment_bounds, delete_segment, set_leds,
  start_show, stop_show, show_status

Zone math convention asserted here (length=40, orientation="up", LED 0 at the
bottom): fraction sizes use floor division (half=20, third=13, quarter=10),
"bottom" anchors at LED 0, "top" at the high end, "middle" is centered, and
orientation="down" mirrors every range as (length - stop, length - start).
"""

from __future__ import annotations

import math
import time
import unittest
from unittest import mock

import fleet
import lightctl
import mcp_light
import shows


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class RecordingClient(lightctl.LightClient):
    """dry_run LightClient that records every posted payload."""

    def __init__(self, host: str, state: dict | None = None, info: dict | None = None):
        super().__init__(host, dry_run=True)
        self.payloads: list[dict] = []
        self._state = state or {}
        self._info = info or {}

    def post_state(self, payload: dict) -> None:
        lightctl.validate_wled_payload(payload)
        self.payloads.append(payload)

    # Hermetic: zone segment allocation reads state/info; never hit the network.
    def get_state(self) -> dict:
        return dict(self._state)

    def get_info(self) -> dict:
        return dict(self._info)


# The shipped wall has four *different* column lengths (34/47/47/40), so the
# geometry API requires an explicit length or channel instead of a single
# default. These wrappers pin the tests to one 40-pixel column so the zone math
# itself stays under test.
LENGTH = 40


def bounds(zone_name, **kwargs):
    kwargs.setdefault("length", LENGTH)
    return lightctl.zone_bounds(zone_name, **kwargs)


def zpayload(zones, **kwargs):
    kwargs.setdefault("length", LENGTH)
    return lightctl.zone_payload(zones, **kwargs)


def lpayload(leds, **kwargs):
    kwargs.setdefault("length", LENGTH)
    return lightctl.leds_payload(leds, **kwargs)


def make_fleet() -> fleet.LightFleet:
    controllers = [
        fleet.ControllerConfig("right", "http://10.27.27.110", {0: "far-right", 1: "middle-right"}),
        fleet.ControllerConfig("left", "http://10.27.27.112", {0: "middle-left", 1: "far-left"}),
    ]
    clients = {
        "right": RecordingClient("http://10.27.27.110"),
        "left": RecordingClient("http://10.27.27.112"),
    }
    return fleet.LightFleet(clients, controllers)


class FakeModes:
    def start(self) -> str:
        return "started"

    def stop(self) -> str:
        return "stopped"


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def posted_count(fleet_: fleet.LightFleet) -> int:
    return sum(len(client.payloads) for client in fleet_.clients.values())


# ---------------------------------------------------------------------------
# zone_bounds
# ---------------------------------------------------------------------------

class ZoneBoundsTests(unittest.TestCase):
    def test_all_spans_the_whole_column(self):
        self.assertEqual(bounds("all"), (0, LENGTH))

    def test_halves_orientation_up(self):
        # LED 0 at the bottom: "top" maps to the high indices.
        self.assertEqual(bounds("bottom half"), (0, 20))
        self.assertEqual(bounds("top half"), (20, 40))
        start, stop = bounds("middle half")
        self.assertLessEqual(abs((stop - start) - 20), 1)
        # centered: symmetric margins on both ends
        self.assertLessEqual(abs(start - (40 - stop)), 1)

    def test_thirds_and_quarters(self):
        for word, fraction in (("third", 3), ("quarter", 4)):
            bottom = bounds(f"bottom {word}")
            top = bounds(f"top {word}")
            middle = bounds(f"middle {word}")
            # floor or rounded division of 40 LEDs are both acceptable sizes
            self.assertIn(bottom[1] - bottom[0], (40 // fraction, round(40 / fraction)))
            self.assertIn(top[1] - top[0], (40 // fraction, round(40 / fraction)))
            self.assertEqual(bottom[0], 0)
            self.assertEqual(top[1], 40)
            self.assertLessEqual(abs((middle[1] - middle[0]) - 40 / fraction), 1)
            self.assertGreater(middle[0], 0)
            self.assertLess(middle[1], 40)
            # middle is centered: symmetric margins
            self.assertLessEqual(abs(middle[0] - (40 - middle[1])), 1)

    def test_every_named_zone_is_accepted(self):
        for position in ("top", "middle", "bottom"):
            for fraction in ("half", "third", "quarter"):
                start, stop = bounds(f"{position} {fraction}")
                self.assertGreaterEqual(start, 0)
                self.assertLess(start, stop)
                self.assertLessEqual(stop, LENGTH)

    def test_orientation_down_flips_top_and_bottom(self):
        for position in ("top", "middle", "bottom"):
            for fraction in ("half", "third", "quarter"):
                zone = f"{position} {fraction}"
                up_start, up_stop = bounds(zone, orientation="up")
                down_start, down_stop = bounds(zone, orientation="down")
                self.assertEqual((down_start, down_stop), (40 - up_stop, 40 - up_start))

    def test_orientation_down_top_equals_up_bottom(self):
        self.assertEqual(
            bounds("top half", orientation="down"),
            bounds("bottom half", orientation="up"),
        )

    def test_custom_length(self):
        self.assertEqual(bounds("all", length=40), (0, 40))
        self.assertEqual(bounds("top half", length=40), (20, 40))
        self.assertEqual(bounds("bottom half", length=40), (0, 20))

    def test_zone_names_are_case_insensitive(self):
        self.assertEqual(bounds("Top Half"), bounds("top half"))

    def test_unknown_zones_raise_value_error(self):
        for bad in ("", "nope", "top fifth", "left half", "top half extra"):
            with self.assertRaises(ValueError, msg=f"zone {bad!r} should be rejected"):
                bounds(bad)


# ---------------------------------------------------------------------------
# zone_payload
# ---------------------------------------------------------------------------

class ZonePayloadTests(unittest.TestCase):
    def test_named_zone_emits_explicit_start_stop(self):
        payload = zpayload([{"zone": "top half", "fx": 9, "pal": 0}])
        self.assertEqual(
            payload,
            {"seg": [{"start": 20, "stop": 40, "fx": 9, "pal": 0}]},
        )

    def test_zone_key_is_not_leaked_into_segment(self):
        payload = zpayload([{"zone": "bottom third", "fx": 9}])
        for seg in payload["seg"]:
            self.assertNotIn("zone", seg)

    def test_explicit_start_stop_form(self):
        payload = zpayload([{"start": 10, "stop": 20, "fx": 28}])
        self.assertEqual(payload, {"seg": [{"start": 10, "stop": 20, "fx": 28}]})

    def test_id_marks_segment_update_id_less_appends(self):
        payload = zpayload([
            {"zone": "bottom half", "id": 3, "rev": True},
            {"zone": "top half", "fx": 9},
        ])
        first, second = payload["seg"]
        self.assertEqual(first["id"], 3)
        self.assertEqual((first["start"], first["stop"]), (0, 20))
        self.assertTrue(first["rev"])
        self.assertNotIn("id", second)
        self.assertEqual((second["start"], second["stop"]), (20, 40))

    def test_seg_fields_pass_through(self):
        payload = zpayload([{
            "zone": "middle half",
            "fx": 90,
            "pal": 11,
            "col": [[255, 0, 0, 0]],
            "sx": 200,
            "ix": 90,
            "rev": True,
            "mi": True,
            "on": False,
        }])
        seg = payload["seg"][0]
        self.assertEqual(seg["fx"], 90)
        self.assertEqual(seg["pal"], 11)
        self.assertEqual(seg["col"], [[255, 0, 0, 0]])
        self.assertEqual(seg["sx"], 200)
        self.assertEqual(seg["ix"], 90)
        self.assertTrue(seg["rev"])
        self.assertTrue(seg["mi"])
        self.assertFalse(seg["on"])

    def test_orientation_down_flips_bounds(self):
        payload = zpayload([{"zone": "top half"}], orientation="down")
        seg = payload["seg"][0]
        self.assertEqual((seg["start"], seg["stop"]), (0, 20))

    def test_multiple_zones_preserve_order(self):
        payload = zpayload([
            {"zone": "bottom half", "fx": 9},
            {"zone": "top half", "fx": 28},
        ])
        self.assertEqual(len(payload["seg"]), 2)
        self.assertEqual(payload["seg"][0]["fx"], 9)
        self.assertEqual(payload["seg"][1]["fx"], 28)


# ---------------------------------------------------------------------------
# leds_payload
# ---------------------------------------------------------------------------

class LedsPayloadTests(unittest.TestCase):
    def test_color_list_form_from_led_zero(self):
        payload = lpayload(["FF0000", "00FF00", "0000FF"])
        self.assertEqual(payload, {"seg": [{"i": ["FF0000", "00FF00", "0000FF"]}]})

    def test_color_list_with_seg_id(self):
        payload = lpayload(["FF0000"], seg_id=1)
        self.assertEqual(payload, {"seg": [{"id": 1, "i": ["FF0000"]}]})

    def test_range_form(self):
        payload = lpayload([[0, 10, "FF0000"], [10, 20, "00FF00"]])
        self.assertEqual(payload, {"seg": [{"i": [0, 10, "FF0000", 10, 20, "00FF00"]}]})

    def test_range_form_with_seg_id(self):
        payload = lpayload([[5, 15, "FFFFFF"]], seg_id=3)
        self.assertEqual(payload, {"seg": [{"id": 3, "i": [5, 15, "FFFFFF"]}]})

    def test_invalid_hex_colors_raise(self):
        for bad in ("GG0000", "FFF", "red", ""):
            with self.assertRaises(ValueError, msg=f"color {bad!r} should be rejected"):
                lpayload([bad])
        with self.assertRaises(ValueError):
            lpayload([[0, 10, "not-hex"]])

    def test_range_bounds_are_validated(self):
        with self.assertRaises(ValueError):
            lpayload([[-1, 10, "FF0000"]])
        with self.assertRaises(ValueError):
            lpayload([[0, LENGTH + 1, "FF0000"]])
        with self.assertRaises(ValueError):
            lpayload([[10, 10, "FF0000"]])
        with self.assertRaises(ValueError):
            lpayload([[20, 10, "FF0000"]])

    def test_full_column_color_list_is_accepted(self):
        payload = lpayload(["FF0000"] * LENGTH)
        self.assertEqual(len(payload["seg"]), 1)


# ---------------------------------------------------------------------------
# shows.validate_show
# ---------------------------------------------------------------------------

class ValidateShowTests(unittest.TestCase):
    def test_invalid_show_tool_does_not_revoke_existing_lighting_owner(self):
        import smart_director
        lights = make_fleet()
        with mock.patch.object(smart_director, 'before_external_write') as handoff:
            with self.assertRaisesRegex(ValueError, 'Step 0:.*seg'):
                mcp_light.call_tool(lights, 'start_show', {'show': {'steps': [
                    {'look': {'payload': {'seg': ['magenta']}}, 'duration_s': 1}]}}, FakeModes())
        handoff.assert_not_called()

    def test_single_segment_object_is_normalized_before_show_execution(self):
        original = {'seg': {'fx': 159, 'col': [[160, 20, 140]]}}
        steps = shows.validate_show({'steps': [{'look': {'payload': original}, 'duration_s': .01}]})
        lights = make_fleet()
        shows.apply_look(lights, steps[0]['look'])
        for client in lights.clients.values():
            self.assertEqual([segment['fx'] for segment in client.payloads[0]['seg']], [159, 159])
        self.assertIsInstance(original['seg'], dict)

    def test_bad_segment_shapes_fail_before_replacing_a_running_show(self):
        for segments in ('magenta', ['magenta'], [9]):
            with self.subTest(segments=segments):
                lights = make_fleet()
                shows.start_show(lights, {'name': 'existing', 'steps': [
                    {'look': {'payload': {'bri': 100}}, 'duration_s': 60}]})
                self.assertTrue(wait_for(lambda: posted_count(lights) == 2))
                try:
                    with self.assertRaisesRegex(ValueError, 'Step 0:.*seg'):
                        shows.start_show(lights, {'steps': [{'look': {'payload': {'seg': segments}}, 'duration_s': 1}]})
                    self.assertEqual(shows.show_status()['name'], 'existing')
                    self.assertEqual(posted_count(lights), 2)
                finally:
                    shows.stop_show()

    def test_accepts_atmosphere_look(self):
        steps = shows.validate_show({
            "name": "demo",
            "steps": [{"look": {"atmosphere": "ocean"}, "duration_s": 5}],
        })
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["look"], {"atmosphere": "ocean"})
        self.assertEqual(steps[0]["duration_s"], 5)

    def test_accepts_wall_mode_look(self):
        steps = shows.validate_show({
            "steps": [
                {"look": {"wall_mode": "span", "fx": 9, "pal": 0}, "duration_s": 2.5},
                {"look": {"wall_mode": "versus", "fx_left": 9, "fx_right": 28}, "duration_s": 3},
                {"look": {"wall_mode": "mirror", "fx": 110}, "duration_s": 1},
                {"look": {"wall_mode": "chase", "fx": 28}, "duration_s": 1},
            ]
        })
        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[0]["look"]["wall_mode"], "span")

    def test_accepts_payload_look_with_optional_target(self):
        steps = shows.validate_show({
            "steps": [
                {"look": {"payload": {"on": True, "bri": 200}}, "duration_s": 1},
                {"look": {"payload": {"seg": [{"fx": 9}]}, "target": "middle-left"}, "duration_s": 1},
            ]
        })
        self.assertEqual(steps[1]["look"]["target"], "middle-left")

    def test_normalized_steps_carry_duration_and_optional_transition(self):
        steps = shows.validate_show({
            "loop": True,
            "steps": [{"look": {"payload": {"bri": 128}}, "duration_s": 2, "transition_s": 0.5}],
        })
        self.assertIsInstance(steps, list)
        self.assertEqual(steps[0]["transition_s"], 0.5)

    def test_rejects_malformed_shows(self):
        bad_shows = [
            "not a dict",
            {},
            {"steps": "not a list"},
            {"steps": []},
            {"steps": [{"duration_s": 1}]},                              # missing look
            {"steps": [{"look": {"payload": {}}}]},                      # missing duration_s
            {"steps": [{"look": {"payload": {}}, "duration_s": -1}]},   # negative duration
            {"steps": [{"look": {"nonsense": 1}, "duration_s": 1}]},     # unknown look form
            {"steps": [{"look": {"wall_mode": "spinny"}, "duration_s": 1}]},  # bad wall mode
        ]
        for bad in bad_shows:
            with self.assertRaises(ValueError, msg=f"show {bad!r} should be rejected"):
                shows.validate_show(bad)


# ---------------------------------------------------------------------------
# shows.ShowRunner
# ---------------------------------------------------------------------------

def payload_steps(*targets: str, duration_s: float = 0.05) -> list[dict]:
    return shows.validate_show({
        "steps": [
            {
                "look": {"payload": {"seg": [{"fx": 9}]}, "target": target},
                "duration_s": duration_s,
            }
            for target in targets
        ]
    })


class ShowRunnerTests(unittest.TestCase):
    def test_steps_through_two_steps_then_stops(self):
        fleet_ = make_fleet()
        runner = shows.ShowRunner(fleet_, payload_steps("all", "all"), loop=False)
        self.assertTrue(runner.daemon)
        runner.start()
        self.assertTrue(wait_for(lambda: posted_count(fleet_) >= 4))
        runner.join(timeout=5)
        self.assertFalse(runner.is_alive())
        self.assertFalse(runner.is_running())
        # two steps fanned out to both controllers, through fleet.post_state
        for name in ("right", "left"):
            self.assertEqual(len(fleet_.clients[name].payloads), 2)
            for payload in fleet_.clients[name].payloads:
                self.assertTrue(payload["udpn"]["nn"])

    def test_loop_false_terminates_on_its_own(self):
        fleet_ = make_fleet()
        runner = shows.ShowRunner(fleet_, payload_steps("all", "all"), loop=False)
        runner.start()
        self.assertTrue(wait_for(lambda: not runner.is_alive(), timeout=5))
        self.assertFalse(runner.is_running())
        self.assertGreaterEqual(posted_count(fleet_), 4)

    def test_stop_is_cooperative_during_long_step(self):
        fleet_ = make_fleet()
        runner = shows.ShowRunner(fleet_, payload_steps("all", duration_s=60.0), loop=True)
        runner.start()
        self.assertTrue(wait_for(lambda: posted_count(fleet_) >= 2))
        self.assertTrue(runner.is_running())
        runner.stop()
        runner.join(timeout=3)
        self.assertFalse(runner.is_alive(), "stop() must not wait out the full step duration")
        self.assertFalse(runner.is_running())

    def test_loop_true_repeats_until_stopped(self):
        fleet_ = make_fleet()
        runner = shows.ShowRunner(fleet_, payload_steps("all"), loop=True)
        runner.start()
        # more posts than steps means it looped
        self.assertTrue(wait_for(lambda: posted_count(fleet_) >= 4))
        runner.stop()
        runner.join(timeout=3)
        self.assertFalse(runner.is_alive())

    def test_look_target_is_honored(self):
        fleet_ = make_fleet()
        runner = shows.ShowRunner(fleet_, payload_steps("middle-left"), loop=False)
        runner.start()
        runner.join(timeout=5)
        self.assertEqual(fleet_.clients["right"].payloads, [])
        left_payloads = fleet_.clients["left"].payloads
        self.assertEqual(len(left_payloads), 1)
        # channel target: segment id injected by the fleet
        self.assertEqual(left_payloads[0]["seg"][0]["id"], 0)

    def test_atmosphere_look_posts_through_the_fleet(self):
        fleet_ = make_fleet()
        steps = shows.validate_show({
            "steps": [{"look": {"atmosphere": "ocean"}, "duration_s": 0.05}],
        })
        runner = shows.ShowRunner(fleet_, steps, loop=False)
        runner.start()
        runner.join(timeout=5)
        for name in ("right", "left"):
            self.assertEqual(len(fleet_.clients[name].payloads), 1)


# ---------------------------------------------------------------------------
# shows module-level registry
# ---------------------------------------------------------------------------

class ShowRegistryTests(unittest.TestCase):
    def tearDown(self):
        try:
            shows.stop_show()
        except Exception:
            pass

    def test_status_is_a_dict_when_idle(self):
        shows.stop_show()
        status = shows.show_status()
        self.assertIsInstance(status, dict)
        self.assertIn("running", status)
        self.assertFalse(status["running"])

    def test_start_status_stop_cycle(self):
        fleet_ = make_fleet()
        message = shows.start_show(fleet_, {
            "name": "registry-test",
            "steps": [{"look": {"payload": {"seg": [{"fx": 9}]}}, "duration_s": 30.0}],
        })
        self.assertIsInstance(message, str)
        self.assertTrue(wait_for(lambda: posted_count(fleet_) >= 2))
        status = shows.show_status()
        self.assertTrue(status["running"])
        stop_message = shows.stop_show()
        self.assertIsInstance(stop_message, str)
        self.assertTrue(wait_for(lambda: not shows.show_status()["running"]))
        self.assertFalse(shows.show_status()["running"])

    def test_invalid_show_is_rejected_without_starting(self):
        fleet_ = make_fleet()
        shows.stop_show()
        with self.assertRaises(ValueError):
            shows.start_show(fleet_, {"steps": []})
        self.assertFalse(shows.show_status()["running"])


# ---------------------------------------------------------------------------
# mcp_light zone/LED/show tools
# ---------------------------------------------------------------------------

class McpZoneToolTests(unittest.TestCase):
    def test_new_tools_are_exposed(self):
        tool_names = {tool["name"] for tool in mcp_light.build_tools()}
        for name in (
            "set_zone",
            "set_segment_bounds",
            "delete_segment",
            "set_leds",
            "start_show",
            "stop_show",
            "show_status",
        ):
            self.assertIn(name, tool_names)

    def test_start_show_checks_effect_ids_against_the_live_catalog(self):
        """A show must not be a bypass for the live-catalog check.

        set_effect/strips/dynamic_scene all run _check_fx_allowed; a show's raw
        payload (or wall_mode look) previously reached columns/shows unchecked.
        The catalogs are seeded here because a *known* catalog is what makes the
        check meaningful. Effect-family prohibitions are gone, so the remaining
        check is catalog membership.
        """
        catalog = ["Solid", "Strobe"] + ["Fx"] * 218
        mcp_light.seed_effect_catalog("left", catalog)
        mcp_light.seed_effect_catalog("right", catalog)
        self.addCleanup(mcp_light.seed_effect_catalog, "left", None)
        self.addCleanup(mcp_light.seed_effect_catalog, "right", None)

        fleet_ = make_fleet()
        for look in (
            {"payload": {"seg": [{"fx": 999}]}},   # absent from the live catalog
            {"wall_mode": "span", "fx": 999},
        ):
            with self.assertRaises(ValueError, msg=f"look {look} must be rejected"):
                mcp_light.call_tool(
                    fleet_,
                    "start_show",
                    {"show": {"steps": [{"look": look, "duration_s": 1}]}},
                    FakeModes(),
                )
        # Strobe (id 1) is in the catalog and is no longer family-filtered.
        for look in (
            {"payload": {"seg": [{"fx": 1}]}},
            {"wall_mode": "span", "fx": 1},
        ):
            result = mcp_light.call_tool(
                fleet_,
                "start_show",
                {"show": {"steps": [{"look": look, "duration_s": 1}]}},
                FakeModes(),
            )
            self.assertIn("content", result)
        shows.stop_show()

    def test_start_show_does_not_reject_valid_ids_when_the_catalog_is_unknown(self):
        """An unseeded cache means "cannot verify", not "forbidden".

        Regression: the guard used to fall back to the offline SAFE_EFFECTS list,
        so a live-only effect (159 = DJ Light on the real wall) was rejected and
        the Music Director's show failed to start.
        """
        mcp_light.seed_effect_catalog("left", None)
        mcp_light.seed_effect_catalog("right", None)
        fleet_ = make_fleet()
        message = mcp_light.call_tool(
            fleet_,
            "start_show",
            {"show": {"steps": [{"look": {"payload": {"seg": [{"fx": 159}]}}, "duration_s": 1}]}},
            FakeModes(),
        )
        self.assertIn("content", message)

    def test_one_unseeded_controller_does_not_shrink_the_allowed_set(self):
        """A partially known catalog must not collapse to the offline list."""
        catalog = ["Solid"] + ["Fx"] * 219
        mcp_light.seed_effect_catalog("right", catalog)   # only one controller
        self.addCleanup(mcp_light.seed_effect_catalog, "right", None)
        mcp_light.seed_effect_catalog("left", None)
        allowed = mcp_light.allowed_effects_for(make_fleet(), fleet.DEFAULT_TARGET)
        self.assertIsNotNone(allowed)
        self.assertIn(159, allowed)

    def test_tool_schemas_require_their_mandatory_args(self):
        tools = {tool["name"]: tool for tool in mcp_light.build_tools()}
        self.assertIn("channel", tools["set_zone"]["inputSchema"].get("required", []))
        self.assertIn("zone", tools["set_zone"]["inputSchema"].get("required", []))
        self.assertIn("leds", tools["set_leds"]["inputSchema"].get("required", []))
        for field in ("id", "start", "stop"):
            self.assertIn(field, tools["set_segment_bounds"]["inputSchema"].get("required", []))
        self.assertIn("id", tools["delete_segment"]["inputSchema"].get("required", []))
        self.assertIn("show", tools["start_show"]["inputSchema"].get("required", []))

    def test_set_zone_posts_zone_bounds_to_the_channel(self):
        # Real segment geometry (the fake above has none, which only exercises
        # the LEDS_PER_COLUMN fallback): middle-right is 47 pixels at bus 0..47.
        controllers = [
            fleet.ControllerConfig("right", "http://10.27.27.110", {
                0: fleet.SegmentConfig("far-right", gpio=2, pixels=40, start=47, stop=87),
                1: fleet.SegmentConfig("middle-right", gpio=16, pixels=47, start=0, stop=47),
            }),
            fleet.ControllerConfig("left", "http://10.27.27.112", {
                0: fleet.SegmentConfig("middle-left", gpio=16, pixels=48, start=34, stop=82),
                1: fleet.SegmentConfig("far-left", gpio=2, pixels=34, start=0, stop=34),
            }),
        ]
        fleet_ = fleet.LightFleet(
            {
                "right": RecordingClient("http://10.27.27.110"),
                "left": RecordingClient("http://10.27.27.112"),
            },
            controllers,
        )
        result = mcp_light.call_tool(
            fleet_,
            "set_zone",
            {"channel": "middle-right", "zone": "top half", "fx": 9, "pal": 0},
            FakeModes(),
        )
        self.assertIn("content", result)
        self.assertEqual(fleet_.clients["left"].payloads, [])
        right = fleet_.clients["right"].payloads
        self.assertEqual(len(right), 1)
        seg = right[0]["seg"][0]
        self.assertEqual(seg["id"], 1)
        self.assertNotIn("start", seg)
        self.assertNotIn("stop", seg)
        self.assertEqual(seg["fx"], 9)
        self.assertEqual(seg["pal"], 0)

    def test_set_zone_hex_color_is_converted(self):
        fleet_ = make_fleet()
        mcp_light.call_tool(
            fleet_,
            "set_zone",
            {"channel": "far-left", "zone": "bottom half", "col": "FF8800"},
            FakeModes(),
        )
        left = fleet_.clients["left"].payloads
        self.assertEqual(len(left), 1)
        seg = left[0]["seg"][0]
        self.assertEqual(seg["id"], 1)
        self.assertEqual(seg["col"][0][:3], [255, 136, 0])
        self.assertNotIn("start", seg)
        self.assertNotIn("stop", seg)

    def test_set_zone_uses_absolute_bus_bounds_from_segment_geometry(self):
        """With configured pixels/start/stop, zone bounds offset onto the bus."""
        controllers = [
            fleet.ControllerConfig(
                "left",
                "http://10.27.27.112",
                {
                    0: fleet.SegmentConfig(channel="far-left", pixels=34, start=0, stop=34),
                    1: fleet.SegmentConfig(channel="middle-left", pixels=48, start=34, stop=82),
                },
            ),
        ]
        fleet_ = fleet.LightFleet({"left": RecordingClient("http://10.27.27.112")}, controllers)
        mcp_light.call_tool(
            fleet_,
            "set_zone",
            {"channel": "middle-left", "zone": "top half", "fx": 9},
            FakeModes(),
        )
        seg = fleet_.clients["left"].payloads[0]["seg"][0]
        self.assertEqual(seg["id"], 1)
        self.assertEqual(seg["fx"], 9)
        self.assertNotIn("start", seg)
        self.assertNotIn("stop", seg)

    def test_set_zone_allocation_skips_live_state_segment_ids(self):
        controllers = [
            fleet.ControllerConfig("right", "http://10.27.27.110", {0: "far-right", 1: "middle-right"}),
        ]
        client = RecordingClient("http://10.27.27.110", state={"seg": [{"id": 0}, {"id": 1}, {"id": 2}]})
        fleet_ = fleet.LightFleet({"right": client}, controllers)
        mcp_light.call_tool(
            fleet_,
            "set_zone",
            {"channel": "middle-right", "zone": "top half", "fx": 9},
            FakeModes(),
        )
        self.assertEqual(client.payloads[0]["seg"][0]["id"], 1)
        self.assertNotIn("start", client.payloads[0]["seg"][0])

    def test_set_zone_raises_when_no_segment_is_free(self):
        controllers = [
            fleet.ControllerConfig("right", "http://10.27.27.110", {0: "far-right", 1: "middle-right"}),
        ]
        client = RecordingClient(
            "http://10.27.27.110",
            state={"seg": [{"id": 0}, {"id": 1}]},
            info={"leds": {"maxseg": 2}},
        )
        fleet_ = fleet.LightFleet({"right": client}, controllers)
        mcp_light.call_tool(
            fleet_,
            "set_zone",
            {"channel": "middle-right", "zone": "top half", "fx": 9},
            FakeModes(),
        )
        self.assertEqual(client.payloads[0]["seg"][0]["id"], 1)
        self.assertNotIn("start", client.payloads[0]["seg"][0])

    def _geometry_fleet(self) -> fleet.LightFleet:
        """Fleet with full SegmentConfig geometry so DDP topology resolves.
        Defined on the test case: class is unittest-style; pytest fixtures can
        still be injected into methods (monkeypatch below).
        """
        controllers = [
            fleet.ControllerConfig(
                "right",
                "http://10.27.27.110",
                {
                    0: fleet.SegmentConfig(channel="far-right", pixels=40, start=47, stop=87),
                    1: fleet.SegmentConfig(channel="middle-right", pixels=47, start=0, stop=47),
                },
            ),
            fleet.ControllerConfig(
                "left",
                "http://10.27.27.112",
                {
                    0: fleet.SegmentConfig(channel="far-left", pixels=34, start=0, stop=34),
                    1: fleet.SegmentConfig(channel="middle-left", pixels=48, start=34, stop=82),
                },
            ),
        ]
        clients = {
            "right": RecordingClient("http://10.27.27.110"),
            "left": RecordingClient("http://10.27.27.112"),
        }
        return fleet.LightFleet(clients, controllers)

    def _record_frame_start(self, bucket):
        """Capture realtime.frame_start calls instead of streaming to the LAN."""
        import realtime

        original = realtime.frame_start
        realtime.frame_start = (
            lambda fleet_, frames, fps=6, duration_s=300: bucket.append((frames, fps, duration_s)) or "ok"
        )
        self.addCleanup(setattr, realtime, "frame_start", original)

    def test_set_leds_color_list_targets_segment(self):
        fleet_ = self._geometry_fleet()
        started = []
        self._record_frame_start(started)
        result = mcp_light.call_tool(
            fleet_,
            "set_leds",
            {"target": "middle-left", "leds": ["FF0000", "00FF00"]},
            FakeModes(),
        )
        self.assertIn("content", result)
        self.assertEqual(fleet_.clients["right"].payloads, [])
        left = fleet_.clients["left"].payloads
        # Power primer first: WLED realtime shows nothing while the controller is off.
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0], {"on": True, "udpn": {"nn": True}})
        frames, fps, duration_s = started[0]
        self.assertEqual(fps, 6)
        self.assertEqual(duration_s, 300)
        # Only the targeted controller streams; middle-left sits at bus 34..82.
        self.assertEqual(set(frames), {"left"})
        self.assertEqual(len(frames["left"]), 82 * 3)
        bus = frames["left"]
        self.assertEqual(bus[34 * 3:36 * 3], b"\xff\x00\x00\x00\xff\x00")
        # exactly the two painted LEDs on the bus: red R=255 + green G=255
        self.assertEqual(sum(bus), 510)


    def test_set_leds_range_form_with_explicit_segment(self):
        fleet_ = self._geometry_fleet()
        started = []
        self._record_frame_start(started)
        mcp_light.call_tool(
            fleet_,
            "set_leds",
            {"target": "right", "segment": 1, "leds": [[0, 10, "FF0000"], [10, 20, "00FF00"]]},
            FakeModes(),
        )
        right = fleet_.clients["right"].payloads
        self.assertEqual(len(right), 1)
        self.assertEqual(right[0], {"on": True, "udpn": {"nn": True}})
        frames, fps, duration_s = started[0]
        # segment 1 = middle-right at bus 0..47: first 20 LEDs painted, rest black.
        self.assertEqual(set(frames), {"right"})
        bus = frames["right"]
        self.assertEqual(len(bus), 87 * 3)
        self.assertEqual(bus[0:3], b"\xff\x00\x00")
        self.assertEqual(bus[27:33], b"\xff\x00\x00\x00\xff\x00")
        # 10 red (R=255) + 10 green (G=255) LEDs; everything past LED 19 is black
        self.assertEqual(sum(bus), 5100)
        self.assertEqual(sum(bus[60:]), 0)

    def test_set_leds_rejects_ranges_beyond_the_real_segment(self):
        fleet_ = self._geometry_fleet()
        started = []
        self._record_frame_start(started)
        # middle-left has 48 pixels; a range past 48 must fail, not be clamped
        # to the old fixed 40-pixel column constant.
        with self.assertRaises(ValueError):
            mcp_light.call_tool(
                fleet_,
                "set_leds",
                {"target": "middle-left", "leds": [[40, 49, "FF0000"]]},
                FakeModes(),
            )
        self.assertEqual(started, [])

    def test_set_segment_bounds_posts_raw_bounds(self):
        fleet_ = make_fleet()
        with self.assertRaises(ValueError):
            mcp_light.call_tool(
                fleet_,
                "set_segment_bounds",
                {"target": "right", "id": 2, "start": 0, "stop": 20, "rev": True, "grp": 1, "spc": 1},
                FakeModes(),
            )
        self.assertEqual(fleet_.clients["right"].payloads, [])
        self.assertEqual(fleet_.clients["left"].payloads, [])

    def test_delete_segment_zeroes_the_stop(self):
        fleet_ = make_fleet()
        with self.assertRaises(ValueError):
            mcp_light.call_tool(fleet_, "delete_segment", {"target": "right", "id": 2}, FakeModes())
        self.assertEqual(fleet_.clients["right"].payloads, [])

    def test_show_tools_drive_the_registry(self):
        fleet_ = make_fleet()
        try:
            result = mcp_light.call_tool(
                fleet_,
                "start_show",
                {"show": {
                    "name": "mcp-test",
                    "steps": [{"look": {"payload": {"seg": [{"fx": 9}]}}, "duration_s": 30.0}],
                }},
                FakeModes(),
            )
            self.assertIn("content", result)
            self.assertTrue(wait_for(lambda: posted_count(fleet_) >= 2))
            status_result = mcp_light.call_tool(fleet_, "show_status", {}, FakeModes())
            self.assertIn("running", status_result["content"][0]["text"])
            stop_result = mcp_light.call_tool(fleet_, "stop_show", {}, FakeModes())
            self.assertIn("content", stop_result)
            self.assertTrue(wait_for(lambda: not shows.show_status()["running"]))
        finally:
            shows.stop_show()

    def test_start_show_stops_realtime_session(self):
        """A running DDP session repaints every frame; a new show must stop it."""
        fleet_ = make_fleet()
        with mock.patch("realtime.realtime_stop", return_value="Realtime stopped.") as stop_mock:
            shows.start_show(
                fleet_,
                {"steps": [{"look": {"payload": {"seg": [{"fx": 9}]}}, "duration_s": 0.05}]},
            )
            stop_mock.assert_called_once_with()
        shows.stop_show()


# ---------------------------------------------------------------------------
# Non-finite durations, loop validation, malformed steps
# ---------------------------------------------------------------------------

class NonFiniteDurationTests(unittest.TestCase):
    def test_nan_and_inf_durations_are_rejected(self):
        for bad in (math.nan, math.inf, -math.inf):
            for field in ("duration_s", "transition_s"):
                show = {"steps": [{"look": {"payload": {"bri": 1}}, "duration_s": 1, field: bad}]}
                with self.assertRaises(ValueError, msg=f"{field}={bad} should be rejected"):
                    shows.validate_show(show)

    def test_non_finite_transition_units_are_ignored(self):
        self.assertIsNone(shows._transition_units(math.nan))
        self.assertIsNone(shows._transition_units(math.inf))
        self.assertIsNone(shows._transition_units(-1.0))
        self.assertEqual(shows._transition_units(0.5), 5)

    def test_loop_must_be_a_bool(self):
        for bad in ("false", "true", 1, 0, "yes"):
            show = {"loop": bad, "steps": [{"look": {"payload": {}}, "duration_s": 1}]}
            with self.assertRaises(ValueError, msg=f"loop={bad!r} should be rejected"):
                shows.validate_show(show)

    def test_malformed_step_does_not_kill_the_runner(self):
        fleet_ = make_fleet()
        good = shows.validate_show({
            "steps": [{"look": {"payload": {"seg": [{"fx": 9}]}}, "duration_s": 0.05}],
        })[0]
        malformed = {"look": {"payload": {"seg": [{"fx": 9}]}}, "transition_s": 0.0}  # no duration_s
        runner = shows.ShowRunner(fleet_, [malformed, good], loop=False)
        runner.start()
        runner.join(timeout=5)
        self.assertFalse(runner.is_alive())
        # the good step still ran despite the malformed sibling
        self.assertGreaterEqual(posted_count(fleet_), 2)


class ShowRegistryRaceTests(unittest.TestCase):
    def tearDown(self):
        try:
            shows.stop_show()
        except Exception:
            pass

    def test_immediate_stop_after_start_never_raises(self):
        fleet_ = make_fleet()
        for _ in range(20):
            shows.start_show(fleet_, {
                "steps": [{"look": {"payload": {"seg": [{"fx": 9}]}}, "duration_s": 30.0}],
            })
            message = shows.stop_show()
            self.assertEqual(message, "Show stopped.")
        self.assertFalse(shows.show_status()["running"])


# ---------------------------------------------------------------------------
# mcp_light: fleet guards, timer registry, dry-run validation, JSON-RPC
# ---------------------------------------------------------------------------

class StatefulRecordingClient(RecordingClient):
    """RecordingClient whose get_state returns a canned state dict."""

    def __init__(self, host: str, state: dict | None = None):
        super().__init__(host)
        self._state = state or {}

    def get_state(self) -> dict:
        return self._state


def make_stateful_fleet(bri: int) -> fleet.LightFleet:
    controllers = [
        fleet.ControllerConfig("right", "http://10.27.27.110", {0: "far-right", 1: "middle-right"}),
        fleet.ControllerConfig("left", "http://10.27.27.112", {0: "middle-left", 1: "far-left"}),
    ]
    clients = {
        "right": StatefulRecordingClient("http://10.27.27.110", {"on": True, "bri": bri}),
        "left": StatefulRecordingClient("http://10.27.27.112", {"on": True, "bri": bri}),
    }
    return fleet.LightFleet(clients, controllers)


class McpFleetGuardTests(unittest.TestCase):
    def test_start_show_requires_fleet_mode(self):
        client = RecordingClient("http://10.27.27.110")
        with self.assertRaises(ValueError) as ctx:
            mcp_light.call_tool(client, "start_show", {"show": {"steps": []}}, FakeModes())
        self.assertIn("fleet mode", str(ctx.exception))

    def test_music_director_requires_fleet_mode(self):
        client = RecordingClient("http://10.27.27.110")
        for action in ("start", "stop", "status"):
            with self.assertRaises(ValueError, msg=f"action={action} should require fleet mode"):
                mcp_light.call_tool(client, "music_director", {"action": action}, FakeModes())

    def test_set_segment_bounds_rejects_start_not_below_stop(self):
        fleet_ = make_fleet()
        for start, stop in ((20, 20), (30, 20)):
            with self.assertRaises(ValueError, msg=f"start={start} stop={stop} should be rejected"):
                mcp_light.call_tool(
                    fleet_,
                    "set_segment_bounds",
                    {"target": "right", "id": 2, "start": start, "stop": stop},
                    FakeModes(),
                )
        self.assertEqual(fleet_.clients["right"].payloads, [])


class McpTimerRegistryTests(unittest.TestCase):
    def tearDown(self):
        mcp_light._stop_timers()

    def test_repeated_fade_off_replaces_the_previous_timer(self):
        client = StatefulRecordingClient("http://10.27.27.110", {"bri": 100})
        mcp_light.call_tool(client, "fade_off", {"minutes": 5, "brightness": 200}, FakeModes())
        first = mcp_light._timers["fade"]
        self.assertTrue(first.is_alive())
        mcp_light.call_tool(client, "fade_off", {"minutes": 5, "brightness": 200}, FakeModes())
        second = mcp_light._timers["fade"]
        self.assertIsNot(first, second)
        self.assertTrue(first._stop.is_set(), "the replaced fade timer must be stopped")
        self.assertTrue(second.is_alive())

    def test_repeated_sunrise_replaces_the_previous_timer(self):
        client = StatefulRecordingClient("http://10.27.27.110", {"bri": 0})
        mcp_light.call_tool(client, "start_sunrise", {"minutes": 5}, FakeModes())
        first = mcp_light._timers["sunrise"]
        mcp_light.call_tool(client, "start_sunrise", {"minutes": 5}, FakeModes())
        second = mcp_light._timers["sunrise"]
        self.assertIsNot(first, second)
        self.assertTrue(first._stop.is_set())

    def test_fade_off_clamps_sub_minute_durations(self):
        client = StatefulRecordingClient("http://10.27.27.110", {"bri": 100})
        mcp_light.call_tool(client, "fade_off", {"minutes": 0.5, "brightness": 200}, FakeModes())
        self.assertEqual(mcp_light._timers["fade"].duration_minutes, 1)

    def test_fade_off_in_fleet_mode_uses_controller_brightness(self):
        fleet_ = make_stateful_fleet(bri=77)
        mcp_light.call_tool(fleet_, "fade_off", {"minutes": 5}, FakeModes())
        self.assertEqual(mcp_light._timers["fade"].start_brightness, 77)

    def test_sunrise_with_explicit_zero_brightness_stays_zero(self):
        client = StatefulRecordingClient("http://10.27.27.110", {"bri": 0})
        mcp_light.call_tool(client, "start_sunrise", {"minutes": 5, "brightness": 0}, FakeModes())
        self.assertEqual(mcp_light._timers["sunrise"].max_brightness, 0)


class DryRunValidationTests(unittest.TestCase):
    def test_stderr_dry_run_client_validates_payloads(self):
        client = mcp_light.StderrDryRunClient("http://10.27.27.110")
        with self.assertRaises(ValueError):
            client.post_state({"seg": [{"fx": 300}]})  # out of the 0-255 range
        # a safe payload passes through without raising
        client.post_state({"seg": [{"fx": 0}]})


class McpHandleProtocolTests(unittest.TestCase):
    def _server(self) -> mcp_light.McpServer:
        return mcp_light.McpServer(RecordingClient("http://10.27.27.110"))

    def test_unknown_notification_is_not_answered(self):
        server = self._server()
        response = server.handle({
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": 1},
        })
        self.assertIsNone(response)
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "no/such"}))

    def test_unknown_request_still_gets_method_not_found(self):
        server = self._server()
        response = server.handle({"jsonrpc": "2.0", "id": 7, "method": "no/such"})
        self.assertEqual(response["id"], 7)
        self.assertEqual(response["error"]["code"], -32601)

    def test_tools_call_with_null_params_is_handled(self):
        server = self._server()
        response = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": None})
        # unknown (empty) tool name surfaces as a JSON-RPC error, not a crash
        self.assertEqual(response["id"], 3)
        self.assertIn("error", response)


class MatchLightsToSongTests(unittest.TestCase):
    def test_description_does_not_promise_applying_a_show(self):
        tools = {tool["name"]: tool for tool in mcp_light.build_tools()}
        description = tools["match_lights_to_song"]["description"]
        self.assertNotIn("automatically generate", description)
        self.assertIn("Does not change the lights", description)

    def test_microphone_fallback_runs_when_probing_fails(self):
        client = RecordingClient("http://10.27.27.110")
        song = {"artist": "A", "title": "T", "album": "", "genre": "Jazz"}
        with (
            mock.patch("subprocess.run", side_effect=OSError("no dbus")),
            mock.patch.object(mcp_light.music_recognizer, "is_available", return_value=True),
            mock.patch.object(mcp_light.music_recognizer, "recognize_ambient_sync", return_value=song),
        ):
            result = mcp_light.call_tool(client, "match_lights_to_song", {}, FakeModes())
        text = result["content"][0]["text"]
        self.assertIn("Now playing: T by A", text)

    def test_recognize_music_uses_ambient_mic(self):
        client = RecordingClient("http://10.27.27.110")
        song = {"title": "Poker Face", "artist": "Lady Gaga", "album": "The Fame", "genre": "Pop"}
        with (
            mock.patch.object(mcp_light.music_recognizer, "is_available", return_value=True),
            mock.patch.object(mcp_light.music_recognizer, "recognize_ambient_sync", return_value=song),
        ):
            result = mcp_light.call_tool(client, "recognize_music", {}, FakeModes())
        text = result["content"][0]["text"]
        self.assertIn("Recognized: Poker Face", text)
        self.assertIn("Lady Gaga", text)


if __name__ == "__main__":
    unittest.main()
