"""Per-strip color/effect control (columns.py + the AI `strips` tool).

Two layers are covered:

1. `columns` composers normalize `col` / `colors` / `color` (hex string, flat
   RGB list, or list of RGB lists) onto WLED's ``col`` field, per strip, and
   still post exactly one multi-segment payload per controller.
2. `mcp_light`'s ``strips`` tool advertises the new per-strip fields, validates
   them strictly (channel names, colors, bri, on, live effect catalog), and
   forwards normalized values to the composers.

The fake fleet + dry-run RecordingClient pattern is the one used by
tests/test_columns.py, so the assertions are on the exact payloads WLED gets.
"""

from __future__ import annotations

import unittest

import ai_chat
import columns
import fleet
import lightctl
import mcp_light


class RecordingClient(lightctl.LightClient):
    """dry_run LightClient that records every posted payload."""

    def __init__(self, host: str):
        super().__init__(host, dry_run=True)
        self.payloads: list[dict] = []

    def post_state(self, payload: dict) -> None:
        lightctl.validate_wled_payload(payload)
        self.payloads.append(payload)


def make_fleet() -> fleet.LightFleet:
    controllers = [
        fleet.ControllerConfig("left", "http://10.27.27.110", {0: "far-left", 1: "middle-left"}),
        fleet.ControllerConfig("right", "http://10.27.27.112", {0: "far-right", 1: "middle-right"}),
    ]
    clients = {
        "left": RecordingClient("http://10.27.27.110"),
        "right": RecordingClient("http://10.27.27.112"),
    }
    return fleet.LightFleet(clients, controllers)


def only_payload(fleet_: fleet.LightFleet, controller: str) -> dict:
    payloads = fleet_.clients[controller].payloads
    assert len(payloads) == 1, f"expected one POST to {controller}, got {payloads!r}"
    return payloads[0]


def seg_map(payload: dict) -> dict[int, dict]:
    return {seg["id"]: seg for seg in payload["seg"]}


def strips_tool() -> dict:
    return next(tool for tool in mcp_light.build_tools() if tool["name"] == "strips")


def result_text(result: dict) -> str:
    return result["content"][0]["text"]


# ---------------------------------------------------------------------------
# columns.py: per-strip colors / effect / palette
# ---------------------------------------------------------------------------


class ColumnsPerStripTests(unittest.TestCase):
    def test_per_strip_effect_palette_and_colors_per_controller(self):
        fleet_ = make_fleet()
        columns.per_strip(
            fleet_,
            [
                {
                    "channel": "far-left",
                    "fx": 59,
                    "pal": 2,
                    "colors": [[255, 0, 0], [0, 0, 255]],
                },
                {"channel": "middle-left", "fx": 28, "sx": 200, "ix": 90},
                {"channel": "far-right", "color": "FF8800", "bri": 120, "on": True},
            ],
        )

        left = seg_map(only_payload(fleet_, "left"))
        self.assertEqual(set(left), {0, 1})
        self.assertEqual(left[0]["fx"], 59)
        self.assertEqual(left[0]["pal"], 2)
        self.assertEqual(left[0]["col"], [[255, 0, 0], [0, 0, 255]])
        self.assertEqual(left[1]["fx"], 28)
        self.assertEqual(left[1]["sx"], 200)
        self.assertEqual(left[1]["ix"], 90)

        right = seg_map(only_payload(fleet_, "right"))
        self.assertEqual(set(right), {0})  # middle-right was left untouched
        self.assertEqual(right[0]["col"], [[255, 136, 0]])
        self.assertEqual(right[0]["bri"], 120)
        self.assertIs(right[0]["on"], True)

    def test_per_strip_clamps_color_components(self):
        fleet_ = make_fleet()
        columns.per_strip(
            fleet_,
            [{"channel": "far-left", "colors": [[300, -4, 128], [0, 999, 0]]}],
        )

        col = seg_map(only_payload(fleet_, "left"))[0]["col"]
        self.assertEqual(col, [[255, 0, 128], [0, 255, 0]])

    def test_per_strip_accepts_hex_flat_rgb_and_hex_lists(self):
        fleet_ = make_fleet()
        columns.per_strip(
            fleet_,
            [
                {"channel": "far-left", "colors": "#00FF00"},
                {"channel": "middle-left", "colors": [255, 0, 0]},
                {"channel": "middle-right", "colors": ["0000FF", "FF0000"]},
            ],
        )

        left = seg_map(only_payload(fleet_, "left"))
        self.assertEqual(left[0]["col"], [[0, 255, 0]])
        self.assertEqual(left[1]["col"], [[255, 0, 0]])
        right = seg_map(only_payload(fleet_, "right"))
        self.assertEqual(right[1]["col"], [[0, 0, 255], [255, 0, 0]])

    def test_col_wins_over_colors_alias(self):
        fleet_ = make_fleet()
        columns.per_strip(
            fleet_,
            [
                {
                    "channel": "far-left",
                    "col": "FF0000",
                    "colors": [[0, 0, 255]],
                }
            ],
        )

        self.assertEqual(seg_map(only_payload(fleet_, "left"))[0]["col"], [[255, 0, 0]])

    def test_apply_channels_accepts_shared_colors(self):
        fleet_ = make_fleet()
        columns.apply_channels(
            fleet_, ["far-left", "far-right"], fx=9, colors=[[10, 20, 30]]
        )

        left = seg_map(only_payload(fleet_, "left"))
        right = seg_map(only_payload(fleet_, "right"))
        self.assertEqual(left[0]["fx"], 9)
        self.assertEqual(left[0]["col"], [[10, 20, 30]])
        self.assertEqual(right[0]["col"], [[10, 20, 30]])

    def test_set_channel_accepts_colors_hex_and_flat_rgb(self):
        fleet_ = make_fleet()
        columns.set_channel(fleet_, "middle-right", colors="00FF00")
        self.assertEqual(
            only_payload(fleet_, "right"),
            {"seg": [{"id": 1, "col": [[0, 255, 0]]}], "udpn": {"nn": True}},
        )

        fleet_ = make_fleet()
        columns.set_channel(fleet_, "middle-left", colors=[0, 0, 255])
        self.assertEqual(
            only_payload(fleet_, "left"),
            {"seg": [{"id": 1, "col": [[0, 0, 255]]}], "udpn": {"nn": True}},
        )


# ---------------------------------------------------------------------------
# mcp_light: strips tool schema
# ---------------------------------------------------------------------------


class StripsSchemaTests(unittest.TestCase):
    def test_assignment_schema_declares_colors_bri_on(self):
        tool = strips_tool()
        props = tool["inputSchema"]["properties"]
        assignment = props["assignments"]["items"]["properties"]

        for key in ("col", "colors", "bri", "on"):
            self.assertIn(key, assignment)
        for key in ("col", "colors"):
            self.assertEqual(assignment[key]["type"], ["array", "string"])
        self.assertEqual(assignment["bri"]["minimum"], 0)
        self.assertEqual(assignment["bri"]["maximum"], 255)
        self.assertEqual(assignment["on"]["type"], "boolean")
        self.assertIn("colors", props)  # channels mode: shared look
        self.assertEqual(props["colors"]["type"], ["array", "string"])
        self.assertIn("different colors/palettes/effects per strip", tool["description"])

    def test_schema_accepts_new_arguments_and_rejects_bad_types(self):
        schema = strips_tool()["inputSchema"]

        assignments = {
            "assignments": [
                {
                    "channel": "far-left",
                    "fx": 59,
                    "pal": 2,
                    "colors": [[255, 0, 0], "00FF00"],
                    "bri": 120,
                    "on": True,
                }
            ]
        }
        self.assertEqual(ai_chat._validate_against_schema(assignments, schema), [])

        channels = {"channels": ["far-left", "far-right"], "fx": 9, "colors": "FF8800"}
        self.assertEqual(ai_chat._validate_against_schema(channels, schema), [])

        bad = {"assignments": [{"channel": "far-left", "colors": 5}]}
        self.assertTrue(ai_chat._validate_against_schema(bad, schema))


# ---------------------------------------------------------------------------
# mcp_light: strips tool handler
# ---------------------------------------------------------------------------


class StripsHandlerTests(unittest.TestCase):
    def setUp(self):
        lightctl._fx_history.clear()
        mcp_light._fx_allowed.clear()

    def tearDown(self):
        lightctl._fx_history.clear()
        mcp_light._fx_allowed.clear()

    def call(self, fleet_: fleet.LightFleet, arguments: dict) -> str:
        return result_text(mcp_light.call_tool(fleet_, "strips", arguments, None))

    def seed_catalog(self) -> None:
        catalog = ["Solid", "Breathe", "GEQ", "RSVD"]
        for controller in ("left", "right"):
            mcp_light.seed_effect_catalog(controller, catalog)

    def test_applies_assignments_with_per_strip_colors(self):
        fleet_ = make_fleet()
        text = self.call(
            fleet_,
            {
                "assignments": [
                    {
                        "channel": "far-left",
                        "fx": 9,
                        "colors": [[255, 0, 0], [0, 0, 255]],
                        "bri": 120,
                        "on": True,
                    },
                    {"channel": "middle-right", "colors": "00FF00", "bri": 64},
                ]
            },
        )

        left = seg_map(only_payload(fleet_, "left"))[0]
        self.assertEqual(left["fx"], 9)
        self.assertEqual(left["col"], [[255, 0, 0], [0, 0, 255]])
        self.assertEqual(left["bri"], 120)
        self.assertIs(left["on"], True)

        right = seg_map(only_payload(fleet_, "right"))
        self.assertEqual(set(right), {1})  # middle-right lives on segment 1
        self.assertEqual(right[1]["col"], [[0, 255, 0]])
        self.assertEqual(right[1]["bri"], 64)
        self.assertNotIn("fx", right[1])
        self.assertIn("Applied per-strip looks", text)
        # Per-strip effects still feed the variety hint.
        self.assertEqual(lightctl.recent_fx_ids()[0], 9)

    def test_channels_mode_shared_colors(self):
        fleet_ = make_fleet()
        text = self.call(
            fleet_,
            {
                "channels": ["far-left", "middle-right"],
                "fx": 9,
                "colors": "FF8800",
                "speed": 50,
            },
        )

        self.assertEqual(
            seg_map(only_payload(fleet_, "left"))[0],
            {"id": 0, "fx": 9, "col": [[255, 136, 0]], "sx": 50},
        )
        self.assertEqual(
            seg_map(only_payload(fleet_, "right"))[1],
            {"id": 1, "fx": 9, "col": [[255, 136, 0]], "sx": 50},
        )
        self.assertIn("Applied look to far-left, middle-right", text)

    def test_rejects_unknown_channel(self):
        fleet_ = make_fleet()
        with self.assertRaises(ValueError) as ctx:
            self.call(fleet_, {"assignments": [{"channel": "ceiling", "fx": 9}]})
        self.assertIn("Unknown channel", str(ctx.exception))
        self.assertIn("far-left", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            self.call(fleet_, {"channels": ["ceiling"], "fx": 9})
        self.assertIn("Unknown channel", str(ctx.exception))

    def test_rejects_invalid_colors(self):
        bad_colors = [
            [],
            [[256, 0, 0]],
            [[-1, 0, 0]],
            [[1.5, 2, 3]],
            [[1, 2]],  # not an RGB triplet
            [[1, 2, 3, 4]],  # RGBW is only accepted as a hex string here
            "ZZZZZZ",
            "FF88",
            12,
            [[0, 0, 0]] * 9,  # more than eight stops
            ["FF0000", 12],
        ]
        for colors in bad_colors:
            for arguments in (
                {"assignments": [{"channel": "far-left", "colors": colors}]},
                {"channels": ["far-left"], "fx": 9, "colors": colors},
            ):
                with self.subTest(colors=colors, arguments=list(arguments)):
                    with self.assertRaises(ValueError):
                        self.call(make_fleet(), arguments)

    def test_rejects_bad_bri_and_on(self):
        for bri in (256, -1, 120.0, "120", True):
            with self.subTest(bri=bri):
                with self.assertRaises(ValueError):
                    self.call(
                        make_fleet(),
                        {"assignments": [{"channel": "far-left", "bri": bri}]},
                    )
        for on in ("yes", 1, 0):
            with self.subTest(on=on):
                with self.assertRaises(ValueError):
                    self.call(
                        make_fleet(),
                        {"assignments": [{"channel": "far-left", "on": on}]},
                    )

    def test_rejects_unknown_effect_from_live_catalog(self):
        self.seed_catalog()
        fleet_ = make_fleet()
        with self.assertRaises(ValueError) as ctx:
            self.call(
                fleet_, {"assignments": [{"channel": "far-left", "fx": 99}]}
            )
        self.assertIn("not usable on the target", str(ctx.exception))
        # Validation happens before any POST.
        self.assertEqual(fleet_.clients["left"].payloads, [])

    def test_rejects_missing_channels_and_assignments(self):
        with self.assertRaises(ValueError) as ctx:
            self.call(make_fleet(), {})
        self.assertIn("requires channels or assignments", str(ctx.exception))

    def test_assignments_must_be_objects(self):
        with self.assertRaises(ValueError) as ctx:
            self.call(make_fleet(), {"assignments": ["far-left"]})
        self.assertIn("must be an object", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
