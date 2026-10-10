"""Tests for the FireTV ADB bridge (firetv.py) and its MCP tools.

Written against the firetv.py contract:
load_firetv_config(cfg=None) / is_enabled(cfg=None) / connect() /
status() -> {'enabled', 'connected', 'awake', 'foreground_app'} /
wake() / sleep() / open_url(url) / keyevent(code), with every action
gated behind the config 'firetv.enabled' switch
(ValueError('FireTV control is disabled in settings') when off), and the
mcp_light tools tv_status, tv_wake, tv_sleep, tv_open_url.

All adb traffic is faked by patching firetv.subprocess.run,
so no real TV or adb binary is needed.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

import firetv
import lightctl
import light_gui
import mcp_light

DEFAULT_HOST = "10.27.27.207:5555"
DISABLED_MESSAGE = "FireTV control is disabled in settings"

POWER_AWAKE = """
Power Manager State:
  mWakefulness=Awake
  mWakefulnessChanging=false
Display Power: state=ON
"""

POWER_ASLEEP = """
Power Manager State:
  mWakefulness=Asleep
Display Power: state=OFF
"""

FOCUS = """
mCurrentFocus=Window{1a2b3c4 u0 com.amazon.avod.tv.client/.MainActivity}
mFocusedApp=AppWindowToken{9z8y7x6 token=Token{1a2b3c4 ActivityRecord{5d6e7f8 u0 com.amazon.avod.tv.client/.MainActivity t7}}}
mResumedActivity: ActivityRecord{1a2b3c4 u0 com.amazon.avod.tv.client/.MainActivity t7}
"""

AWAKE_DUMP = POWER_AWAKE + FOCUS
ASLEEP_DUMP = POWER_ASLEEP + FOCUS

FOREGROUND_PACKAGE = "com.amazon.avod.tv.client"


class AdbFake:
    """Dispatch fake standing in for subprocess.run adb calls."""

    def __init__(self, dump: str = AWAKE_DUMP):
        self.dump = dump
        self.calls: list[tuple[list[str], dict]] = []

    def run(self, argv, *args, **kwargs):
        self.calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=self._output(argv), stderr="")

    def _output(self, argv) -> str:
        if "dumpsys" in [str(arg) for arg in argv]:
            return self.dump
        return ""

    @property
    def flat_args(self) -> list[str]:
        return [str(arg) for argv, _ in self.calls for arg in argv]

    def matching(self, *needles: str) -> list[list[str]]:
        return [
            [str(arg) for arg in argv]
            for argv, _ in self.calls
            if all(needle in [str(arg) for arg in argv] for needle in needles)
        ]


class FireTVTestCase(unittest.TestCase):
    enabled_config = {"enabled": True, "host": DEFAULT_HOST}
    disabled_config = {"enabled": False, "host": DEFAULT_HOST}

    def setUp(self):
        self.adb = AdbFake()
        self.run_patch = patch("firetv.subprocess.run", side_effect=self.adb.run)
        self.run_mock = self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def enable(self):
        return patch("firetv.load_firetv_config", return_value=dict(self.enabled_config))

    def disable(self):
        return patch("firetv.load_firetv_config", return_value=dict(self.disabled_config))

    def assert_timeouts_used(self):
        self.assertTrue(self.adb.calls, "expected at least one adb call")
        for argv, kwargs in self.adb.calls:
            self.assertIn("timeout", kwargs, f"missing timeout for {argv}")
            self.assertGreater(kwargs["timeout"], 0)


class ConfigTests(FireTVTestCase):
    def test_defaults_apply_when_no_firetv_section(self):
        config = firetv.load_firetv_config({})

        self.assertEqual(config["host"], DEFAULT_HOST)
        self.assertIs(config["enabled"], False)

    def test_partial_override_merges_with_defaults(self):
        config = firetv.load_firetv_config({"firetv": {"enabled": True}})

        self.assertIs(config["enabled"], True)
        self.assertEqual(config["host"], DEFAULT_HOST)

    def test_explicit_host_overrides_default(self):
        config = firetv.load_firetv_config(
            {"firetv": {"enabled": True, "host": "10.0.0.9:5555"}}
        )

        self.assertEqual(config["host"], "10.0.0.9:5555")
        self.assertIs(config["enabled"], True)

    def test_config_file_is_loaded_when_cfg_omitted(self):
        with patch("lightctl.load_config", return_value={"firetv": {"enabled": True}}):
            self.assertTrue(firetv.is_enabled())

    def test_is_enabled_reflects_config(self):
        self.assertTrue(firetv.is_enabled({"firetv": {"enabled": True}}))
        self.assertFalse(firetv.is_enabled({"firetv": {"enabled": False}}))
        self.assertFalse(firetv.is_enabled({"firetv": {}}))
        self.assertFalse(firetv.is_enabled({}))


class EnabledGatingTests(FireTVTestCase):
    def test_actions_raise_when_disabled(self):
        with self.disable():
            for call in (
                firetv.wake,
                firetv.sleep,
                firetv.play_pause,
                firetv.next_track,
                firetv.previous_track,
                firetv.volume_up,
                firetv.volume_down,
                firetv.mute,
                lambda: firetv.open_url("http://10.0.0.1:8123/tv"),
                lambda: firetv.keyevent(224),
            ):
                with self.assertRaises(ValueError) as caught:
                    call()
                self.assertEqual(str(caught.exception), DISABLED_MESSAGE)

        self.assertEqual(self.adb.calls, [])

    def test_status_reports_disabled_without_touching_adb(self):
        with self.disable():
            result = firetv.status()

        self.assertIs(result["enabled"], False)
        self.assertIs(result["connected"], False)
        self.assertEqual(self.adb.calls, [])


class StatusTests(FireTVTestCase):
    def test_awake_device_parses_wakefulness_and_foreground_app(self):
        with self.enable():
            result = firetv.status()

        self.assertIs(result["enabled"], True)
        self.assertIs(result["connected"], True)
        self.assertTrue(result["awake"])
        self.assertEqual(result["foreground_app"], FOREGROUND_PACKAGE)

    def test_asleep_device_reports_awake_false(self):
        self.adb.dump = ASLEEP_DUMP
        with self.enable():
            result = firetv.status()

        self.assertIs(result["connected"], True)
        self.assertFalse(result["awake"])

    def test_adb_failure_reports_connected_false(self):
        def unreachable(argv, *args, **kwargs):
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr=f"error: device '{DEFAULT_HOST}' not found"
            )

        self.run_mock.side_effect = unreachable
        with self.enable():
            result = firetv.status()

        self.assertIs(result["enabled"], True)
        self.assertIs(result["connected"], False)

    def test_status_calls_use_timeouts(self):
        with self.enable():
            firetv.status()

        self.assert_timeouts_used()


class ActionTests(FireTVTestCase):
    def test_connect_dials_the_configured_host(self):
        with self.enable():
            firetv.connect()

        connects = self.adb.matching("connect")
        self.assertTrue(connects, f"no adb connect call in {self.adb.calls}")
        self.assertIn(DEFAULT_HOST, self.adb.flat_args)

    def test_wake_sends_keyevent_224(self):
        with self.enable():
            firetv.wake()

        keyevents = self.adb.matching("keyevent")
        self.assertTrue(keyevents, f"no keyevent call in {self.adb.calls}")
        self.assertIn("224", keyevents[0])

    def test_sleep_sends_keyevent_223(self):
        with self.enable():
            firetv.sleep()

        keyevents = self.adb.matching("keyevent")
        self.assertTrue(keyevents, f"no keyevent call in {self.adb.calls}")
        self.assertIn("223", keyevents[0])

    def test_media_controls_send_safe_named_keyevents(self):
        cases = (
            (firetv.play_pause, "85"),
            (firetv.next_track, "87"),
            (firetv.previous_track, "88"),
            (firetv.volume_up, "24"),
            (firetv.volume_down, "25"),
            (firetv.mute, "164"),
        )
        for method, code in cases:
            with self.subTest(method=method.__name__):
                self.adb.calls.clear()
                with self.enable():
                    method()
                keyevents = self.adb.matching("keyevent")
                self.assertTrue(keyevents, f"no keyevent call in {self.adb.calls}")
                self.assertIn(code, keyevents[0])

    def test_keyevent_passes_code_through(self):
        with self.enable():
            firetv.keyevent(4)

        keyevents = self.adb.matching("keyevent")
        self.assertTrue(keyevents, f"no keyevent call in {self.adb.calls}")
        self.assertIn("4", keyevents[0])

    def test_open_url_starts_view_intent_with_url(self):
        url = "http://10.27.27.20:8123/tv"
        with self.enable():
            firetv.open_url(url)

        intents = self.adb.matching("am", "start")
        self.assertTrue(intents, f"no am-start call in {self.adb.calls}")
        intent = intents[0]
        self.assertIn("-a", intent)
        self.assertIn("android.intent.action.VIEW", intent)
        self.assertIn("-d", intent)
        self.assertIn(url, intent)

    def test_actions_use_timeouts(self):
        with self.enable():
            firetv.wake()
            firetv.sleep()
            firetv.open_url("http://10.27.27.20:8123/tv")

        self.assert_timeouts_used()

    def test_connect_raises_when_adb_reports_failure_on_stdout(self):
        def fail(argv, *args, **kwargs):
            self.adb.calls.append((list(argv), kwargs))
            return subprocess.CompletedProcess(
                argv, 0, stdout=f"unable to connect to {DEFAULT_HOST}", stderr=""
            )

        self.run_mock.side_effect = fail
        with self.enable():
            with self.assertRaises(RuntimeError) as caught:
                firetv.connect()
        self.assertIn("unable to connect", str(caught.exception))

    def test_no_devices_error_triggers_reconnect_retry(self):
        attempts = {"shells": 0}

        def flaky(argv, *args, **kwargs):
            self.adb.calls.append((list(argv), kwargs))
            args_str = [str(arg) for arg in argv]
            if "dumpsys" in args_str:
                attempts["shells"] += 1
                if attempts["shells"] == 1:
                    return subprocess.CompletedProcess(
                        argv, 1, stdout="", stderr="error: no devices/emulators found"
                    )
                return subprocess.CompletedProcess(argv, 0, stdout=self.adb.dump, stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        self.run_mock.side_effect = flaky
        with self.enable():
            result = firetv.status()

        self.assertIs(result["connected"], True)
        self.assertTrue(self.adb.matching("connect"), f"no reconnect in {self.adb.calls}")


class GuiFireTVActionTests(unittest.TestCase):
    def test_media_actions_route_to_named_firetv_methods(self):
        mappings = {
            "previous": "previous_track",
            "play_pause": "play_pause",
            "next": "next_track",
            "volume_down": "volume_down",
            "mute": "mute",
            "volume_up": "volume_up",
        }
        for action, method_name in mappings.items():
            with self.subTest(action=action), patch(f"firetv.{method_name}", return_value="") as method:
                result = light_gui.run_firetv_action({"action": action})
            method.assert_called_once_with()
            self.assertTrue(result["ok"])

    def test_unknown_firetv_action_message_lists_media_actions(self):
        with self.assertRaises(ValueError) as caught:
            light_gui.run_firetv_action({"action": "rewind"})
        message = str(caught.exception)
        self.assertIn("play_pause", message)
        self.assertIn("volume_up", message)


class FakeModes:
    def start(self) -> str:
        return "started"

    def stop(self) -> str:
        return "stopped"


class McpToolTests(FireTVTestCase):
    def setUp(self):
        super().setUp()
        self.client = lightctl.LightClient("10.27.27.110", dry_run=True)

    def test_firetv_tools_are_registered(self):
        tools = {tool["name"]: tool for tool in mcp_light.build_tools()}

        for name in (
            "tv_status", "tv_wake", "tv_sleep", "tv_open_url",
            "tv_play_pause", "tv_next", "tv_previous", "tv_volume_up",
            "tv_volume_down", "tv_mute",
        ):
            self.assertIn(name, tools)

        schema = tools["tv_open_url"]["inputSchema"]
        self.assertIn("url", schema["properties"])
        self.assertIn("url", schema.get("required", []))

    def test_tv_wake_calls_through(self):
        with patch("firetv.wake") as wake:
            result = mcp_light.call_tool(self.client, "tv_wake", {}, FakeModes())

        wake.assert_called_once_with()
        self.assertIn("content", result)

    def test_tv_sleep_calls_through(self):
        with patch("firetv.sleep") as sleep:
            result = mcp_light.call_tool(self.client, "tv_sleep", {}, FakeModes())

        sleep.assert_called_once_with()
        self.assertIn("content", result)

    def test_tv_open_url_passes_the_url(self):
        url = "http://10.27.27.20:8123/tv"
        with patch("firetv.open_url") as open_url:
            result = mcp_light.call_tool(self.client, "tv_open_url", {"url": url}, FakeModes())

        open_url.assert_called_once()
        args, kwargs = open_url.call_args
        self.assertTrue(url in args or kwargs.get("url") == url)
        self.assertIn("content", result)

    def test_tv_status_returns_status_dict(self):
        status = {
            "enabled": True,
            "connected": True,
            "awake": True,
            "foreground_app": FOREGROUND_PACKAGE,
        }
        with patch("firetv.status", return_value=status):
            result = mcp_light.call_tool(self.client, "tv_status", {}, FakeModes())

        text = result["content"][0]["text"]
        self.assertIn("connected", text)
        self.assertIn(FOREGROUND_PACKAGE, text)

    def test_tv_media_tools_call_through(self):
        cases = {
            "tv_play_pause": "play_pause",
            "tv_next": "next_track",
            "tv_previous": "previous_track",
            "tv_volume_up": "volume_up",
            "tv_volume_down": "volume_down",
            "tv_mute": "mute",
        }
        for tool_name, method_name in cases.items():
            with self.subTest(tool=tool_name), patch(f"firetv.{method_name}") as method:
                result = mcp_light.call_tool(self.client, tool_name, {}, FakeModes())
            method.assert_called_once_with()
            self.assertIn("content", result)

    def test_disabled_error_surfaces_to_the_caller(self):
        with patch("firetv.open_url", side_effect=ValueError(DISABLED_MESSAGE)):
            try:
                result = mcp_light.call_tool(
                    self.client, "tv_open_url", {"url": "http://10.0.0.1:8123/tv"}, FakeModes()
                )
            except ValueError as exc:
                self.assertIn("disabled", str(exc))
            else:
                self.assertIn("disabled", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
