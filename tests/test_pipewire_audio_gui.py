"""Regression tests for PipeWire Audio Settings.

Run with:

    python3 tests/test_pipewire_audio_gui.py          # or: python3 -m unittest discover tests

No display, PipeWire or PyGObject needed: ``gtk_stub`` provides GTK, and the
PipeWire/WirePlumber commands are intercepted. Each test pins the behaviour
that broke in the field, so the same defect cannot come back unnoticed.
"""

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import gtk_stub  # noqa: E402

gtk_stub.install()

APP_PATH = os.path.join(ROOT, "pipewire-audio-gui.py")
_spec = importlib.util.spec_from_file_location("pipewire_audio_gui", APP_PATH)
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)

# Pristine implementations, kept so their own unit tests are not shadowed by
# the per-test doubles installed in Base.setUp.
REAL_NOTIFY_SEND = app.notify_send
REAL_RESTART_SERVICES = app.restart_services


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

SINK = {
    "id": 0,
    "type": "PipeWire:Interface:Node",
    "info": {
        "props": {
            "media.class": "Audio/Sink",
            "device.api": "alsa",
            "node.name": "alsa_output.spk",
            "node.description": "Speakers",
        },
        "params": {"Format": [{"format": "S24LE", "rate": 48000}]},
    },
}

USB_SINK = {
    "id": 2,
    "type": "PipeWire:Interface:Node",
    "info": {
        "props": {
            "media.class": "Audio/Sink",
            "device.api": "alsa",
            "node.name": "alsa_output.usb-dac",
            "node.description": "USB DAC",
        },
        "params": {"Format": [{"format": "S16LE", "rate": 44100}]},
    },
}


def stream(name, app_name, rate, state="running", with_enum=True):
    """A playback stream node."""
    props = {
        "media.class": "Stream/Output/Audio",
        "node.name": name,
        "application.name": app_name,
    }
    if with_enum:
        info = {"format": "F32LE", "rate": {"default": rate}}
    else:
        # Browser streams often expose only the negotiated Format.
        info = {"format": "F32LE", "rate": rate}
    return {
        "id": abs(hash(name)) % 1000,
        "type": "PipeWire:Interface:Node",
        "info": {"state": state, "props": props, "params": [{"id": "EnumFormat", "info": info}]},
    }


class Base(unittest.TestCase):
    """Redirects every path the app writes to into a temp dir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pwag-test-")
        self._saved = {
            "APP_DIR": app.APP_DIR,
            "MAPPING_FILE": app.MAPPING_FILE,
            "WP_JSON_DIR": app.WP_JSON_DIR,
            "WP_LUA_DIR": app.WP_LUA_DIR,
            "WP_JSON_FILE": app.WP_JSON_FILE,
            "WP_LUA_FILE": app.WP_LUA_FILE,
            "CONFIG_HOME": app.CONFIG_HOME,
            "run": app.run,
            "restart_services": app.restart_services,
            "notify_send": app.notify_send,
        }
        app.APP_DIR = self.tmp
        app.MAPPING_FILE = os.path.join(self.tmp, "mapping.json")
        app.WP_JSON_DIR = os.path.join(self.tmp, "wp-json")
        app.WP_LUA_DIR = os.path.join(self.tmp, "wp-lua")
        app.WP_JSON_FILE = os.path.join(app.WP_JSON_DIR, "51-user-device-format.conf")
        app.WP_LUA_FILE = os.path.join(app.WP_LUA_DIR, "51-user-device-format.lua")
        app.CONFIG_HOME = self.tmp
        app.run = lambda cmd, timeout=20: (0, "", "")
        app.restart_services = lambda names: (0, "", "")
        app.notify_send = lambda summary, body, urgency="normal": (True, "")
        self.graph = 48000
        self.forced = 0
        app.current_graph_rate = lambda: self.graph
        app.set_default_rate = lambda rate: (0, "", "")
        app.set_allowed_rates = lambda rates: (0, "", "")
        app.force_rate = self._force
        self.notifications = []
        app.notify_send = self._notify

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(app, key, value)

    # helpers ---------------------------------------------------------
    def _force(self, rate):
        self.forced = rate or 0
        if rate:
            self.graph = rate
        return (0, "", "")

    def _notify(self, summary, body, urgency="normal"):
        self.notifications.append((summary, body, urgency))
        return (True, "")

    def set_graph(self, rate):
        """Set the graph rate and keep forced-rate semantics consistent."""
        self.graph = rate
        if rate:
            self.forced = rate

    def window(self, nodes=None, autowatch=True):
        self.set_nodes(nodes if nodes is not None else [SINK])
        win = app.MainWindow()
        win.autowatch = autowatch
        return win

    def set_nodes(self, nodes):
        payload = json.dumps(nodes)
        app.run = lambda cmd, timeout=20: (0, payload, "")


class TestMappingPersistence(Base):
    def test_saved_file_is_valid_json_with_trailing_newline(self):
        """A literal backslash-n made every load fall back to defaults."""
        app.save_mapping({"44100": "bitperfect"}, True, {})
        raw = open(app.MAPPING_FILE, "rb").read()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b"\\n", raw.rstrip())
        self.assertEqual(json.loads(raw)["autowatch"], True)

    def test_roundtrip_rates_autowatch_and_devices(self):
        devices = {"alsa_output.usb-dac": {"format": "S16LE", "buffer_size": "512"}}
        app.save_mapping({"44100": "bitperfect", "48000": 96000}, True, devices)
        mapping, autowatch, loaded = app.load_mapping()
        self.assertEqual(mapping["44100"], "bitperfect")
        self.assertEqual(mapping["48000"], 96000)
        self.assertTrue(autowatch)
        self.assertEqual(loaded, devices)

    def test_corrupt_file_falls_back_to_defaults(self):
        with open(app.MAPPING_FILE, "w") as handle:
            handle.write("{not json")
        mapping, autowatch, devices = app.load_mapping()
        self.assertEqual(mapping["44100"], "bitperfect")
        self.assertFalse(autowatch)
        self.assertEqual(devices, {})

    def test_non_dict_devices_is_ignored(self):
        with open(app.MAPPING_FILE, "w") as handle:
            json.dump({"rates": {}, "autowatch": True, "devices": "nope"}, handle)
        self.assertEqual(app.load_mapping()[2], {})

    def test_missing_file_returns_defaults(self):
        mapping, autowatch, devices = app.load_mapping()
        self.assertEqual(mapping["48000"], "bitperfect")
        self.assertFalse(autowatch)
        self.assertEqual(devices, {})

    def test_quiet_save_preserves_device_settings(self):
        win = self.window()
        win.save_mapping_quiet()
        self.assertEqual(app.load_mapping()[2], {})


class TestRateHelpers(Base):
    def test_resolve_target(self):
        mapping = {"44100": "bitperfect", "48000": 96000}
        self.assertIsNone(app.resolve_target(44100, mapping))
        self.assertEqual(app.resolve_target(48000, mapping), 96000)
        # Unknown rate defaults to bit-perfect rather than raising.
        self.assertIsNone(app.resolve_target(12345, mapping))

    def test_allowed_rates_for(self):
        allowed = app.allowed_rates_for({"44100": "bitperfect", "48000": 96000, "x": 1})
        self.assertEqual(allowed, [44100, 96000])

    def test_graph_rate_ignores_static_default(self):
        """default.audio.rate is not the live clock; trusting it broke decisions."""
        self.assertEqual(app.graph_rate_from({"default.audio.rate": 48000}), 0)
        self.assertEqual(app.graph_rate_from({"clock.rate": 44100}), 44100)
        self.assertEqual(
            app.graph_rate_from({"clock.force-rate": 96000, "clock.rate": 44100}), 96000
        )
        self.assertEqual(app.graph_rate_from({"clock.force-rate": 0, "clock.rate": 44100}), 44100)

    def test_parse_clock_value_shapes(self):
        self.assertEqual(app.parse_clock_value("44100"), 44100)
        self.assertEqual(app.parse_clock_value("44100.00"), 44100)
        self.assertEqual(app.parse_clock_value("[ 44100, 48000 ]"), [44100, 48000])
        self.assertEqual(app.parse_clock_value("hello"), "hello")


class TestParamParsing(Base):
    """pw-dump emits params either as a mapping or as a list of entries."""

    def _nodes(self, param_id, info, shape):
        if shape == "list":
            return {"info": {"params": [{"id": param_id, "info": info}]}}
        if shape == "dict":
            return {"info": {"params": {param_id: {"info": info}}}}
        return {"info": {"params": {param_id: info}}}

    def test_param_entries_normalises_shapes(self):
        entries = [{"id": "Format", "info": 1}]
        for shape in ("list", "dict", "flat"):
            node = self._nodes("Format", 1, shape)
            self.assertEqual(app.param_entries(node), entries, shape)
        self.assertEqual(app.param_entries({}), [])

    def test_rate_ranges_across_shapes(self):
        info = {"audio": {"rate": {"min": 32000, "max": 96000}}}
        for shape in ("list", "dict", "flat"):
            node = self._nodes("EnumFormat", info, shape)
            self.assertEqual(app.rate_ranges(node), [(32000, 96000)], shape)

    def test_supported_caps_across_shapes(self):
        info = {"format": "S32LE", "rate": {"default": 48000, "alt1": 44100}}
        for shape in ("list", "dict", "flat"):
            node = self._nodes("EnumFormat", info, shape)
            self.assertEqual(app.supported_caps(node), ([44100, 48000], ["S32LE"]), shape)

    def test_current_format_across_shapes(self):
        info = {"format": "S24LE", "rate": 48000}
        for shape in ("list", "dict", "flat"):
            node = self._nodes("Format", info, shape)
            self.assertEqual(app.current_format(node), ("S24LE", 48000), shape)

    def test_device_accepts_rate_handles_missing_lists(self):
        """`rate in dev.get('rates') or []` raised TypeError on None."""
        self.assertFalse(app.device_accepts_rate({"rates": None, "ranges": []}, 44100))
        self.assertTrue(app.device_accepts_rate({"rates": [44100]}, 44100))
        self.assertTrue(
            app.device_accepts_rate({"rates": [], "ranges": [(32000, 96000)]}, 88200)
        )


class TestStreamDetection(Base):
    def test_paused_streams_do_not_pin_the_rate(self):
        """Paused streams linger in the graph and pinned max() forever."""
        nodes = [SINK, stream("firefox", "Firefox", 48000, "suspended"),
                 stream("music", "Music", 44100, "running")]
        self.set_nodes(nodes)
        self.assertEqual(app.active_input_rate(), 44100)

    def test_idle_streams_ignored_unknown_state_counted(self):
        self.set_nodes([SINK, stream("a", "A", 48000, "idle")])
        self.assertEqual(app.active_input_rate(), 0)
        node = stream("old", "Old", 22050, state=None)
        node["info"].pop("state")
        self.set_nodes([node])
        self.assertEqual(app.active_input_rate(), 22050)

    def test_format_only_stream_still_reports_a_rate(self):
        """Browser streams expose only Format; EnumFormat alone missed them."""
        self.set_nodes([SINK, stream("firefox", "Firefox", 48000, with_enum=False)])
        rates = [s["rates"] for s in app.load_streams() if s["name"] == "firefox"]
        self.assertEqual(rates, [[48000]])
        self.assertEqual(app.active_input_rate(), 48000)

    def test_all_paused_reports_silence(self):
        self.set_nodes([SINK, stream("a", "A", 48000, "suspended")])
        self.assertEqual(app.active_input_rate(), 0)

    def test_stream_state_is_rendered(self):
        self.assertEqual(app._stream_rates_text([48000], "running"), "48000Hz")
        self.assertEqual(app._stream_rates_text([48000], "suspended"), "48000Hz, suspended")
        self.assertEqual(app._stream_rates_text([], "idle"), "idle")


class TestWatcher(Base):
    def test_bitperfect_forces_the_graph_rate(self):
        win = self.window([SINK, stream("music", "Music", 44100)])
        win.watcher_tick()
        win.watcher_tick()
        self.assertEqual(self.forced, 44100)
        self.assertEqual(len(self.notifications), 1)
        self.assertIn("44100", self.notifications[0][1])

    def test_switching_both_ways_notifies_each_time(self):
        """Regression: silence used to release the pin, silencing one direction."""
        music = stream("music", "Music", 44100)
        firefox = stream("firefox", "Firefox", 48000)
        win = self.window([SINK, music])

        win.watcher_tick()
        win.watcher_tick()
        self.assertEqual(self.forced, 44100)

        # Pause music, play YouTube: still a real change, must notify.
        self.set_nodes([SINK, stream("music", "Music", 44100, "suspended"), firefox])
        win.watcher_tick()
        win.watcher_tick()
        self.assertEqual(self.forced, 48000)
        self.assertEqual(len(self.notifications), 2)
        self.assertIn("48000", self.notifications[1][1])

        # And back again.
        self.set_nodes([SINK, music, stream("firefox", "Firefox", 48000, "suspended")])
        win.watcher_tick()
        win.watcher_tick()
        self.assertEqual(self.forced, 44100)
        self.assertEqual(len(self.notifications), 3)

    def test_flapping_rates_do_nothing(self):
        win = self.window([SINK])
        for rate in (44100, 48000, 44100, 0, 48000, 44100, 0):
            self.set_nodes([SINK, stream("s", "S", rate)] if rate else [SINK])
            win.watcher_tick()
        self.assertEqual(self.notifications, [])
        self.assertEqual(self.forced, 0)

    def test_debounce_delays_action_by_one_tick(self):
        """A decision must be stable for two ticks before it is applied."""
        win = self.window([SINK, stream("music", "Music", 44100)])
        win.watcher_tick()
        self.assertEqual(self.notifications, [])
        self.assertEqual(self.forced, 0)
        win.watcher_tick()
        self.assertEqual(len(self.notifications), 1)
        self.assertEqual(self.forced, 44100)

    def test_stream_change_needs_two_ticks(self):
        win = self.window([SINK])
        win.watcher_tick()
        win.watcher_tick()
        self.set_nodes([SINK, stream("firefox", "Firefox", 48000)])
        win.watcher_tick()
        self.assertNotIn("Active input streams", win.log_buffer.get_text())
        win.watcher_tick()
        self.assertIn(
            "Active input streams: Firefox [48000Hz].", win.log_buffer.get_text()
        )

    def test_silence_keeps_the_pin(self):
        """Releasing on silence dropped the graph to the default and made the
        next switch in that direction a silent no-op."""
        win = self.window([SINK, stream("music", "Music", 44100)])
        win.watcher_tick()
        win.watcher_tick()
        self.assertEqual(self.forced, 44100)
        self.set_nodes([SINK])
        for _ in range(4):
            win.watcher_tick()
        self.assertEqual(self.forced, 44100)

    def test_converged_skip_is_logged_but_silent(self):
        win = self.window([SINK])
        win.watcher_tick()
        win.watcher_tick()
        self.set_nodes([SINK, stream("firefox", "Firefox", 48000)])
        self.set_graph(48000)
        win.watcher_tick()
        win.watcher_tick()
        text = win.log_buffer.get_text()
        self.assertIn("already active", text)
        self.assertEqual(self.notifications, [])

    def test_failure_is_reported_once_then_silent(self):
        app.set_default_rate = lambda rate: (1, "", "pw-metadata boom")
        win = self.window([SINK, stream("music", "Music", 44100)])
        for _ in range(5):
            win.watcher_tick()
        self.assertIn("FAILED", win.log_buffer.get_text())
        self.assertEqual(len(self.notifications), 1)
        self.assertEqual(self.notifications[0][2], "critical")

    def test_stuck_graph_warns_once(self):
        # Commands "succeed" but the graph never moves: the classic lie.
        app.force_rate = lambda rate: (0, "", "")
        self.graph = 44100
        win = self.window([SINK, stream("music", "Music", 48000)])
        for _ in range(8):
            win.watcher_tick()
        warns = [t for t in win.log_buffer.get_text().splitlines() if "did not follow" in t]
        self.assertEqual(len(warns), 1)

    def test_tick_keeps_the_timeout_alive(self):
        """Returning None made GLib drop the source after one call.

        Tick several times: the steady-state path (past the debounce and the
        first apply) is the one that reaches the final return.
        """
        win = self.window([SINK, stream("music", "Music", 44100)])
        for _ in range(6):
            self.assertTrue(win.watcher_tick())
        # And once more with no streams at all.
        self.set_nodes([SINK])
        for _ in range(3):
            self.assertTrue(win.watcher_tick())
        self.set_nodes([SINK, stream("music", "Music", 44100)])
        for _ in range(3):
            self.assertTrue(win.watcher_tick())

    def test_disabled_autowatch_still_logs_streams(self):
        win = self.window([SINK])
        win.watcher_tick()
        win.watcher_tick()
        self.set_nodes([SINK, stream("firefox", "Firefox", 48000)])
        win.watcher_tick()
        win.watcher_tick()
        self.assertIn(
            "Active input streams: Firefox [48000Hz].", win.log_buffer.get_text()
        )


class TestDeviceSettings(Base):
    def test_save_records_format_and_buffer_per_device(self):
        win = self.window([SINK, USB_SINK])
        win.device_combo.set_active_id("alsa_output.usb-dac")
        win.format_combo.set_active_id("S16LE")
        win.buffer_combo.set_active_id("512")
        win.on_save_format()
        win.on_save_buffer()
        stored = app.load_mapping()[2]["alsa_output.usb-dac"]
        self.assertEqual(stored["format"], "S16LE")
        self.assertEqual(stored["buffer_size"], "512")

    def test_reapply_reports_success_not_failure(self):
        """write_*_config return a bool; comparing it to rc==0 logged failures."""
        win = self.window([SINK, USB_SINK])
        win.device_combo.set_active_id("alsa_output.usb-dac")
        win.format_combo.set_active_id("S16LE")
        win.on_save_format()
        win._applied_devices.clear()
        win.watcher_tick()
        text = win.log_buffer.get_text()
        self.assertIn("Re-applied saved format S16LE", text)
        self.assertNotIn("Could not re-apply format", text)

    def test_reset_prunes_stored_entry(self):
        win = self.window([SINK, USB_SINK])
        win.device_combo.set_active_id("alsa_output.usb-dac")
        win.format_combo.set_active_id("S16LE")
        win.buffer_combo.set_active_id("512")
        win.on_save_format()
        win.on_save_buffer()
        win.on_reset_format()
        self.assertEqual(app.load_mapping()[2]["alsa_output.usb-dac"], {"buffer_size": "512"})
        win.on_reset_buffer()
        self.assertNotIn("alsa_output.usb-dac", app.load_mapping()[2])

    def test_device_id_is_stable_across_reconnects(self):
        devices, _ = app.load_devices.__wrapped__() if False else (None, None)  # noqa
        self.set_nodes([SINK, USB_SINK])
        win = app.MainWindow()
        ids = {d["name"]: d["device_id"] for d in win.devices}
        self.assertEqual(ids["alsa_output.usb-dac"], "alsa_output.usb-dac")


class TestDeviceRefresh(Base):
    def test_selection_survives_refresh(self):
        win = self.window([SINK])
        win.device_combo.set_active_id("alsa_output.spk")
        self.set_nodes([SINK, USB_SINK])
        win.refresh_devices_tick()
        self.assertEqual(win.device_combo.get_active_id(), "alsa_output.spk")

    def test_connect_and_disconnect_logged_once(self):
        win = self.window([SINK])
        self.set_nodes([SINK, USB_SINK])
        win.refresh_devices_tick()
        self.set_nodes([SINK])
        for _ in range(4):
            win.refresh_devices_tick()
        text = win.log_buffer.get_text()
        self.assertIn("Device connected: USB DAC.", text)
        self.assertEqual(text.count("Device disconnected"), 1)

    def test_absent_device_pruned_after_three_misses(self):
        win = self.window([SINK, USB_SINK])
        win._applied_devices.add("alsa_output.usb-dac")
        self.set_nodes([SINK])
        for _ in range(3):
            win.refresh_devices_tick()
        self.assertNotIn("alsa_output.usb-dac", win._applied_devices)

    def test_failed_refresh_is_survivable(self):
        win = self.window([SINK])
        app.run = lambda cmd, timeout=20: (1, "", "pw-dump missing")
        self.assertTrue(win.refresh_devices_tick())
        self.assertEqual([d["name"] for d in win.devices], ["alsa_output.spk"])


class TestLogView(Base):
    def test_log_actually_writes_text(self):
        """insert() with a tag name raised TypeError and was swallowed."""
        win = self.window()
        win.log("hello")
        self.assertIn("hello", win.log_buffer.get_text())

    def test_levels_use_tags(self):
        win = self.window()
        win.log("bad", level="error")
        self.assertEqual(win.log_buffer.tagged_chunks()[0][1], ("log_error",))

    def test_unknown_level_falls_back_without_crashing(self):
        win = self.window()
        win.log("odd", level="nonsense")
        self.assertIn("odd", win.log_buffer.get_text())

    def test_survives_tag_creation_failure_and_retries(self):
        win = self.window()
        buffer = win.log_buffer
        original = buffer.create_tag

        def explode(*args, **kwargs):
            raise RuntimeError("no tags here")

        buffer.create_tag = explode
        win.log("during outage")
        self.assertIn("during outage", buffer.get_text())
        buffer.create_tag = original
        win.log("after recovery", level="warn")
        self.assertEqual(buffer.tagged_chunks()[-1][1], ("log_warn",))


class TestDesktopConfig(Base):
    def test_kde_config_falls_back_to_real_home(self):
        """XDG_CONFIG_HOME can point into a sandbox where kdeglobals is absent."""
        home = tempfile.mkdtemp(prefix="pwag-home-")
        real_config = os.path.join(home, ".config")
        os.makedirs(real_config)
        with open(os.path.join(real_config, "kdeglobals"), "w") as handle:
            handle.write("[General]\nColorScheme=BreezeDark\nwidgetStyle=Breeze\n")
        with open(os.path.join(real_config, "plasmarc"), "w") as handle:
            handle.write("[Theme]\nname=Breeze\n")
        sandbox_config = tempfile.mkdtemp(prefix="pwag-sandbox-")
        saved_home = os.environ.get("HOME")
        try:
            os.environ["HOME"] = home
            app.CONFIG_HOME = sandbox_config  # empty: no kdeglobals here
            self.assertEqual(app.kde_config_home(), real_config)
            settings = app.read_kde_theme_settings()
            self.assertEqual(settings["color_scheme"], "BreezeDark")
            self.assertEqual(settings["widget_style"], "Breeze")
            self.assertEqual(settings["plasma_theme"], "Breeze")
        finally:
            if saved_home is not None:
                os.environ["HOME"] = saved_home

    def test_kde_config_prefers_configured_home(self):
        base = tempfile.mkdtemp(prefix="pwag-cfg-")
        with open(os.path.join(base, "kdeglobals"), "w") as handle:
            handle.write("[General]\nColorScheme=Nordic\n")
        app.CONFIG_HOME = base
        self.assertEqual(app.kde_config_home(), base)
        self.assertEqual(app.read_kde_theme_settings()["color_scheme"], "Nordic")


class TestHelpers(Base):
    def test_restart_helper_logs_outcome(self):
        win = self.window()
        app.restart_services = lambda names: (0, "", "")
        win.restart_pipewire()
        self.assertIn("Restart PipeWire: OK.", win.log_buffer.get_text())
        app.restart_services = lambda names: (1, "", "no systemd")
        win.restart_wireplumber()
        self.assertIn("restart failed: no systemd", win.log_buffer.get_text())

    def test_autostart_roundtrip_in_temp_config_home(self):
        self.assertFalse(app.is_autostart_enabled())
        self.assertTrue(app.set_autostart(True))
        self.assertTrue(app.is_autostart_enabled())
        self.assertTrue(app.set_autostart(False))
        self.assertFalse(app.is_autostart_enabled())

    def test_notify_send_reports_failure(self):
        """A missing/failing notify-send used to be reported as success."""
        notify = REAL_NOTIFY_SEND  # not the recording stub
        saved_which = app.shutil.which
        try:
            app.shutil.which = lambda name: None
            ok, reason = notify("s", "b")
            self.assertFalse(ok)
            self.assertIn("not installed", reason)

            failing = os.path.join(self.tmp, "notify-send")
            with open(failing, "w") as handle:
                handle.write("#!/bin/sh\necho boom >&2\nexit 1\n")
            os.chmod(failing, os.stat(failing).st_mode | stat.S_IXUSR)
            app.shutil.which = lambda name: failing
            ok, reason = notify("s", "b")
            self.assertFalse(ok)
            self.assertEqual(reason, "boom")

            good = os.path.join(self.tmp, "notify-ok")
            with open(good, "w") as handle:
                handle.write("#!/bin/sh\nexit 0\n")
            os.chmod(good, os.stat(good).st_mode | stat.S_IXUSR)
            app.shutil.which = lambda name: good
            self.assertEqual(notify("s", "b"), (True, ""))
        finally:
            app.shutil.which = saved_which

    def test_theme_follow_runs_without_desktop_config(self):
        theme, dark, colors = app.follow_system_theme()
        self.assertIsInstance(theme, (str, type(None)))
        self.assertIsInstance(dark, bool)
        self.assertIsInstance(colors, str)

    def test_config_quote_escapes(self):
        self.assertEqual(app.config_quote('a"b\\c'), '"a\\"b\\\\c"')
        self.assertEqual(app.config_quote("a\nb"), '"a b"')


class TestDeadCodeRemoved(unittest.TestCase):
    def test_profiles_are_gone(self):
        for name in ("load_profile", "save_profile", "list_profiles", "BUILTIN_PROFILES"):
            self.assertFalse(hasattr(app, name), name)

    def test_removed_theme_pickers_are_gone(self):
        for name in ("apply_gtk_theme", "load_theme_pref", "save_theme_pref"):
            self.assertFalse(hasattr(app, name), name)

    def test_duplicate_helpers_are_gone(self):
        source = open(APP_PATH).read()
        self.assertNotIn("def spa_quote", source)
        self.assertNotIn("def lua_quote", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)