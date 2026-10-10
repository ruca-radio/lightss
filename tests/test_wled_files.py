"""Custom WLED palettes (firmware 16+) authored by the AI.

WLED 16 keeps custom palettes in ``paletteN.json`` on the device filesystem and
reloads them the moment such a file is uploaded (``wled_server.cpp``:
``handleUpload`` -> ``loadCustomPalettes()``), so an AI-authored palette is live
without a reboot. These tests run against a fake WLED 16 device: nothing here
touches real hardware.
"""

from __future__ import annotations

import json
import re
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import lightctl
import mcp_light
import wled_files


class _Wled16Device(BaseHTTPRequestHandler):
    """Minimal WLED 16 file manager + info endpoint."""

    files: dict[str, str] = {}
    version = "0.16.1"
    cpalmax = 129
    uploads: list[tuple[str, str]] = []
    deletes: list[str] = []

    def log_message(self, *args):  # keep test output clean
        return

    def _send(self, status: int, body: bytes = b"", content_type: str | None = None):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, payload):
        self._send(200, json.dumps(payload).encode(), "application/json")

    def do_GET(self):
        if self.path.startswith("/json/info"):
            return self._json(
                {
                    "ver": self.version,
                    "cpalmax": self.cpalmax,
                    "cpalcount": len(self.files),
                    "palcount": 75 + len(self.files),
                }
            )
        match = re.match(r"/edit\?func=edit&path=(.*)", self.path)
        if match:
            name = match.group(1).lstrip("/")
            if name in self.files:
                return self._send(200, self.files[name].encode(), "text/plain")
            return self._send(404)
        match = re.match(r"/edit\?func=delete&path=(.*)", self.path)
        if match:
            name = match.group(1).lstrip("/")
            self.files.pop(name, None)
            self.deletes.append(name)
            return self._send(200, b"Deleted")
        return self._send(404)

    def do_POST(self):
        if self.path != "/upload":
            return self._send(404)
        length = int(self.headers["Content-Length"])
        raw = self.rfile.read(length)
        head, _, rest = raw.partition(b"\r\n\r\n")
        self.assert_multipart(head)
        name = re.search(rb'filename="([^"]+)"', head).group(1).decode()
        content = rest.rsplit(b"\r\n--", 1)[0].decode()
        self.files[name] = content
        self.uploads.append((name, content))
        self._send(200, b"File Uploaded!")

    @staticmethod
    def assert_multipart(head: bytes):
        if b'name="file"' not in head:
            raise AssertionError(f"WLED expects a part named 'file': {head!r}")


class _DeviceFixture(unittest.TestCase):
    def setUp(self):
        _Wled16Device.files = {}
        _Wled16Device.uploads = []
        _Wled16Device.deletes = []
        _Wled16Device.version = "0.16.1"
        _Wled16Device.cpalmax = 129
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Wled16Device)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.host = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.client = lightctl.LightClient(self.host, dry_run=True)

    @property
    def files(self) -> dict[str, str]:
        return _Wled16Device.files


class _Modes:
    def start(self):
        return "started"

    def stop(self):
        return "stopped"


# ---------------------------------------------------------------------------
# Palette model
# ---------------------------------------------------------------------------

class PaletteModelTests(unittest.TestCase):
    def test_colors_are_spread_evenly_across_the_gradient(self):
        stops = wled_files.normalize_stops(["#ff0000", "#00ff00", "#0000ff"])
        self.assertEqual([stop[0] for stop in stops], [0, 128, 255])

    def test_explicit_stops_are_kept_and_sorted(self):
        stops = wled_files.normalize_stops([[255, 87, 255, 65], [0, 255, 33, 4]])
        self.assertEqual(stops, [(0, 255, 33, 4), (255, 87, 255, 65)])

    def test_hex_and_rgb_inputs_agree(self):
        self.assertEqual(
            wled_files.normalize_stops(["#ff2104", "#2aff16"]),
            wled_files.normalize_stops([[255, 33, 4], [42, 255, 22]]),
        )

    def test_file_matches_the_documented_wled_format(self):
        body = wled_files.palette_file(
            [(0, 255, 33, 4), (43, 255, 68, 25), (255, 87, 255, 65)]
        )
        self.assertEqual(
            body,
            b'{"palette":[0,255,33,4,43,255,68,25,255,87,255,65]}',
        )
        self.assertEqual(
            wled_files.parse_palette_file(body),
            [(0, 255, 33, 4), (43, 255, 68, 25), (255, 87, 255, 65)],
        )

    def test_reads_both_on_disk_formats_the_firmware_accepts(self):
        # colors.cpp: loadCustomPalettes() takes [pos,"RRGGBB",...] (what the
        # WLED 16 UI writes) and [pos,r,g,b,...] (the documented API shape).
        hex_form = '{"palette":[0,"cfd143",25,"8b6e2a",255,"ffc6a1"]}'
        self.assertEqual(
            wled_files.parse_palette_file(hex_form),
            [(0, 207, 209, 67), (25, 139, 110, 42), (255, 255, 198, 161)],
        )
        int_form = '{"palette":[0,207,209,67,25,139,110,42,255,255,198,161]}'
        self.assertEqual(
            wled_files.parse_palette_file(int_form),
            wled_files.parse_palette_file(hex_form),
        )

    def test_unparseable_palette_files_raise(self):
        for bad in (
            "not json",
            '{"palette":[]}',
            '{"palette":[0,1,2]}',
            '{"palette":[0,"zzzzzz",255,"ffc6a1"]}',
            '{"palette":[0,207,209,67,25,139,110]}',
        ):
            with self.assertRaises(ValueError, msg=f"{bad!r} should be rejected"):
                wled_files.parse_palette_file(bad)

    def test_invalid_palettes_are_rejected(self):
        for bad in (
            [],
            ["#ff0000"],
            [[0, 255, 0, 0], [0, 0, 0, 255]],          # duplicate position
            [[999, 255, 0, 0], [255, 0, 0, 255]],      # position out of range
            ["#ff0000", "#00ff00", "#0000ff", "#ffffff",
             "#111111", "#222222", "#333333", "#444444",
             "#555555", "#666666", "#777777", "#888888",
             "#999999", "#aaaaaa", "#bbbbbb", "#cccccc", "#dddddd"],  # 17 stops
            ["not-a-color", "#00ff00"],
        ):
            with self.assertRaises(ValueError, msg=f"{bad!r} should be rejected"):
                wled_files.normalize_stops(bad)

    def test_palette_ids_follow_the_firmware_numbering(self):
        # 16.0+: custom palettes count down from 200 into the 72..200 range.
        self.assertEqual(wled_files.palette_id_for_slot(0, "0.16.1"), 200)
        self.assertEqual(wled_files.palette_id_for_slot(1, "0.16.0"), 199)
        self.assertEqual(wled_files.palette_id_for_slot(128, "0.16.1"), 72)
        with self.assertRaises(ValueError):
            wled_files.palette_id_for_slot(129, "0.16.1")
        # Before 16.0 they counted down from 255.
        self.assertEqual(wled_files.palette_id_for_slot(0, "0.15.1"), 255)
        self.assertEqual(wled_files.palette_id_for_slot(1, "0.14.0"), 254)


# ---------------------------------------------------------------------------
# WLED file transport
# ---------------------------------------------------------------------------

class WledFileTransportTests(_DeviceFixture):
    def test_upload_posts_multipart_to_the_upload_route(self):
        wled_files.upload_file(self.host, "palette0.json", b'{"palette":[0,1,2,3]}')
        self.assertEqual(list(self.files), ["palette0.json"])
        self.assertEqual(self.files["palette0.json"], '{"palette":[0,1,2,3]}')

    def test_missing_file_reads_as_empty_not_an_error(self):
        self.assertFalse(wled_files.file_exists(self.host, "palette9.json"))
        self.assertEqual(wled_files.read_file(self.host, "palette9.json"), "")

    def test_delete_removes_the_file(self):
        wled_files.upload_file(self.host, "palette0.json", b"{}")
        wled_files.delete_file(self.host, "palette0.json")
        self.assertEqual(self.files, {})
        self.assertEqual(_Wled16Device.deletes, ["palette0.json"])

    def test_next_free_slot_skips_occupied_slots(self):
        wled_files.upload_file(self.host, "palette0.json", b"{}")
        wled_files.upload_file(self.host, "palette1.json", b"{}")
        self.assertEqual(wled_files.next_free_slot(self.host, reported_count=2), 2)
        # A stale count still cannot collide with an existing file.
        self.assertEqual(wled_files.next_free_slot(self.host, reported_count=0), 2)


# ---------------------------------------------------------------------------
# The AI tool
# ---------------------------------------------------------------------------

class DesignPaletteToolTests(_DeviceFixture):
    def _call(self, args):
        result = mcp_light.call_tool(self.client, "design_palette", args, _Modes())
        return json.loads(result["content"][0]["text"])

    def test_create_writes_a_palette_the_device_can_use(self):
        result = self._call(
            {"colors": ["#ff2104", "#ff4419", "#ff0719", "#ff5267", "#fffff2"]}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["palette_ids"], [200])
        self.assertEqual(list(self.files), ["palette0.json"])
        stops = wled_files.parse_palette_file(self.files["palette0.json"])
        self.assertEqual(stops[0], (0, 255, 33, 4))
        self.assertEqual(stops[-1], (255, 255, 255, 242))

    def test_repeated_creation_uses_the_next_slot_and_id(self):
        first = self._call({"colors": ["#ff0000", "#0000ff"]})
        second = self._call({"colors": ["#00ff00", "#ffff00"]})
        self.assertEqual(first["palette_ids"], [200])
        self.assertEqual(second["palette_ids"], [199])
        self.assertEqual(sorted(self.files), ["palette0.json", "palette1.json"])

    def test_apply_switches_the_segment_onto_the_new_palette(self):
        with mock.patch.object(mcp_light, "_post_state", return_value={"ok": True}) as post:
            result = self._call({"colors": ["#ff0000", "#0000ff"], "apply": True})
        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["applied"])
        payload = post.call_args[0][1]
        self.assertEqual(payload, {"seg": [{"pal": 200}]})

    def test_list_reports_slots_and_reads_back_the_stops(self):
        self._call({"colors": ["#ff0000", "#0000ff"]})
        result = self._call({"action": "list"})
        entry = result["controllers"][""]
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["version"], "0.16.1")
        self.assertEqual(entry["custom_palettes_max"], 129)
        self.assertEqual(entry["palettes"][0]["palette_id"], 200)
        self.assertEqual(entry["palettes"][0]["stops"][0], [0, 255, 0, 0])

    def test_list_reports_a_palette_file_it_cannot_parse(self):
        _Wled16Device.files["palette0.json"] = '{"palette":[0,"nothex",255,"ffffff"]}'
        entry = self._call({"action": "list"})["controllers"][""]
        self.assertTrue(entry["ok"])
        self.assertIn("error", entry["palettes"][0])
        self.assertNotIn("stops", entry["palettes"][0])

    def test_list_reads_the_hex_format_the_device_ui_writes(self):
        _Wled16Device.files["palette0.json"] = '{"palette":[0,"ff0000",255,"0000ff"]}'
        entry = self._call({"action": "list"})["controllers"][""]
        self.assertEqual(entry["palettes"][0]["stops"], [[0, 255, 0, 0], [255, 0, 0, 255]])

    def test_delete_uses_rmcpal_so_the_device_reloads_its_palettes(self):
        """The docs call rmcpal a boolean; the firmware reads it as a slot.

        json.cpp: rmcpal removes /palette<slot>.json and calls
        loadCustomPalettes(). Deleting the file through /edit alone leaves the
        device serving a palette that is gone from disk.
        """
        self._call({"colors": ["#ff0000", "#0000ff"]})
        with mock.patch.object(mcp_light, "_post_state", return_value={"ok": True}) as post:
            result = self._call({"action": "delete", "slot": 0})
        self.assertTrue(result["controllers"][""]["ok"])
        self.assertEqual(post.call_args[0][1], {"rmcpal": 0})
        # No raw file delete is used: it would not reload the device.
        self.assertEqual(_Wled16Device.deletes, [])

    def test_delete_reports_a_device_that_refuses_rmcpal(self):
        self._call({"colors": ["#ff0000", "#0000ff"]})
        with mock.patch.object(mcp_light, "_post_state", side_effect=RuntimeError("offline")):
            result = self._call({"action": "delete", "slot": 0})
        self.assertFalse(result["controllers"][""]["ok"])
        self.assertIn("offline", result["controllers"][""]["error"])

    def test_delete_requires_a_slot(self):
        with self.assertRaises(ValueError):
            self._call({"action": "delete"})

    def test_bad_input_is_rejected_before_anything_is_written(self):
        for args in (
            {"colors": []},
            {"colors": ["#ff0000"]},
            {"colors": ["nope", "#00ff00"]},
            {"action": "explode"},
        ):
            with self.assertRaises(ValueError, msg=f"{args} should be rejected"):
                self._call(args)
        self.assertEqual(self.files, {})
        self.assertEqual(_Wled16Device.uploads, [])

    def test_explicit_stops_are_placed_exactly(self):
        result = self._call({"stops": [[0, 255, 0, 0], [64, 0, 0, 255], [255, 255, 255, 255]]})
        self.assertTrue(result["ok"])
        stops = wled_files.parse_palette_file(self.files["palette0.json"])
        self.assertEqual([stop[0] for stop in stops], [0, 64, 255])

    def test_pre_16_firmware_gets_a_pre_16_palette_id(self):
        _Wled16Device.version = "0.15.1"
        result = self._call({"colors": ["#ff0000", "#0000ff"]})
        self.assertEqual(result["palette_ids"], [255])
