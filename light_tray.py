#!/usr/bin/env python3
"""Desktop tray GUI for the bedroom Wi-Fi LED controller (Ubuntu/Linux)."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Callable

import lightctl
import light_gui
import music_recognizer

logger = logging.getLogger("light_tray")
LOG_PATH = os.path.expanduser("~/.config/lightss/tray.log")

AI_ROUTABLE_ACTIONS = {
    "brightness",
    "scene",
    "random",
    "temperature",
    "color",
    "hex",
    "effect",
    "preset",
    "fade",
    "sunrise",
    "cycle",
    "audio_reactive",
    "music",
    "schedule",
    "scene_management",
    "device",
}

DARK_STYLESHEET = """
QWidget { background: #0d0f12; color: #e8eaed; font-size: 13px; }
QMainWindow, QTabWidget::pane { background: #0d0f12; border: 1px solid #242832; }
QTabBar::tab { background: #171b22; color: #b8c0cc; padding: 10px 13px; min-height: 22px; border: 1px solid #242832; border-bottom: 0; }
QTabBar::tab:selected { background: #232936; color: #ffffff; }
QGroupBox { background: #151922; border: 1px solid #2b3240; border-radius: 8px; margin-top: 14px; padding: 18px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #dbe7ff; }
QPushButton { background: #2b3342; color: #f5f7fb; border: 1px solid #3a4558; border-radius: 7px; padding: 7px 12px; min-height: 28px; font-weight: 600; }
QPushButton:hover { background: #364155; }
QPushButton:pressed { background: #202633; }
QPushButton:disabled { background: #171b22; color: #606977; border-color: #252b35; }
QLineEdit, QTextEdit, QSpinBox, QComboBox, QTimeEdit { background: #0f131a; color: #f0f3f7; border: 1px solid #303847; border-radius: 6px; padding: 6px; min-height: 24px; selection-background-color: #2d6cdf; }
QSlider { min-height: 26px; }
QSlider::groove:horizontal { height: 7px; background: #2b3240; border-radius: 3px; }
QSlider::handle:horizontal { background: #4da3ff; border: 1px solid #8cc7ff; width: 16px; margin: -6px 0; border-radius: 8px; }
QProgressBar { background: #0f131a; border: 1px solid #303847; border-radius: 6px; min-height: 24px; text-align: center; }
QProgressBar::chunk { background: #20c997; border-radius: 5px; }
QMenu { background: #151922; color: #e8eaed; border: 1px solid #303847; }
QMenu::item:selected { background: #2d6cdf; }
QCheckBox { background: transparent; spacing: 8px; color: #e8eaed; min-height: 26px; padding: 2px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #58657a; border-radius: 3px; background: #0f131a; }
QCheckBox::indicator:checked { background: #4da3ff; border-color: #8cc7ff; }
QScrollArea { background: #0d0f12; border: 0; }
QScrollBar:vertical { background: #0d0f12; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #3a4558; border-radius: 6px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: #4a566b; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { height: 0; }
"""

try:
    from PySide6.QtCore import Qt, QTimer, Signal, QObject
    from PySide6.QtGui import QAction, QFont, QIcon, QPixmap, QCursor, QKeySequence, QShortcut
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QProgressBar,
        QScrollArea,
        QSlider,
        QSpinBox,
        QSystemTrayIcon,
        QVBoxLayout,
        QWidget,
        QTabWidget,
        QTextEdit,
        QTimeEdit,
    )
except ImportError as exc:
    raise SystemExit(
        "Desktop tray GUI requires PySide6. Install it with:\n"
        "  .venv/bin/pip install pyside6"
    ) from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tray_pixmap(size: int = 64) -> QPixmap:
    """Draw a simple warm-glow bulb icon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    from PySide6.QtGui import QPainter, QRadialGradient, QColor

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QRadialGradient(size / 2, size / 2, size / 2)
    gradient.setColorAt(0.0, QColor(255, 220, 100))
    gradient.setColorAt(0.5, QColor(255, 160, 60))
    gradient.setColorAt(1.0, Qt.GlobalColor.transparent)
    painter.setBrush(gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return pixmap


def is_ai_routable_action(action: str) -> bool:
    return action in AI_ROUTABLE_ACTIONS


def build_ai_control_prompt(
    instruction: str,
    state: dict | None = None,
    now_playing: dict[str, str] | None = None,
    device_snapshot: dict | None = None,
) -> str:
    lines = [f"Desktop AI Mode control request: {instruction}"]
    if state:
        seg = state.get("seg", [{}])[0] if state.get("seg") else {}
        col = seg.get("col", [[0, 0, 0, 0]])[0] if seg.get("col") else [0, 0, 0, 0]
        lines.extend(
            [
                "Current light state:",
                f"Power: {'ON' if state.get('on') else 'OFF'}",
                f"Brightness: {state.get('bri', '?')}",
                f"Color: RGBW({', '.join(str(part) for part in col)})",
                f"Effect: {seg.get('fx', '-')}",
                f"Speed: {seg.get('sx', '-')}",
            ]
        )
    if now_playing:
        lines.append(f"Now playing: {light_gui.now_playing_text(now_playing)}")
    if device_snapshot:
        lines.append(light_gui.device_snapshot_text(device_snapshot))
    lines.append(light_gui.ai_action_reference())
    lines.append(
        "Follow the user's direction using only safe supported light actions. "
        "On and Off are system controls, so do not reinterpret them unless explicitly included in this AI request."
    )
    return "\n".join(lines)


class LightWorker:
    """Thread-safe wrapper around LightClient for the GUI."""

    def __init__(self, client: lightctl.LightClient) -> None:
        self.client = client

    def _safe_post(self, payload: lightctl.WledPayload) -> str:
        try:
            logger.info("Posting to %s: %s", getattr(self.client, "state_url", "client"), payload)
            self.client.post_state(payload)
            logger.info("Post succeeded")
            return "OK"
        except Exception as exc:
            logger.exception("post_state failed")
            return str(exc)

    def _safe_visible_post(self, payload: lightctl.WledPayload) -> str:
        return self._safe_post(lightctl.merge_payloads(lightctl.on_payload(True), payload))

    def on(self) -> str:
        return self._safe_post(lightctl.on_payload(True))

    def off(self) -> str:
        return self._safe_post(lightctl.on_payload(False))

    def set_bri(self, value: int, transition_ms: int = 0) -> str:
        return self._safe_visible_post(lightctl.brightness_payload(value, transition_ms=transition_ms))

    def set_color(self, r: int, g: int, b: int, w: int, transition_ms: int = 0) -> str:
        return self._safe_visible_post(lightctl.color_payload(r, g, b, w, transition_ms=transition_ms))

    def set_temp(self, kelvin: int, transition_ms: int = 0) -> str:
        return self._safe_visible_post(lightctl.color_payload(*lightctl.kelvin_to_rgbw(kelvin), transition_ms=transition_ms))

    def set_effect(self, effect_id: int, speed: int, transition_ms: int = 0) -> str:
        try:
            return self._safe_visible_post(lightctl.effect_payload(effect_id, speed, transition_ms=transition_ms))
        except ValueError as exc:
            return str(exc)

    def set_scene(self, name: str, transition_ms: int = 0) -> str:
        try:
            return self._safe_post(lightctl.scene_payload(name, transition_ms=transition_ms))
        except ValueError as exc:
            return str(exc)

    def set_preset(self, preset_id: int, transition_ms: int = 0) -> str:
        try:
            return self._safe_post(lightctl.preset_payload(preset_id, transition_ms=transition_ms))
        except ValueError as exc:
            return str(exc)

    def set_hex(self, hex_str: str, transition_ms: int = 0) -> str:
        try:
            return self._safe_visible_post(lightctl.color_payload(*lightctl.hex_to_rgbw(hex_str), transition_ms=transition_ms))
        except ValueError as exc:
            return str(exc)

    def get_state(self) -> dict | None:
        try:
            return self.client.get_state()
        except Exception:
            logger.exception("get_state failed")
            return None

    def get_device_snapshot(self) -> dict:
        try:
            return self.client.get_device_snapshot()
        except Exception as exc:
            logger.exception("get_device_snapshot failed")
            return {"error": str(exc)}

    def random_scene(self, transition_ms: int = 0) -> str:
        return self._safe_post(lightctl.random_scene_payload(transition_ms=transition_ms))

    def restart(self) -> str:
        try:
            self.client.post_state(lightctl.restart_payload())
            return "Restart command sent."
        except Exception:
            return "Restart command sent (device may have rebooted before responding)."


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

class _ChatSignals(QObject):
    response_ready = Signal(dict)


class _MusicSignals(QObject):
    listen_done = Signal(object, str)
    match_done = Signal(object, str, str)


class _AudioSignals(QObject):
    level_changed = Signal(float, bool)
    error = Signal(str)


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, worker: LightWorker) -> None:
        super().__init__()
        self.worker = worker
        self.setWindowTitle("Bedroom LED Controller")
        self.setMinimumSize(520, 720)

        font = QFont("Inter", 10)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        self.setFont(font)

        # Signals
        self._chat_signals = _ChatSignals()
        self._chat_signals.response_ready.connect(self._on_ai_response)
        self._music_signals = _MusicSignals()
        self._music_signals.listen_done.connect(self._handle_listen_result)
        self._music_signals.match_done.connect(self._handle_match_result)
        self._audio_signals = _AudioSignals()
        self._audio_signals.level_changed.connect(self._on_audio_level)
        self._audio_signals.error.connect(self._on_audio_error)

        # Thread managers
        self._mode1_thread: lightctl.ReactiveThread | None = None
        self._fade_timer: lightctl.FadeTimer | None = None
        self._sunrise_sim: lightctl.SunriseSimulator | None = None
        self._cycle_thread: lightctl.CycleThread | None = None
        self._current_song: dict | None = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # Status
        self.status_label = QLabel("Status: —")
        self.status_label.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self.status_label)

        # Tabs
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self._build_control_tab()
        self._build_color_tab()
        self._build_effects_tab()
        self._build_timers_tab()
        self._build_chat_tab()
        self._build_music_tab()

        # Auto-refresh state
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_state)
        self._refresh_timer.start(3000)
        self._refresh_state()

        # Keyboard shortcuts
        self._esc_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._esc_shortcut.activated.connect(self.hide)

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------

    def _build_control_tab(self) -> None:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        ai_box = QGroupBox("Control Mode")
        ai_layout = QVBoxLayout(ai_box)
        self.ai_mode_checkbox = QCheckBox("AI Mode")
        self.ai_mode_checkbox.setToolTip("Route non-system controls through the AI. On and Off remain direct system controls.")
        ai_layout.addWidget(self.ai_mode_checkbox)
        layout.addWidget(ai_box)

        # Power
        power_box = QGroupBox("Power")
        power_layout = QHBoxLayout(power_box)
        self.btn_on = QPushButton("💡 On")
        self.btn_on.setStyleSheet("background: #2d6cdf; color: white; font-weight: bold; padding: 10px;")
        self.btn_on.setToolTip("Turn the bedroom LEDs on")
        self.btn_on.clicked.connect(self._on_on)
        self.btn_off = QPushButton("⏻ Off")
        self.btn_off.setStyleSheet("background: #b83232; color: white; font-weight: bold; padding: 10px;")
        self.btn_off.setToolTip("Turn the bedroom LEDs off")
        self.btn_off.clicked.connect(self._on_off)
        power_layout.addWidget(self.btn_on)
        power_layout.addWidget(self.btn_off)
        layout.addWidget(power_box)

        # Brightness
        bri_box = QGroupBox("Brightness")
        bri_layout = QVBoxLayout(bri_box)
        self.bri_slider = QSlider(Qt.Orientation.Horizontal)
        self.bri_slider.setRange(0, 255)
        self.bri_slider.setValue(200)
        self.bri_slider.valueChanged.connect(self._on_bri_changed)
        self.bri_slider.sliderReleased.connect(lambda: self._on_bri_changed(self.bri_slider.value()))
        self.bri_slider.setToolTip("Set brightness to selected level")
        self.bri_label = QLabel("200")
        bri_layout.addWidget(self.bri_slider)
        bri_layout.addWidget(self.bri_label)
        layout.addWidget(bri_box)

        # Transition
        trans_box = QGroupBox("Transition (ms)")
        trans_layout = QVBoxLayout(trans_box)
        self.trans_slider = QSlider(Qt.Orientation.Horizontal)
        self.trans_slider.setRange(0, 2000)
        self.trans_slider.setValue(0)
        self.trans_slider.setToolTip("Set transition time in milliseconds")
        self.trans_label = QLabel("0 ms")
        self.trans_slider.valueChanged.connect(lambda v: self.trans_label.setText(f"{v} ms"))
        trans_layout.addWidget(self.trans_slider)
        trans_layout.addWidget(self.trans_label)
        layout.addWidget(trans_box)

        # Scenes
        scene_box = QGroupBox("Scenes")
        scene_layout = QHBoxLayout(scene_box)
        for name in ("Warm", "Night", "Focus", "Ocean", "Party"):
            btn = QPushButton(name)
            btn.setToolTip(f"Apply the {name.lower()} scene")
            btn.clicked.connect(lambda checked, n=name.lower(): self._on_scene(n))
            scene_layout.addWidget(btn)
        self.btn_random = QPushButton("🎲 Random")
        self.btn_random.setStyleSheet("background: #333; color: white;")
        self.btn_random.setToolTip("Apply a random scene")
        self.btn_random.clicked.connect(self._on_random)
        scene_layout.addWidget(self.btn_random)
        layout.addWidget(scene_box)

        # Scene management
        save_box = QGroupBox("Scene Management")
        save_layout = QHBoxLayout(save_box)
        self.scene_name_input = QLineEdit()
        self.scene_name_input.setPlaceholderText("Scene name")
        self.scene_name_input.setToolTip("Enter a name to save or delete a custom scene")
        btn_save_scene = QPushButton("💾 Save")
        btn_save_scene.setToolTip("Save the current LED state as a named scene")
        btn_save_scene.clicked.connect(self._on_save_scene)
        btn_delete_scene = QPushButton("🗑 Delete")
        btn_delete_scene.setToolTip("Delete a saved custom scene")
        btn_delete_scene.clicked.connect(self._on_delete_scene)
        save_layout.addWidget(self.scene_name_input)
        save_layout.addWidget(btn_save_scene)
        save_layout.addWidget(btn_delete_scene)
        layout.addWidget(save_box)

        # Audio Reactive
        mode_box = QGroupBox("Audio Reactive")
        mode_layout = QVBoxLayout(mode_box)
        mode_buttons = QHBoxLayout()
        self.btn_mode1 = QPushButton("▶ Start Mode 1")
        self.btn_mode1.setToolTip("Start microphone-reactive beat mode")
        self.btn_mode1.clicked.connect(self._on_mode1)
        self.btn_stop_mode1 = QPushButton("⏹ Stop Mode 1")
        self.btn_stop_mode1.setToolTip("Stop microphone-reactive beat mode")
        self.btn_stop_mode1.clicked.connect(self._on_stop_mode1)
        self.btn_stop_mode1.setEnabled(False)
        mode_buttons.addWidget(self.btn_mode1)
        mode_buttons.addWidget(self.btn_stop_mode1)
        mode_layout.addLayout(mode_buttons)

        self.vu_bar = QProgressBar()
        self.vu_bar.setRange(0, 100)
        self.vu_bar.setValue(0)
        self.vu_bar.setFormat("Mic input: %p%")
        self.vu_bar.setToolTip("Live microphone level used by Mode 1 beat detection")
        self.vu_bar.setStyleSheet(
            "QProgressBar { background: #111; border: 1px solid #333; border-radius: 6px; text-align: center; }"
            "QProgressBar::chunk { background: #20c997; border-radius: 5px; }"
        )
        self.beat_label = QLabel("Beat: idle")
        self.beat_label.setStyleSheet("font-size: 12px; color: #888;")
        self.beat_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mode_layout.addWidget(self.vu_bar)
        mode_layout.addWidget(self.beat_label)
        layout.addWidget(mode_box)

        # Restart
        restart_box = QGroupBox("Device")
        restart_layout = QHBoxLayout(restart_box)
        btn_restart = QPushButton("⟳ Restart Controller")
        btn_restart.setToolTip("Reboot the WLED controller — device will be offline briefly")
        btn_restart.setStyleSheet("background: #8b3a3a; color: white; font-weight: bold;")
        btn_restart.clicked.connect(self._on_restart)
        restart_layout.addWidget(btn_restart)
        layout.addWidget(restart_box)

        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(widget)
        scroll.setMinimumWidth(0)
        self.tabs.addTab(scroll, "Control")

    def _build_color_tab(self) -> None:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # Temperature presets
        temp_presets_box = QGroupBox("Temperature Presets")
        temp_presets_layout = QHBoxLayout(temp_presets_box)
        for label, k in (("🌅 Warm 2700K", 2700), ("☀ Daylight 5000K", 5000), ("❄ Cool 6500K", 6500)):
            btn = QPushButton(label)
            btn.setToolTip(f"Set color temperature to {k}K")
            btn.clicked.connect(lambda checked, kelvin=k: self._on_temp(kelvin))
            temp_presets_layout.addWidget(btn)
        layout.addWidget(temp_presets_box)

        # Kelvin slider
        kelvin_box = QGroupBox("Kelvin")
        kelvin_layout = QVBoxLayout(kelvin_box)
        self.temp_slider = QSlider(Qt.Orientation.Horizontal)
        self.temp_slider.setRange(2000, 6500)
        self.temp_slider.setValue(4000)
        self.temp_slider.valueChanged.connect(self._on_temp_slider)
        self.temp_slider.sliderReleased.connect(lambda: self._on_temp_slider(self.temp_slider.value()))
        self.temp_slider.setToolTip("Adjust color temperature manually")
        self.temp_label = QLabel("4000 K")
        kelvin_layout.addWidget(self.temp_slider)
        kelvin_layout.addWidget(self.temp_label)
        layout.addWidget(kelvin_box)

        # RGBW
        rgb_box = QGroupBox("RGBW Color")
        rgb_layout = QHBoxLayout(rgb_box)
        self.spin_r = QSpinBox()
        self.spin_g = QSpinBox()
        self.spin_b = QSpinBox()
        self.spin_w = QSpinBox()
        for spin, label in ((self.spin_r, "R"), (self.spin_g, "G"), (self.spin_b, "B"), (self.spin_w, "W")):
            spin.setRange(0, 255)
            spin.setValue(255 if label in ("R", "W") else 0)
            spin.setToolTip(f"{label} channel 0-255")
            rgb_layout.addWidget(QLabel(label))
            rgb_layout.addWidget(spin)
        btn_set_color = QPushButton("🎨 Set Color")
        btn_set_color.setToolTip("Apply the selected RGBW color to the LEDs")
        btn_set_color.clicked.connect(self._on_set_color)
        rgb_layout.addWidget(btn_set_color)
        layout.addWidget(rgb_box)

        # Hex
        hex_box = QGroupBox("Hex Color")
        hex_layout = QHBoxLayout(hex_box)
        self.hex_input = QLineEdit("#ff6600")
        self.hex_input.setMaxLength(9)
        self.hex_input.setToolTip("Enter a hex color like #ff6600 or #ff6600aa")
        btn_hex = QPushButton("Set Hex")
        btn_hex.setToolTip("Apply the hex color to the LEDs")
        btn_hex.clicked.connect(self._on_set_hex)
        hex_layout.addWidget(self.hex_input)
        hex_layout.addWidget(btn_hex)
        layout.addWidget(hex_box)

        # Swatches
        swatch_box = QGroupBox("Color Swatches")
        swatch_layout = QHBoxLayout(swatch_box)
        for rgbw, tip in (
            ((255, 0, 0, 0), "Red"),
            ((0, 0, 255, 0), "Blue"),
            ((255, 100, 0, 255), "Pink White"),
            ((0, 255, 120, 0), "Green"),
        ):
            btn = QPushButton("⬤")
            btn.setStyleSheet(f"color: rgb({rgbw[0]}, {rgbw[1]}, {rgbw[2]}); font-size: 20px;")
            btn.setToolTip(f"Set color to {tip}")
            btn.clicked.connect(lambda checked, r=rgbw[0], g=rgbw[1], b=rgbw[2], w=rgbw[3]: self._set_swatch_color(r, g, b, w))
            swatch_layout.addWidget(btn)
        layout.addWidget(swatch_box)

        layout.addStretch()
        self.tabs.addTab(widget, "🎨 Color")

    def _build_effects_tab(self) -> None:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        fx_box = QGroupBox("Effect")
        fx_layout = QHBoxLayout(fx_box)
        self.fx_combo = QComboBox()
        for effect_id, effect_name in lightctl.SAFE_EFFECTS.items():
            self.fx_combo.addItem(f"{effect_id}: {effect_name}", effect_id)
        self.fx_combo.setToolTip("Select a WLED effect")
        self.fx_speed = QSpinBox()
        self.fx_speed.setRange(0, 255)
        self.fx_speed.setValue(128)
        self.fx_speed.setToolTip("Effect speed 0-255")
        btn_fx = QPushButton("✨ Set Effect")
        btn_fx.setToolTip("Apply the selected effect and speed")
        btn_fx.clicked.connect(self._on_set_effect)
        fx_layout.addWidget(self.fx_combo)
        fx_layout.addWidget(QLabel("Speed"))
        fx_layout.addWidget(self.fx_speed)
        fx_layout.addWidget(btn_fx)
        layout.addWidget(fx_box)

        preset_box = QGroupBox("WLED Preset")
        preset_layout = QHBoxLayout(preset_box)
        self.preset_spin = QSpinBox()
        self.preset_spin.setRange(1, 250)
        self.preset_spin.setValue(1)
        self.preset_spin.setToolTip("Preset ID from 1 to 250")
        btn_preset = QPushButton("📂 Load Preset")
        btn_preset.setToolTip("Load the selected WLED preset")
        btn_preset.clicked.connect(self._on_preset)
        preset_layout.addWidget(QLabel("ID"))
        preset_layout.addWidget(self.preset_spin)
        preset_layout.addWidget(btn_preset)
        layout.addWidget(preset_box)

        layout.addStretch()
        self.tabs.addTab(widget, "✨ Effects")

    def _build_timers_tab(self) -> None:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # Fade Off
        fade_box = QGroupBox("Fade Off")
        fade_layout = QHBoxLayout(fade_box)
        self.fade_minutes = QSpinBox()
        self.fade_minutes.setRange(1, 120)
        self.fade_minutes.setValue(30)
        self.fade_minutes.setToolTip("Fade-off duration in minutes")
        btn_fade = QPushButton("▶ Start Fade")
        btn_fade.setToolTip("Start a gradual fade to off")
        btn_fade.clicked.connect(self._on_fade_off_custom)
        fade_layout.addWidget(QLabel("Minutes"))
        fade_layout.addWidget(self.fade_minutes)
        fade_layout.addWidget(btn_fade)
        layout.addWidget(fade_box)

        # Sunrise
        sunrise_box = QGroupBox("Sunrise")
        sunrise_layout = QHBoxLayout(sunrise_box)
        self.sunrise_minutes = QSpinBox()
        self.sunrise_minutes.setRange(1, 120)
        self.sunrise_minutes.setValue(30)
        self.sunrise_minutes.setToolTip("Sunrise simulation duration in minutes")
        btn_sunrise_start = QPushButton("▶ Start")
        btn_sunrise_start.setToolTip("Start sunrise wake-up simulation")
        btn_sunrise_start.clicked.connect(self._on_sunrise_custom)
        btn_sunrise_stop = QPushButton("⏹ Stop")
        btn_sunrise_stop.setToolTip("Stop sunrise simulation")
        btn_sunrise_stop.clicked.connect(self._on_sunrise_stop)
        sunrise_layout.addWidget(QLabel("Minutes"))
        sunrise_layout.addWidget(self.sunrise_minutes)
        sunrise_layout.addWidget(btn_sunrise_start)
        sunrise_layout.addWidget(btn_sunrise_stop)
        layout.addWidget(sunrise_box)

        # Cycle
        cycle_box = QGroupBox("Cycle")
        cycle_layout = QHBoxLayout(cycle_box)
        self.cycle_interval = QSpinBox()
        self.cycle_interval.setRange(5, 3600)
        self.cycle_interval.setValue(60)
        self.cycle_interval.setToolTip("Scene cycle interval in seconds")
        btn_cycle_start = QPushButton("▶ Start")
        btn_cycle_start.setToolTip("Start automatic scene cycling")
        btn_cycle_start.clicked.connect(self._on_cycle_start)
        btn_cycle_stop = QPushButton("⏹ Stop")
        btn_cycle_stop.setToolTip("Stop automatic scene cycling")
        btn_cycle_stop.clicked.connect(self._on_cycle_stop)
        cycle_layout.addWidget(QLabel("Interval (s)"))
        cycle_layout.addWidget(self.cycle_interval)
        cycle_layout.addWidget(btn_cycle_start)
        cycle_layout.addWidget(btn_cycle_stop)
        layout.addWidget(cycle_box)

        # Schedule
        sched_box = QGroupBox("Schedule")
        sched_layout = QVBoxLayout(sched_box)
        sched_inputs = QHBoxLayout()
        self.sched_time = QTimeEdit()
        self.sched_time.setDisplayFormat("HH:mm")
        self.sched_time.setToolTip("Time to trigger the scheduled action")
        self.sched_action = QComboBox()
        self.sched_action.addItems(["on", "off", "scene"])
        self.sched_action.setToolTip("Action to perform at the scheduled time")
        self.sched_scene = QLineEdit()
        self.sched_scene.setPlaceholderText("Scene name (if action=scene)")
        self.sched_scene.setToolTip("Scene name required when action is 'scene'")
        btn_sched_add = QPushButton("➕ Add")
        btn_sched_add.setToolTip("Add a new scheduled action")
        btn_sched_add.clicked.connect(self._on_schedule_add)
        sched_inputs.addWidget(QLabel("Time"))
        sched_inputs.addWidget(self.sched_time)
        sched_inputs.addWidget(QLabel("Action"))
        sched_inputs.addWidget(self.sched_action)
        sched_inputs.addWidget(self.sched_scene)
        sched_inputs.addWidget(btn_sched_add)
        sched_layout.addLayout(sched_inputs)

        self.sched_list = QTextEdit()
        self.sched_list.setReadOnly(True)
        self.sched_list.setMaximumHeight(120)
        self.sched_list.setToolTip("List of scheduled actions")
        sched_layout.addWidget(self.sched_list)

        btn_sched_refresh = QPushButton("🔄 Refresh List")
        btn_sched_refresh.setToolTip("Refresh the schedule list")
        btn_sched_refresh.clicked.connect(self._on_schedule_refresh)
        sched_layout.addWidget(btn_sched_refresh)

        layout.addWidget(sched_box)
        layout.addStretch()
        self.tabs.addTab(widget, "⏱️ Timers")

    def _build_chat_tab(self) -> None:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setStyleSheet("background: #111; border: 1px solid #333; border-radius: 8px; padding: 8px;")
        layout.addWidget(self.chat_history)

        input_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Ask the AI to control your lights...")
        self.chat_input.setToolTip("Type your request and press Ctrl+Enter to send")
        self.chat_input.returnPressed.connect(self._on_chat_send)

        btn_send = QPushButton("➤ Send")
        btn_send.setToolTip("Send your message to the AI")
        btn_send.clicked.connect(self._on_chat_send)

        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.chat_input)
        shortcut.activated.connect(self._on_chat_send)

        input_layout.addWidget(self.chat_input)
        input_layout.addWidget(btn_send)
        layout.addLayout(input_layout)

        btn_clear = QPushButton("🗑 Clear Chat")
        btn_clear.setToolTip("Clear the chat history")
        btn_clear.clicked.connect(self._clear_chat)
        layout.addWidget(btn_clear)

        self.tabs.addTab(widget, "🤖 Chat")

    def _build_music_tab(self) -> None:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        self.song_title_label = QLabel("No song detected")
        self.song_title_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #eee;")
        self.song_title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.song_title_label)

        self.song_detail_label = QLabel("")
        self.song_detail_label.setStyleSheet("font-size: 13px; color: #aaa;")
        self.song_detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.song_detail_label)

        btn_layout = QHBoxLayout()
        self.btn_detect_song = QPushButton("🔍 Detect Song")
        self.btn_detect_song.setToolTip("Detect currently playing song via playerctl or MPRIS")
        self.btn_detect_song.clicked.connect(self._on_detect_song)

        self.btn_listen = QPushButton("🎤 Listen")
        self.btn_listen.setToolTip("Listen to the microphone for 5 seconds and identify the song via Shazam")
        self.btn_listen.clicked.connect(self._on_listen_song)

        self.btn_match_lights = QPushButton("✨ Match Lights")
        self.btn_match_lights.setToolTip("Detect song and suggest a matching light prompt")
        self.btn_match_lights.clicked.connect(self._on_match_lights_tab)
        self.btn_match_lights.setEnabled(False)

        btn_layout.addWidget(self.btn_detect_song)
        btn_layout.addWidget(self.btn_listen)
        btn_layout.addWidget(self.btn_match_lights)
        layout.addLayout(btn_layout)

        self.music_prompt_label = QLabel("")
        self.music_prompt_label.setStyleSheet("font-size: 12px; color: #d6e4ff; padding: 10px; background: #1a1a2e; border-radius: 6px;")
        self.music_prompt_label.setWordWrap(True)
        self.music_prompt_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.music_prompt_label)

        layout.addStretch()
        self.tabs.addTab(widget, "🎵 Music")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _transition(self) -> int:
        return self.trans_slider.value()

    def _set_status(self, text: str) -> None:
        logger.info("UI status: %s", text)
        self.status_label.setText(f"Status: {text}")

    def _ai_mode_active(self) -> bool:
        return self.ai_mode_checkbox.isChecked()

    def _submit_ai_control(
        self,
        instruction: str,
        now_playing: dict[str, str] | None = None,
    ) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            self._set_status("AI Mode needs OPENAI_API_KEY")
            self._add_chat_system("AI Mode needs OPENAI_API_KEY in the desktop GUI environment.", "#ff6666")
            return
        self._set_status(f"AI Mode: {instruction}")
        self._add_chat_user(f"AI Mode: {instruction}")
        self._add_chat_system("AI is deciding...", "#8fa7bd")

        def _do_ai() -> None:
            try:
                state = self.worker.get_state()
                song = now_playing if now_playing is not None else light_gui.get_now_playing()
                device_snapshot = self.worker.get_device_snapshot()
                prompt = build_ai_control_prompt(
                    instruction,
                    state=state,
                    now_playing=song,
                    device_snapshot=device_snapshot,
                )
                plan = light_gui.call_openai_for_plan(prompt, song, device_snapshot)
                result = light_gui.apply_ai_plan(self.worker.client, plan)
                self._chat_signals.response_ready.emit(result)
            except Exception as exc:
                self._chat_signals.response_ready.emit({"error": str(exc)})

        threading.Thread(target=_do_ai, daemon=True).start()

    def _on_on(self) -> None:
        result = self.worker.on()
        self._set_status(f"On — {result}")

    def _on_off(self) -> None:
        result = self.worker.off()
        self._set_status(f"Off — {result}")

    def _on_bri_changed(self, value: int) -> None:
        self.bri_label.setText(str(value))
        if self._ai_mode_active():
            if self.bri_slider.isSliderDown():
                return
            self._submit_ai_control(f"Set brightness to {value}.")
            return
        result = self.worker.set_bri(value, transition_ms=self._transition())
        self._set_status(f"Brightness {value} — {result}")

    def _on_scene(self, name: str) -> None:
        if self._ai_mode_active():
            self._submit_ai_control(f"Apply the {name} scene while preserving the user's intent.")
            return
        result = self.worker.set_scene(name, transition_ms=self._transition())
        self._set_status(f"Scene {name} — {result}")

    def _on_random(self) -> None:
        if self._ai_mode_active():
            self._submit_ai_control("Choose a random safe lighting scene.")
            return
        result = self.worker.random_scene(transition_ms=self._transition())
        self._set_status(f"Random — {result}")

    def _on_temp(self, kelvin: int) -> None:
        if self.temp_slider.value() == kelvin:
            self._on_temp_slider(kelvin)
        else:
            self.temp_slider.setValue(kelvin)

    def _on_temp_slider(self, value: int) -> None:
        self.temp_label.setText(f"{value} K")
        if self._ai_mode_active():
            if self.temp_slider.isSliderDown():
                return
            self._submit_ai_control(f"Set the lights to {value}K color temperature.")
            return
        result = self.worker.set_temp(value, transition_ms=self._transition())
        self._set_status(f"Temp {value}K — {result}")

    def _on_set_color(self) -> None:
        if self._ai_mode_active():
            self._submit_ai_control(
                "Set RGBW color to "
                f"{self.spin_r.value()}, {self.spin_g.value()}, {self.spin_b.value()}, {self.spin_w.value()}."
            )
            return
        result = self.worker.set_color(
            self.spin_r.value(),
            self.spin_g.value(),
            self.spin_b.value(),
            self.spin_w.value(),
            transition_ms=self._transition(),
        )
        self._set_status(f"Color — {result}")

    def _on_set_hex(self) -> None:
        if self._ai_mode_active():
            self._submit_ai_control(f"Set hex color {self.hex_input.text().strip()}.")
            return
        result = self.worker.set_hex(self.hex_input.text().strip(), transition_ms=self._transition())
        self._set_status(f"Hex — {result}")

    def _on_set_effect(self) -> None:
        effect_id = self.fx_combo.currentData()
        if self._ai_mode_active():
            effect_name = self.fx_combo.currentText()
            self._submit_ai_control(f"Set safe effect {effect_name} at speed {self.fx_speed.value()}.")
            return
        result = self.worker.set_effect(effect_id, self.fx_speed.value(), transition_ms=self._transition())
        self._set_status(f"Effect {effect_id} — {result}")

    def _on_preset(self) -> None:
        if self._ai_mode_active():
            self._submit_ai_control(f"Load or interpret WLED preset {self.preset_spin.value()}.")
            return
        result = self.worker.set_preset(self.preset_spin.value(), transition_ms=self._transition())
        self._set_status(f"Preset — {result}")

    def _on_restart(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Restart Controller",
            "Reboot the WLED controller? It will be offline for a few seconds.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        result = self.worker.restart()
        self._set_status(result)

    def _on_fade_off(self) -> None:
        if self._ai_mode_active():
            self._submit_ai_control("Fade the lights off over 30 minutes.")
            return
        timer = lightctl.FadeTimer(self.worker.client, 30)
        timer.start()
        self._set_status("Fade off started (30 min)")

    def _on_fade_off_custom(self) -> None:
        minutes = self.fade_minutes.value()
        if self._ai_mode_active():
            self._submit_ai_control(f"Fade the lights off over {minutes} minutes.")
            return
        self._fade_timer = lightctl.FadeTimer(self.worker.client, minutes)
        self._fade_timer.start()
        self._set_status(f"Fade off started ({minutes} min)")

    def _on_sunrise(self) -> None:
        sim = lightctl.SunriseSimulator(self.worker.client, duration_minutes=30)
        sim.start()
        self._set_status("Sunrise started (30 min)")

    def _on_sunrise_custom(self) -> None:
        minutes = self.sunrise_minutes.value()
        if self._ai_mode_active():
            self._submit_ai_control(f"Start a sunrise wake-up simulation over {minutes} minutes.")
            return
        if self._sunrise_sim and self._sunrise_sim.is_alive():
            self._set_status("Sunrise already running")
            return
        self._sunrise_sim = lightctl.SunriseSimulator(self.worker.client, duration_minutes=minutes)
        self._sunrise_sim.start()
        self._set_status(f"Sunrise started ({minutes} min)")

    def _on_sunrise_stop(self) -> None:
        if self._ai_mode_active():
            self._submit_ai_control("Stop the sunrise simulation.")
            return
        if self._sunrise_sim:
            result = self._sunrise_sim.stop()
            self._set_status(result)
        else:
            self._set_status("Sunrise not running")

    def _on_cycle_start(self) -> None:
        interval = self.cycle_interval.value()
        if self._ai_mode_active():
            self._submit_ai_control(f"Start cycling safe scenes every {interval} seconds.")
            return
        if self._cycle_thread and self._cycle_thread.is_alive():
            self._set_status("Cycle already running")
            return
        self._cycle_thread = lightctl.CycleThread(self.worker.client, interval_seconds=interval)
        self._cycle_thread.start()
        self._set_status(f"Cycle started ({interval}s)")

    def _on_cycle_stop(self) -> None:
        if self._ai_mode_active():
            self._submit_ai_control("Stop automatic scene cycling.")
            return
        if self._cycle_thread:
            result = self._cycle_thread.stop()
            self._set_status(result)
        else:
            self._set_status("Cycle not running")

    def _on_mode1(self) -> None:
        if self._ai_mode_active():
            self._submit_ai_control("Start audio reactive beat mode and choose lights that respond to the room audio.")
            return
        self._start_mode1_direct()

    def _start_mode1_direct(self) -> None:
        if self._mode1_thread is None:
            self._mode1_thread = self._create_mode1_thread()
        result = self._mode1_thread.start()
        if "started" in result.lower():
            self.btn_mode1.setEnabled(False)
            self.btn_stop_mode1.setEnabled(True)
            self.beat_label.setText("Beat: listening")
        self._set_status(result)

    def _on_stop_mode1(self) -> None:
        if self._ai_mode_active():
            self._submit_ai_control("Stop audio reactive beat mode.")
            return
        self._stop_mode1_direct()

    def _stop_mode1_direct(self) -> None:
        if self._mode1_thread:
            result = self._mode1_thread.stop()
            self._set_status(result)
            self._reset_vu()
            self.btn_mode1.setEnabled(True)
            self.btn_stop_mode1.setEnabled(False)
        else:
            self._set_status("Mode 1 not running")

    def _create_mode1_thread(self) -> lightctl.ReactiveThread:
        return lightctl.ReactiveThread(
            self.worker.client,
            level_callback=lambda energy, beat: self._audio_signals.level_changed.emit(energy, beat),
            error_callback=lambda exc: self._audio_signals.error.emit(str(exc)),
        )

    def _on_audio_level(self, energy: float, beat: bool) -> None:
        level = max(0, min(100, int(round(float(energy) * 100))))
        self.vu_bar.setValue(level)
        if beat:
            self.beat_label.setText("Beat: detected")
            self.beat_label.setStyleSheet("font-size: 12px; color: #ffd43b; font-weight: bold;")
        else:
            self.beat_label.setText("Beat: listening")
            self.beat_label.setStyleSheet("font-size: 12px; color: #20c997;")

    def _on_audio_error(self, message: str) -> None:
        self._reset_vu()
        self._set_status(f"Audio-reactive error: {message}")
        self.beat_label.setText("Beat: audio error")
        self.beat_label.setStyleSheet("font-size: 12px; color: #ff6666;")
        self.btn_mode1.setEnabled(True)
        self.btn_stop_mode1.setEnabled(False)

    def _reset_vu(self) -> None:
        self.vu_bar.setValue(0)
        self.beat_label.setText("Beat: idle")
        self.beat_label.setStyleSheet("font-size: 12px; color: #888;")

    def _pause_mode1_for_microphone(self) -> bool:
        if not self._mode1_thread or not self._mode1_thread.is_alive():
            return False
        self._mode1_thread.stop()
        for _ in range(20):
            if not self._mode1_thread.is_alive():
                break
            time.sleep(0.05)
        return True

    def _resume_mode1_after_microphone(self, was_running: bool) -> None:
        if not was_running:
            return
        if self._mode1_thread is None:
            self._mode1_thread = self._create_mode1_thread()
        self._mode1_thread.start()

    def _on_save_scene(self) -> None:
        name = self.scene_name_input.text().strip()
        if not name:
            self._set_status("Enter a scene name to save")
            return
        state = self.worker.get_state()
        if not state:
            self._set_status("Cannot save scene: not connected")
            return
        payload: dict = {}
        for key in ("on", "bri", "seg", "transition"):
            if key in state:
                payload[key] = state[key]
        lightctl.save_scene(name, payload)
        self._set_status(f"Saved scene '{name}'")

    def _on_delete_scene(self) -> None:
        name = self.scene_name_input.text().strip()
        if not name:
            self._set_status("Enter a scene name to delete")
            return
        lightctl.delete_scene(name)
        self._set_status(f"Deleted scene '{name}'")

    def _set_swatch_color(self, r: int, g: int, b: int, w: int) -> None:
        self.spin_r.setValue(r)
        self.spin_g.setValue(g)
        self.spin_b.setValue(b)
        self.spin_w.setValue(w)
        self._on_set_color()

    def _on_schedule_add(self) -> None:
        time_str = self.sched_time.time().toString("HH:mm")
        action = self.sched_action.currentText()
        data: dict = {}
        scene_name = self.sched_scene.text().strip()
        if action == "scene" and scene_name:
            data["scene"] = scene_name
        lightctl.add_schedule(time_str, action, data)
        self._set_status(f"Scheduled {action} at {time_str}")
        self._on_schedule_refresh()

    def _on_schedule_refresh(self) -> None:
        entries = lightctl.list_schedule()
        if not entries:
            self.sched_list.setPlainText("No schedules.")
            return
        lines = []
        for i, entry in enumerate(entries):
            t = entry.get("time", "?")
            action = entry.get("action", "?")
            extra = ""
            if entry.get("data", {}).get("scene"):
                extra = f" -> scene: {entry['data']['scene']}"
            lines.append(f"{i}: {t} -> {action}{extra}")
        self.sched_list.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    def _on_chat_send(self) -> None:
        text = self.chat_input.text().strip()
        if not text:
            return
        self._add_chat_user(text)
        self.chat_input.clear()
        self._add_chat_system("Thinking...", "#888")

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            self._add_chat_system("AI requires OPENAI_API_KEY environment variable to be set.", "#ff6666")
            return

        def _do_ai():
            try:
                now_playing = light_gui.get_now_playing()
                state = self.worker.get_state()
                device_snapshot = self.worker.get_device_snapshot()
                prompt = build_ai_control_prompt(
                    text,
                    state=state,
                    now_playing=now_playing,
                    device_snapshot=device_snapshot,
                )
                plan = light_gui.call_openai_for_plan(prompt, now_playing, device_snapshot)
                result = light_gui.apply_ai_plan(self.worker.client, plan)
                self._chat_signals.response_ready.emit(result)
            except Exception as exc:
                self._chat_signals.response_ready.emit({"error": str(exc)})

        threading.Thread(target=_do_ai, daemon=True).start()

    def _on_ai_response(self, result: dict) -> None:
        if "error" in result:
            self._add_chat_system(f"Error: {result['error']}", "#ff6666")
            return
        response = result.get("response", "")
        confirmations = result.get("confirmations", [])
        client_actions = result.get("client_actions", [])
        if response:
            self._add_chat_ai(response)
        if confirmations:
            self._add_chat_system(" | ".join(confirmations), "#8fa7bd")
        for action in client_actions:
            if action == "startAudioReactive":
                self._start_mode1_direct()
            elif action == "stopAudioReactive":
                self._stop_mode1_direct()
            elif isinstance(action, dict):
                self._run_ai_client_action(action)
        self._refresh_state()
        self._set_status("AI response received")

    def _run_ai_client_action(self, action: dict) -> None:
        kind = action.get("action")
        if kind == "fadeOff":
            self.fade_minutes.setValue(int(action.get("minutes") or self.fade_minutes.value()))
            self._fade_timer = lightctl.FadeTimer(self.worker.client, self.fade_minutes.value())
            self._fade_timer.start()
            self._set_status(f"Fade off started ({self.fade_minutes.value()} min)")
        elif kind == "startCycle":
            self.cycle_interval.setValue(int(action.get("interval") or self.cycle_interval.value()))
            if not self._cycle_thread or not self._cycle_thread.is_alive():
                self._cycle_thread = lightctl.CycleThread(self.worker.client, interval_seconds=self.cycle_interval.value())
                self._cycle_thread.start()
            self._set_status(f"Cycle started ({self.cycle_interval.value()}s)")
        elif kind == "stopCycle":
            if self._cycle_thread:
                self._set_status(self._cycle_thread.stop())
            else:
                self._set_status("Cycle not running")
        elif kind == "startSunrise":
            self.sunrise_minutes.setValue(int(action.get("minutes") or self.sunrise_minutes.value()))
            if not self._sunrise_sim or not self._sunrise_sim.is_alive():
                self._sunrise_sim = lightctl.SunriseSimulator(
                    self.worker.client,
                    duration_minutes=self.sunrise_minutes.value(),
                    max_brightness=int(action.get("brightness") or 255),
                )
                self._sunrise_sim.start()
            self._set_status(f"Sunrise started ({self.sunrise_minutes.value()} min)")
        elif kind == "stopSunrise":
            if self._sunrise_sim:
                self._set_status(self._sunrise_sim.stop())
            else:
                self._set_status("Sunrise not running")
        elif kind == "detectSong":
            self._on_detect_song()
        elif kind == "listenForSong":
            self._on_listen_song()
        elif kind == "matchLightsToSong":
            self._on_match_lights_tab()

    def _add_chat_user(self, text: str) -> None:
        self.chat_history.append(
            f'<div style="text-align:right;background:#1a3a5c;padding:8px 12px;border-radius:10px;margin:4px 0;color:#e0f0ff;">'
            f'<b>You:</b> {text}</div>'
        )

    def _add_chat_ai(self, text: str) -> None:
        self.chat_history.append(
            f'<div style="text-align:left;background:#1a3a1a;padding:8px 12px;border-radius:10px;margin:4px 0;color:#d5f5e3;">'
            f'<b>AI:</b> {text}</div>'
        )

    def _add_chat_system(self, text: str, color: str = "#888") -> None:
        self.chat_history.append(
            f'<div style="text-align:center;color:{color};font-size:12px;margin:4px 0;">{text}</div>'
        )

    def _clear_chat(self) -> None:
        self.chat_history.clear()

    # ------------------------------------------------------------------
    # Music
    # ------------------------------------------------------------------

    def _on_detect_song(self) -> None:
        song = light_gui.get_now_playing()
        self._update_song_display(song)
        if self._ai_mode_active() and song:
            self._submit_ai_control("Use the detected song to choose matching lighting.", now_playing=song)

    def _update_song_display(self, song: dict | None) -> None:
        if not song or (not song.get("title") and not song.get("artist")):
            self.song_title_label.setText("No song detected")
            self.song_detail_label.setText("")
            self.btn_match_lights.setEnabled(False)
            self._current_song = None
            return
        title = song.get("title", "Unknown")
        artist = song.get("artist", "")
        album = song.get("album", "")
        genre = song.get("genre", "")
        self.song_title_label.setText(title)
        details = []
        if artist:
            details.append(f"Artist: {artist}")
        if album:
            details.append(f"Album: {album}")
        if genre:
            details.append(f"Genre: {genre}")
        self.song_detail_label.setText(" | ".join(details))
        self.btn_match_lights.setEnabled(True)
        self._current_song = song

    def _on_listen_song(self) -> None:
        if not music_recognizer.is_available():
            self.song_title_label.setText("Music recognition unavailable")
            self.song_detail_label.setText(music_recognizer.available_reason())
            self.btn_match_lights.setEnabled(False)
            return
        resume_mode1 = self._pause_mode1_for_microphone()
        self.song_title_label.setText("🎤 Listening...")
        if resume_mode1:
            self._reset_vu()
            self.beat_label.setText("Beat: paused for music recognition")
            self.song_detail_label.setText("Paused Mode 1 so Shazam can use the microphone...")
        else:
            self.song_detail_label.setText("Please wait 5 seconds...")
        self.btn_match_lights.setEnabled(False)

        def _do_listen():
            try:
                result = music_recognizer.recognize_sync()
                if result:
                    song = {
                        "title": result.get("title", ""),
                        "artist": result.get("artist", ""),
                        "album": result.get("album", ""),
                        "genre": result.get("genre", ""),
                    }
                else:
                    song = None
                self._music_signals.listen_done.emit(song, "")
            except Exception as exc:
                self._music_signals.listen_done.emit(None, str(exc))
            finally:
                self._resume_mode1_after_microphone(resume_mode1)

        threading.Thread(target=_do_listen, daemon=True).start()

    def _handle_listen_result(self, song: dict | None, error: str) -> None:
        if error:
            self.song_title_label.setText("Recognition failed")
            self.song_detail_label.setText(error)
            self.btn_match_lights.setEnabled(False)
        else:
            self._update_song_display(song)
            if self._ai_mode_active() and song:
                self._submit_ai_control("Use the identified song to choose matching lighting.", now_playing=song)

    def _on_match_lights_tab(self) -> None:
        self.btn_match_lights.setEnabled(False)
        self.song_title_label.setText("Matching lights...")
        resume_mode1 = self._pause_mode1_for_microphone()
        if resume_mode1:
            self._reset_vu()
            self.beat_label.setText("Beat: paused for music detection")
            self.song_detail_label.setText("Paused Mode 1 so music detection can use the microphone...")
        else:
            self.song_detail_label.setText("Detecting song...")

        def _do_match():
            try:
                song = getattr(self, "_current_song", None)
                if not song:
                    song = light_gui.get_now_playing_with_shazam_fallback(use_shazam=True)
                if not song:
                    self._music_signals.match_done.emit(None, "", "No music detected. Play a song first.")
                    return
                genre = song.get("genre", "")
                prompt = f"Song: {song['title']} by {song['artist']}"
                if genre:
                    prompt += f" (genre: {genre})"
                prompt += ". Create lights that match its mood and energy."
                self._music_signals.match_done.emit(song, prompt, "")
            except Exception as exc:
                self._music_signals.match_done.emit(None, "", str(exc))
            finally:
                self._resume_mode1_after_microphone(resume_mode1)

        threading.Thread(target=_do_match, daemon=True).start()

    def _handle_match_result(self, song: dict | None, prompt: str, error: str) -> None:
        self.btn_match_lights.setEnabled(True)
        if error:
            self.song_title_label.setText("Match failed")
            self.song_detail_label.setText(error)
            return
        if not song:
            self.song_title_label.setText("No song detected")
            self.song_detail_label.setText("Play a song first.")
            return
        self._update_song_display(song)
        if self._ai_mode_active():
            self._submit_ai_control(prompt, now_playing=song)
            return
        self.music_prompt_label.setText(f"Suggested prompt:\n{prompt}")
        self.chat_input.setText(prompt)
        self.tabs.setCurrentIndex(4)

    # ------------------------------------------------------------------
    # State refresh
    # ------------------------------------------------------------------

    def _refresh_state(self) -> None:
        state = self.worker.get_state()
        if state is None:
            self.status_label.setText("🔴 Disconnected")
            return
        on_off = "ON" if state.get("on") else "OFF"
        bri = state.get("bri", "?")
        seg = state.get("seg", [{}])[0] if state.get("seg") else {}
        fx = seg.get("fx", "-")
        sx = seg.get("sx", "-")
        self.status_label.setText(f"🟢 Power: {on_off} | Bri {bri} | FX {fx}@{sx}")

    # ------------------------------------------------------------------
    # Minimize-to-tray
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()
        self._set_status("Minimized to tray")


# ---------------------------------------------------------------------------
# Tray Application
# ---------------------------------------------------------------------------

class TrayApplication(QApplication):
    def __init__(self, argv: list[str], host: str, dry_run: bool) -> None:
        super().__init__(argv)
        self.setQuitOnLastWindowClosed(False)
        self.setStyleSheet(DARK_STYLESHEET)

        client = lightctl.LightClient(host, dry_run=dry_run)
        self.worker = LightWorker(client)

        self.window = MainWindow(self.worker)

        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QIcon(_make_tray_pixmap()))
        self.tray.setToolTip("Bedroom LED Controller")

        menu = QMenu()
        act_show = QAction("Show", self)
        act_show.triggered.connect(self.window.show)
        menu.addAction(act_show)

        menu.addSeparator()

        act_on = QAction("Turn On", self)
        act_on.triggered.connect(lambda checked=False: self._run_tray_action("Turn On", self.worker.on))
        menu.addAction(act_on)

        act_off = QAction("Turn Off", self)
        act_off.triggered.connect(lambda checked=False: self._run_tray_action("Turn Off", self.worker.off))
        menu.addAction(act_off)

        menu_scenes = QMenu("Scenes", menu)
        for name in ("Warm", "Night", "Focus", "Ocean", "Party"):
            act = QAction(name, self)
            act.triggered.connect(
                lambda checked=False, n=name.lower(): self._run_tray_action(
                    f"Scene {n}", lambda n=n: self.worker.set_scene(n)
                )
            )
            menu_scenes.addAction(act)
        menu.addMenu(menu_scenes)

        act_restart = QAction("⟳ Restart Controller", self)
        act_restart.triggered.connect(lambda checked=False: self._run_tray_action("Restart", self.worker.restart))
        menu.addAction(act_restart)

        act_recognize = QAction("🎤 Recognize Music", self)
        act_recognize.triggered.connect(self._on_recognize_music)
        menu.addAction(act_recognize)

        act_match = QAction("✨ Match Lights to Song", self)
        act_match.triggered.connect(self._on_match_lights)
        menu.addAction(act_match)

        menu.addSeparator()

        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        self.window.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.window.isVisible():
                self.window.hide()
            else:
                self.window.show()
                self.window.raise_()
                self.window.activateWindow()

    def _run_tray_action(self, label: str, action: Callable[[], str]) -> None:
        result = action()
        message = f"{label}: {result}"
        logger.info("Tray action: %s", message)
        self.window._set_status(message)
        self.tray.showMessage(
            "Bedroom LED Controller",
            message,
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

    def _on_recognize_music(self) -> None:
        if not music_recognizer.is_available():
            QMessageBox.information(
                self.window,
                "Music Recognition",
                f"Unavailable: {music_recognizer.available_reason()}",
            )
            return
        self.tray.showMessage(
            "Music Recognition",
            "Listening for 5 seconds...",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

        def _do_recognize():
            resume_mode1 = self.window._pause_mode1_for_microphone()
            try:
                result = music_recognizer.recognize_sync()
                if result:
                    lines = [f"🎵 {result.get('title', 'Unknown')}"]
                    if result.get("artist"):
                        lines.append(f"👤 {result['artist']}")
                    if result.get("album"):
                        lines.append(f"💿 {result['album']}")
                    msg = "\n".join(lines)
                else:
                    msg = "No match found. Try playing music louder or closer to the microphone."
            except Exception as exc:
                msg = f"Recognition failed: {exc}"
            finally:
                self.window._resume_mode1_after_microphone(resume_mode1)
            self.tray.showMessage("Music Recognition", msg, QSystemTrayIcon.MessageIcon.Information, 8000)

        threading.Thread(target=_do_recognize, daemon=True).start()

    def _on_match_lights(self) -> None:
        self.tray.showMessage(
            "Match Lights",
            "Detecting song and generating matching lights...",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

        def _do_match():
            import shutil, subprocess, re
            song = None
            resume_mode1 = False
            try:
                if shutil.which("playerctl"):
                    meta = subprocess.run(
                        ["playerctl", "metadata", "--format", "{{artist}}\n{{title}}\n{{album}}"],
                        capture_output=True, text=True, timeout=2,
                    ).stdout
                    lines = [l.strip() for l in meta.splitlines()]
                    if len(lines) >= 2 and (lines[0] or lines[1]):
                        song = {"artist": lines[0], "title": lines[1], "album": lines[2] if len(lines) > 2 else ""}
                if not song and music_recognizer.is_available():
                    resume_mode1 = self.window._pause_mode1_for_microphone()
                    result = music_recognizer.recognize_sync()
                    if result:
                        song = {"artist": result.get("artist", ""), "title": result.get("title", ""), "album": result.get("album", ""), "genre": result.get("genre", "")}
            except Exception as exc:
                self.tray.showMessage("Match Lights", f"Detection failed: {exc}", QSystemTrayIcon.MessageIcon.Warning, 5000)
                return
            finally:
                self.window._resume_mode1_after_microphone(resume_mode1)
            if not song:
                self.tray.showMessage("Match Lights", "No music detected. Play a song first.", QSystemTrayIcon.MessageIcon.Warning, 5000)
                return
            genre = song.get("genre", "")
            prompt = f"Song: {song['title']} by {song['artist']}"
            if genre:
                prompt += f" (genre: {genre})"
            prompt += ". Create lights that match its mood and energy."
            self.tray.showMessage(
                "Match Lights",
                f"Detected: {song['title']} by {song['artist']}\nApply this prompt in the AI panel:\n{prompt}",
                QSystemTrayIcon.MessageIcon.Information,
                10000,
            )

        threading.Thread(target=_do_match, daemon=True).start()


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Desktop tray GUI for the bedroom LED controller.")
    parser.add_argument("--host", default=lightctl.DEFAULT_HOST, help=f"Controller host, default {lightctl.DEFAULT_HOST}")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON instead of sending it")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
        ],
    )
    logger.info("Starting desktop tray GUI; log=%s", LOG_PATH)

    app = TrayApplication(sys.argv, args.host, args.dry_run)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
