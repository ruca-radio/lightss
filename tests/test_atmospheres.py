"""Tests for atmospheres.py — effect classification, catalog text, curated looks."""

from __future__ import annotations

import unittest

import atmospheres
import fleet
import lightctl


class RecordingClient(lightctl.LightClient):
    def __init__(self, host: str):
        super().__init__(host, dry_run=True)
        self.payloads: list[dict] = []

    def post_state(self, payload, transition_ms: int = 0):
        self.payloads.append(payload)
        return None


def make_fleet() -> fleet.LightFleet:
    controllers = [
        fleet.ControllerConfig("right", "http://10.27.27.110", {0: "far-right", 1: "middle-right"}),
        fleet.ControllerConfig("left", "http://10.27.27.112", {0: "middle-left", 1: "far-left"}),
    ]
    clients = {c.name: RecordingClient(c.host) for c in controllers}
    return fleet.LightFleet(clients, controllers)


EFFECTS = ["Solid", "Blink", "Breathe", "Fire 2012", "Noisemeter", "Matrix", "RSVD"]
FXDATA = [
    "",
    "",
    "",
    "",
    "!;!;!;1v",   # volume-reactive
    "!;!;!;2",    # requires 2D
    "",
]


class ClassifyTests(unittest.TestCase):
    def test_vibes_and_flags(self):
        classified = atmospheres.classify_effects(EFFECTS, FXDATA)
        self.assertIn("Fire & heat", classified[3]["vibes"])
        self.assertTrue(classified[4]["audio"])
        self.assertTrue(classified[5]["2d"])

    def test_unsafe_marks_strobe_family_and_placeholders(self):
        classified = atmospheres.classify_effects(EFFECTS, FXDATA)
        self.assertFalse(classified[1]["unsafe"])  # Blink remains available
        self.assertTrue(classified[6]["unsafe"])   # RSVD
        self.assertFalse(classified[2]["unsafe"])  # Breathe
        self.assertFalse(classified[3]["unsafe"])  # Fire 2012

    def test_effect_policy_blocks_strobes_but_preserves_blink_and_particles(self):
        names = ["Strobe", "Strobe Mega", "Blink Rainbow", "Chase Flash",
                 "Lightning", "Fireworks 1D", "Sparkle+", "PS Sparkler"]
        classified = atmospheres.classify_effects(names)
        self.assertTrue(all(classified[i]["unsafe"] for i in (0,1,3,4)))
        self.assertTrue(all(not classified[i]["unsafe"] for i in (2,5,6,7)))

    def test_1d_compatible_effect_with_both_flags_is_not_matrix_only(self):
        classified = atmospheres.classify_effects(
            ["Fireworks 1D", "Matrix"], ["!;!;!;12", "!;!;!;2"])
        self.assertFalse(classified[0]["2d"])
        self.assertTrue(classified[1]["2d"])


class CatalogTextTests(unittest.TestCase):
    def test_groups_markers_and_header(self):
        text = atmospheres.catalog_text(EFFECTS, FXDATA)
        self.assertIn("Effect catalog", text)
        self.assertIn("Fire & heat", text)
        self.assertIn("3=Fire 2012", text)
        self.assertIn("4=Noisemeter♪", text)
        self.assertIn("5=Matrix[2D]", text)
        self.assertIn("1=Blink◉", text)


class AtmosphereTests(unittest.TestCase):
    def test_names_sorted_and_nonempty(self):
        names = atmospheres.atmosphere_names()
        self.assertEqual(names, sorted(names))
        self.assertGreater(len(names), 8)

    def test_menu_text_describes_each(self):
        menu = atmospheres.atmosphere_menu_text()
        for name in atmospheres.atmosphere_names():
            self.assertIn(name, menu)

    def test_apply_routes_to_both_controllers(self):
        fleet_ = make_fleet()
        result = atmospheres.apply_atmosphere(fleet_, "fireplace")
        self.assertEqual(set(result), {"right", "left"})
        for name in ("right", "left"):
            payloads = fleet_.clients[name].payloads
            self.assertEqual(len(payloads), 1)
            fx_ids = {seg["fx"] for seg in payloads[0]["seg"]}
            self.assertEqual(fx_ids, {66})
            pal_ids = {seg["pal"] for seg in payloads[0]["seg"]}
            self.assertEqual(pal_ids, {35})

    def test_apply_is_case_insensitive(self):
        fleet_ = make_fleet()
        atmospheres.apply_atmosphere(fleet_, "  Ocean ")
        self.assertTrue(fleet_.clients["left"].payloads)

    def test_unknown_atmosphere_lists_valid_names(self):
        fleet_ = make_fleet()
        with self.assertRaises(ValueError) as ctx:
            atmospheres.apply_atmosphere(fleet_, "mordor")
        self.assertIn("fireplace", str(ctx.exception))

    def test_all_curated_effect_ids_exist_on_wled_16(self):
        # WLED 16.0.1 ships 220 effects / 72 palettes; curated looks must fit.
        for name, spec in atmospheres.ATMOSPHERES.items():
            for _func, kwargs in spec["steps"]:
                for key in ("fx", "fx_left", "fx_right"):
                    if key in kwargs:
                        self.assertGreaterEqual(kwargs[key], 0, name)
                        self.assertLess(kwargs[key], 220, name)
                for key in ("pal", "pal_left", "pal_right"):
                    if key in kwargs:
                        self.assertGreaterEqual(kwargs[key], 0, name)
                        self.assertLess(kwargs[key], 72, name)

    def test_no_curated_atmosphere_uses_unsafe_effects(self):
        classified = atmospheres.classify_effects
        # Build a fake full catalog large enough for all curated ids.
        max_fx = max(
            kwargs[key]
            for spec in atmospheres.ATMOSPHERES.values()
            for _f, kwargs in spec["steps"]
            for key in ("fx", "fx_left", "fx_right")
            if key in kwargs
        )
        names = ["Solid"] * (max_fx + 1)
        # Mark the curated ids with realistic names where we know them.
        names[66] = "Fire 2012"
        names[43] = "Rain"
        names[96] = "Drip"
        info = classified(names)
        for name, spec in atmospheres.ATMOSPHERES.items():
            for _func, kwargs in spec["steps"]:
                for key in ("fx", "fx_left", "fx_right"):
                    if key in kwargs:
                        self.assertFalse(info[kwargs[key]]["unsafe"], f"{name}:{key}")


if __name__ == "__main__":
    unittest.main()
