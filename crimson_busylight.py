#!/usr/bin/env python3
"""
Crimson Mandala + Busylight — single-file beta proof of concept (v3)
====================================================================

Visualizes EEG brainwave amplitudes as layered sacred-geometry / fractal
patterns, AND drives any connected Kuando Busylight Omega/Alpha units from the
same live brain state (merged from Busylight Control Panel v3).

Six pattern modes, freely fused (toggle any combination):

  MANDALA     — 6 concentric petal rings, Delta outermost → Gamma innermost.
  SRI YANTRA  — 9 interlocking triangles around a bindu point.
  METATRON    — 13-circle Metatron's cube with all internal connections.
  SPIRAL      — golden-ratio (phi) spiral with band-coloured petals.
  LISSAJOUS   — three parametric curves driven by pairs of band frequencies.
  FORMLESS    — flow-field particle cloud — no symmetry, just drift & glow.

Colour comes from a hand-tuned tiny MLP (6 -> 4 -> 3) — visualised live so you
can watch the network turn brain state into colour.

Three input modes:
  [1] SYNTH   — auto-drifting synthetic signals (default)
  [2] MANUAL  — drag the band sliders yourself (debug mode)
  [3] SDK     — connect to a real Crimson / FocusCalm FC-11 headset and drive
                the visuals with LIVE brainwaves.
  [4] MULTI   — connect SEVERAL headsets at once; each shown as its own tile and
                logged to its own CSV/.log files. Pair them one at a time (only
                one headset can be in pairing mode at a time), then all stream
                together.

Busylight output (NEW)
----------------------
Every Busylight found on the USB bus at launch becomes an output channel for the
headset signal. Four mapping schemes, cycled with M:

  BAND    — (default) the lights split the spectrum between them. With N lights,
            N bands are picked evenly across Delta..Gamma; each light shows that
            band's palette colour at a brightness equal to its live amplitude.
            So 2 lights = Delta / Gamma, 6 lights = one band each, and the lights
            match the mandala's rings. A single light shows the dominant band.
  MOOD    — every light shows the colour net's fused output (the same RGB that
            tints the visuals), saturation-normalized so it reads on hardware.
  METRIC  — the lights split into an attention half (warm) and a meditation half
            (cool); brightness is the metric value. One light blends the two.
  EMOTION — every light shows the colour of the inferred mood label, at a
            brightness equal to the classifier's confidence.

With MULTIPLE headsets (MULTI mode), the lights are divided between headsets
first, then the mapping is applied inside each headset's group — so 4 lights and
2 headsets gives each headset 2 lights of its own. If there are more headsets
than lights, each light tracks one headset.

No contact / no stream = a slow amber breathe, so a dropped electrode is obvious
without looking at the screen.

Dependencies:
    pip install pygame numpy
    For SDK mode: pip install cffi, plus the Crimson native library in place.
    For lights:   pip install hidapi, plus busylight_commands.py beside this file.
                  Both are optional — the app runs fine without them.

SDK mode setup (same layout as the working stream test):
    <project>/
    ├── crimson_mandala.py     <- this file
    ├── lib/crimson_sdk.py
    └── libcmsn/
        ├── include/crimson_sdk.h
        └── shared/cmsn.dll (+ tensorflowlite_c.dll, uv.dll, the VC++ runtimes)

    Before pressing SDK: put the headset in PAIRING MODE (long-press power until
    it vibrates; LED blinks blue rapidly), quit the FocusCalm app on every
    phone, and forget the FC-11 in Windows Bluetooth Settings (do NOT OS-pair).

Run:
    python crimson_mandala.py

Keys:
    ESC quit (or exit fullscreen)  ·  1/2/3/4 modes  ·  C contact toggle
    R reset patterns  ·  F fullscreen  ·  window is resizable (drag a corner).
    L toggle lights  ·  M cycle light mapping  ·  K rescan for lights
    [ / ] master light brightness down / up
"""

import os
import sys
import csv
import math
import time
import threading
from queue import Queue, Empty
from dataclasses import dataclass, field
from typing import List, Tuple, Callable

import numpy as np
import pygame

# ---------------------------------------------------------------------------
# Optional Busylight import
# ---------------------------------------------------------------------------
# Two independent pieces: the hidapi binding, and the command-frame table that
# shipped with the control panel. Either can be missing (no hardware on this
# machine, running the visualiser on a laptop, etc.) — in that case the app runs
# exactly as before and the light strip just reports why it's dark.
BUSYLIGHT_AVAILABLE = False
BUSYLIGHT_IMPORT_ERROR = ""
try:
    import hid  # type: ignore
    from busylight_commands import COMMANDS, KEEPALIVE  # type: ignore
    BUSYLIGHT_AVAILABLE = True
except Exception as _bl_exc:            # ImportError, or hidapi .so/.dll missing
    hid = None                          # type: ignore
    COMMANDS, KEEPALIVE = {}, None      # type: ignore
    BUSYLIGHT_IMPORT_ERROR = str(_bl_exc)

# ---------------------------------------------------------------------------
# Optional Crimson SDK import
# ---------------------------------------------------------------------------
# The real SDK is a CFFI wrapper around a native library (cmsn.dll on Windows).
# That DLL depends on sibling DLLs in libcmsn/shared/, and on modern Windows the
# loader ignores PATH for dependency resolution — so we must register that
# directory with os.add_dll_directory() BEFORE importing crimson_sdk, or the
# import fails with error 126 / 0x7e. This mirrors the working stream test.
import os as _os
import sys as _sys

SDK_AVAILABLE = False
SDK_IMPORT_ERROR = ""

def _prepare_sdk_paths():
    """Locate crimson_sdk.py and its native libcmsn/shared, register the DLL
    directory. Returns the shared dir (or None). Safe to call on any OS."""
    here = _os.path.dirname(_os.path.abspath(__file__))
    sdk_dir = None
    for cand in (
        _os.path.join(here, "lib"),
        _os.path.join(here, "..", "lib"),
        _os.path.join(here, "..", "..", "lib"),
        here,
    ):
        if _os.path.isfile(_os.path.join(cand, "crimson_sdk.py")):
            sdk_dir = _os.path.abspath(cand)
            if sdk_dir not in _sys.path:
                _sys.path.insert(0, sdk_dir)
            break

    shared = None
    if sdk_dir:
        cand = _os.path.join(_os.path.dirname(sdk_dir), "libcmsn", "shared")
        if _os.path.isdir(cand):
            shared = _os.path.abspath(cand)
    if shared is None:
        for c in (_os.path.join(here, "libcmsn", "shared"),
                  _os.path.join(here, "..", "libcmsn", "shared")):
            if _os.path.isdir(c):
                shared = _os.path.abspath(c)
                break

    if shared and _sys.platform.startswith("win"):
        try:
            _os.add_dll_directory(shared)
        except (AttributeError, OSError):
            pass
        _os.environ["PATH"] = shared + _os.pathsep + _os.environ.get("PATH", "")
    return shared

_SDK_SHARED_DIR = _prepare_sdk_paths()

# On Windows the DLL sometimes only resolves its siblings via the current working
# directory, so briefly chdir into shared/ across the import, then restore.
_prev_cwd = None
if _SDK_SHARED_DIR and _sys.platform.startswith("win"):
    _prev_cwd = _os.getcwd()
    try:
        _os.chdir(_SDK_SHARED_DIR)
    except OSError:
        _prev_cwd = None
try:
    from crimson_sdk import (  # type: ignore
        CMSNSDK, CMSNDeviceListener, Connectivity,
        ContactState, AFEDataSignalType, set_log_level, LogLevel,
    )
    SDK_AVAILABLE = True
except Exception as _e:  # ImportError, OSError (DLL load), etc.
    SDK_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"
finally:
    if _prev_cwd:
        try:
            _os.chdir(_prev_cwd)
        except OSError:
            pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FPS = 60
SAMPLE_RATE = 250
EEG_BUFFER_LEN = SAMPLE_RATE * 3
MIN_W, MIN_H = 1100, 680     # below this, layout starts to crowd

# (name, low, high, center_hz, petal_count, base_amp, slider_color)
BANDS = [
    ("Delta",    0.5,  4.0,  2.0,   6, 48.0, ( 60, 100, 200)),
    ("Theta",    4.0,  8.0,  6.0,   8, 32.0, ( 90,  80, 220)),
    ("Alpha",    8.0, 12.0, 10.0,  10, 38.0, (140,  90, 220)),
    ("LowBeta", 12.0, 22.0, 17.0,  14, 22.0, (220,  60,  60)),
    ("HighBeta",22.0, 32.0, 27.0,  18, 14.0, (245, 150,  40)),
    ("Gamma",   32.0, 56.0, 44.0,  24,  8.0, (250, 200,  50)),
]
BAND_MAX = np.array([100, 70, 80, 50, 35, 25], dtype=float)

# Real Crimson/FC-11 hardware reports much larger band amplitudes than the
# synthetic engine (e.g. resting delta ~128, gamma ~73 uV/Hz). Using the
# synthetic BAND_MAX above would pin those bands to full and kill the visual
# dynamics. So SDK mode uses its own ceilings, chosen to put typical resting
# values mid-range with headroom for active states. Tune to taste.
BAND_MAX_SDK = np.array([220, 130, 90, 90, 90, 140], dtype=float)

# Palette
BG_DARK    = ( 10,   7,   7)
PANEL      = ( 18,  12,  12)
BORDER     = ( 50,  30,  30)
TEXT       = (220, 215, 210)
TEXT_DIM   = (130, 120, 115)
TEXT_FAINT = ( 80,  72,  70)
ACCENT     = (220,  38,  38)
ACCENT_DIM = (127,  29,  29)
PHI = (1 + math.sqrt(5)) / 2

# --- Busylight hardware -----------------------------------------------------
BL_VENDOR_ID = 0x27BB
BL_MODELS = {0x3BCF: "Omega", 0x3BCE: "Alpha"}
BL_MAX_RGB = 0x64            # device channels are 0-100, NOT 0-255
BL_KEEPALIVE_INTERVAL = 20.0 # device auto-offs at ~30s of silence
BL_UPDATE_HZ = 20.0          # HID writes/sec. The renderer runs at 60; 20 is the
                             # rate the old control panel's colour cycle used, so
                             # it's known-good on this hardware.
BL_MIN_DELTA = 1             # skip a write if no channel moved this much
BL_QUEUE_MAX = 8             # backpressure: drop frames rather than lag behind
BL_STRIP_H = 62              # height of the on-screen light strip

# --- Busylight responsiveness ----------------------------------------------
# Raw band amplitudes barely move and Delta outranks everything all session, so
# the lights are driven off each band's deviation from ITS OWN slow baseline.
BL_BASE_TAU = 10.0           # s — baseline "what's normal for this channel"
BL_DEV_TAU  = 6.0            # s — typical swing size, sets the contrast scale
BL_ATTACK   = 0.55           # envelope rise per update (fast: catch bursts)
BL_RELEASE  = 0.22           # envelope fall per update (slower: avoid flicker)
BL_GAIN     = 1.0            # sensitivity multiplier, , and . adjust it live
BL_WARMUP_S = 4.0            # s of white on connect while the baseline settles

# rank    — most lights on the dominant band, fewer on 2nd and 3rd (default)
# state   — same, over the four GUI brain-state bars (focus/calm/drowsy/alert)
# spread  — one band per light, evenly across Delta..Gamma
# mood    — every light shows the colour net's fused output
# metric  — warm half = attention, cool half = meditation
# emotion — the inferred mood label, in its GUI brain-state colour
LIGHT_MAPPINGS = ["rank", "state", "spread", "mood", "metric", "emotion"]

WHITE_COLOR     = (255, 255, 255)
NO_SIGNAL_COLOR = (255, 150,  20)

# Inferred-mood label -> which of the four GUI brain-state bars it belongs to
# (TinyMLP.HIDDEN_NAMES = focus / calm / drowsy / alert). The lights use those
# same colours, so what's on the LED matches the bar on screen.
EMOTION_TO_STATE = {
    "Focused": 0, "Relaxed": 1, "Meditative": 1,
    "Drowsy":  2, "Alert":    3, "Anxious":    3,
}


# ---------------------------------------------------------------------------
# Emotion inference
# ---------------------------------------------------------------------------
# We don't have a real affective classifier — nobody can read true emotion off a
# single forehead channel. This is a lightweight, honest heuristic that maps the
# band mix + attention/meditation into a coarse, human-readable label. Treat it
# as a mood "vibe", not a clinical readout.
EMOTION_STATES = [
    # (label, description) — chosen by simple rules below
    "Focused", "Relaxed", "Drowsy", "Alert", "Anxious", "Meditative", "Neutral",
]

def infer_emotion(bands_norm, attention, meditation, contact):
    """Return (label, confidence 0-1) from normalized bands + metrics.
    bands_norm order: [delta, theta, alpha, low_beta, high_beta, gamma]."""
    if not contact:
        return ("No contact", 0.0)
    d, th, a, lb, hb, g = (float(x) for x in bands_norm)
    att = attention / 100.0
    med = meditation / 100.0

    beta = (lb + hb) / 2.0
    fast = (beta + g) / 2.0          # engagement / arousal
    slow = (d + th) / 2.0            # drowsiness / rest
    calm = (a + med) / 2.0           # relaxed-alert

    scores = {
        "Focused":    0.55 * att + 0.45 * beta,
        "Alert":      0.60 * g + 0.40 * hb,
        "Relaxed":    0.55 * a + 0.45 * med,
        "Meditative": 0.50 * med + 0.30 * th + 0.20 * a,
        "Drowsy":     0.60 * slow - 0.30 * att,
        "Anxious":    0.55 * hb + 0.25 * g - 0.30 * med,
        "Neutral":    0.35,          # baseline floor
    }
    label = max(scores, key=scores.get)
    vals = sorted(scores.values(), reverse=True)
    # Confidence = how much the top state beats the runner-up (0-1-ish).
    conf = max(0.0, min(1.0, (vals[0] - vals[1]) * 2.0 + 0.25))
    return (label, conf)


# ---------------------------------------------------------------------------
# Data export (log + CSV) and optional terminal readout
# ---------------------------------------------------------------------------
class DataExporter:
    """Writes headset readings to a timestamped .log and .csv, and optionally
    echoes a compact live readout to the terminal. Rate-limited so it doesn't
    flood the disk or console (the renderer runs at 60 FPS; we sample slower)."""

    def __init__(self, out_dir=None, terminal=True, hz=5.0, prefix="crimson_session"):
        self.terminal = terminal
        self.period = 1.0 / hz if hz > 0 else 0.2
        self._last = 0.0
        self._t0 = time.time()
        self._fh_log = None
        self._csv = None
        self._fh_csv = None
        self._open = False
        self._row_count = 0

        base = out_dir or os.path.dirname(os.path.abspath(__file__))
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(base, f"{prefix}_{stamp}.log")
        self.csv_path = os.path.join(base, f"{prefix}_{stamp}.csv")
        try:
            self._fh_log = open(self.log_path, "w", encoding="utf-8", buffering=1)
            self._fh_csv = open(self.csv_path, "w", encoding="utf-8", newline="")
            self._csv = csv.writer(self._fh_csv)
            self._csv.writerow([
                "wall_time", "elapsed_s", "mode", "source",
                "delta", "theta", "alpha", "low_beta", "high_beta", "gamma",
                "attention", "meditation", "contact",
                "emotion", "emotion_confidence",
                "rgb_r", "rgb_g", "rgb_b",
            ])
            self._fh_csv.flush()
            self._open = True
        except OSError as e:
            print(f"[export] Could not open output files: {e}")
            self._open = False

    def update(self, mode, bands_norm, raw_bands, attention, meditation,
               contact, rgb):
        """Call every frame; internally rate-limited. raw_bands are the
        un-normalized uV/Hz values (nicer for the CSV)."""
        now = time.time()
        if now - self._last < self.period:
            return
        self._last = now

        label, conf = infer_emotion(bands_norm, attention, meditation, contact)
        elapsed = now - self._t0
        rb = [float(x) for x in raw_bands]

        # Terminal line
        if self.terminal:
            cs = "contact" if contact else "LEAD-OFF"
            print(
                f"[{elapsed:6.1f}s {mode:6s}] "
                f"d{rb[0]:6.1f} th{rb[1]:6.1f} a{rb[2]:6.1f} "
                f"lB{rb[3]:6.1f} hB{rb[4]:6.1f} g{rb[5]:6.1f} | "
                f"att {attention:5.1f} med {meditation:5.1f} | "
                f"{cs:8s} | {label:11s} ({conf*100:3.0f}%)"
            )

        # File writes
        if self._open:
            self._fh_log.write(
                f"{time.strftime('%H:%M:%S')} {mode} "
                f"bands=[{', '.join(f'{v:.2f}' for v in rb)}] "
                f"att={attention:.1f} med={meditation:.1f} "
                f"contact={contact} emotion={label} conf={conf:.2f}\n"
            )
            self._csv.writerow([
                time.strftime("%Y-%m-%dT%H:%M:%S"), f"{elapsed:.3f}", mode,
                "headset" if mode == "sdk" else mode,
                f"{rb[0]:.3f}", f"{rb[1]:.3f}", f"{rb[2]:.3f}",
                f"{rb[3]:.3f}", f"{rb[4]:.3f}", f"{rb[5]:.3f}",
                f"{attention:.2f}", f"{meditation:.2f}", int(bool(contact)),
                label, f"{conf:.3f}",
                f"{rgb[0]:.3f}", f"{rgb[1]:.3f}", f"{rgb[2]:.3f}",
            ])
            self._fh_csv.flush()
            self._row_count += 1

    def close(self):
        if self._open:
            try:
                self._fh_log.close()
            except Exception:
                pass
            try:
                self._fh_csv.close()
            except Exception:
                pass
            print(f"[export] Saved {self._row_count} rows")
            print(f"[export]   log: {self.log_path}")
            print(f"[export]   csv: {self.csv_path}")
        self._open = False



class TinyMLP:
    """Hand-tuned 6 -> 4 -> 3 MLP. Hidden layer is interpretable:
       focus / calm / drowsy / alert."""

    HIDDEN_NAMES  = ["focus", "calm", "drowsy", "alert"]
    HIDDEN_COLORS = [(220, 80, 80), (80, 180, 220), (130, 100, 200), (240, 200, 80)]

    def __init__(self):
        self.W1 = np.array([
            [-0.6, -0.5, -0.2,  0.9,  1.0,  0.4],   # focus
            [-0.3,  0.3,  1.0, -0.5, -0.7, -0.4],   # calm
            [ 1.0,  0.8,  0.0, -0.7, -0.6, -0.5],   # drowsy
            [-0.4, -0.3, -0.1,  0.3,  0.8,  1.0],   # alert
        ])
        self.b1 = np.array([-0.2, -0.2, -0.2, -0.2])
        self.W2 = np.array([
            [ 0.9, -0.4, -0.5,  0.8],   # R
            [ 0.5,  0.3, -0.3,  0.4],   # G
            [-0.5,  0.9,  0.7, -0.4],   # B
        ])
        self.b2 = np.array([0.1, 0.1, 0.1])
        self.last_input  = np.zeros(6)
        self.last_hidden = np.zeros(4)
        self.last_output = np.array([0.5, 0.5, 0.5])

    def forward(self, x):
        h     = np.tanh(self.W1 @ x + self.b1)
        h_pos = (h + 1.0) * 0.5
        rgb   = 1.0 / (1.0 + np.exp(-(self.W2 @ h + self.b2)))
        self.last_input, self.last_hidden, self.last_output = x.copy(), h_pos, rgb
        return rgb, h_pos


# ---------------------------------------------------------------------------
# Shared brain state
# ---------------------------------------------------------------------------
@dataclass
class BrainState:
    bands: np.ndarray = field(default_factory=lambda: np.array(
        [b[5] for b in BANDS], dtype=float))
    attention: float = 50.0
    meditation: float = 50.0
    contact: bool = True
    eeg_buffer: np.ndarray = field(default_factory=lambda: np.zeros(EEG_BUFFER_LEN))
    eeg_write: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def normalized_bands(self, ceiling=None):
        if ceiling is None:
            ceiling = BAND_MAX
        with self.lock:
            return np.clip(self.bands / ceiling, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Signal sources
# ---------------------------------------------------------------------------
def _synthesize_eeg(state, dt, t_phase, contact):
    n = int(dt * SAMPLE_RATE)
    for _ in range(n):
        t_phase += 1.0 / SAMPLE_RATE
        if contact:
            v = 0.0
            for i, b in enumerate(BANDS):
                v += state.bands[i] * math.sin(2 * math.pi * b[3] * t_phase + i * 0.7)
            v += np.random.uniform(-7, 7)
            if np.random.random() < 0.0008:
                v += np.random.uniform(-90, 90)
        else:
            v = np.random.uniform(-150, 150)
        v = max(-150.0, min(150.0, v))
        state.eeg_buffer[state.eeg_write] = v
        state.eeg_write = (state.eeg_write + 1) % EEG_BUFFER_LEN
    return t_phase


def _derive_metrics(state, smooth=0.05):
    beta = state.bands[3] + state.bands[4]
    calm = state.bands[2] + state.bands[1]
    if not state.contact:
        att_t, med_t = 5.0, 5.0
    else:
        att_t = 30 + (beta / (beta + calm + 1.0)) * 80 + np.random.uniform(-5, 5)
        med_t = 25 + (calm / (calm + beta + 1.0)) * 80 + np.random.uniform(-4, 4)
    state.attention  += (att_t - state.attention)  * smooth
    state.meditation += (med_t - state.meditation) * smooth
    state.attention  = max(0, min(100, state.attention))
    state.meditation = max(0, min(100, state.meditation))


class SyntheticSource:
    def __init__(self, state):
        self.state = state
        self.targets = np.array([b[5] for b in BANDS], dtype=float)
        self.last_drift = 0.0
        self.t_phase = 0.0

    def step(self, dt, t_now):
        if t_now - self.last_drift > 2.0:
            self.last_drift = t_now
            for i, b in enumerate(BANDS):
                self.targets[i] = b[5] * np.random.uniform(0.5, 1.5)
        with self.state.lock:
            self.state.bands += (self.targets - self.state.bands) * min(1.0, dt * 0.6)
            self.t_phase = _synthesize_eeg(self.state, dt, self.t_phase, self.state.contact)
            _derive_metrics(self.state, 0.05)


class ManualSource:
    def __init__(self, state, slider_values):
        self.state = state
        self.slider_values = slider_values
        self.t_phase = 0.0

    def step(self, dt, t_now):
        with self.state.lock:
            for i in range(6):
                self.state.bands[i] = self.slider_values[i] * BAND_MAX[i]
            self.t_phase = _synthesize_eeg(self.state, dt, self.t_phase, self.state.contact)
            _derive_metrics(self.state, 0.20)


def _scale_metric(v):
    """SDK builds differ: some report attention/meditation as 0.0-1.0, others
    as 0-100. Normalize to 0-100. (This fixes the '2500%' bug.)"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return v * 100.0 if v <= 1.0 else v


class SDKSource:
    """Live headset source. Streams real EEG into the shared BrainState via the
    Crimson SDK's callbacks. The renderer reads BrainState the same way it does
    for SYNTH/MANUAL, so nothing downstream needs to change."""

    def __init__(self, state):
        self.state = state
        self.device = None
        self.device_name = None
        self.target_name = None
        self.status = "Idle" if SDK_AVAILABLE else (
            "SDK unavailable: " + (SDK_IMPORT_ERROR or "crimson_sdk not found"))
        self.streaming = False
        # Periodic-rescan state: keep looking for a headset after launch.
        self._want_scan = False
        self._last_scan_attempt = 0.0
        self._rescan_period = 4.0     # seconds between rescans while unconnected

    def step(self, dt, t_now):
        # Keep scanning (retrying every few seconds) until we have a device.
        # This lets the app pick up a headset that's powered on / put into
        # pairing mode AFTER launch, instead of only at startup.
        if not SDK_AVAILABLE:
            return
        if self.device is not None:
            return  # already connected/connecting
        if not self._want_scan:
            return
        if t_now - self._last_scan_attempt >= self._rescan_period:
            self._last_scan_attempt = t_now
            self._issue_scan()

    def _issue_scan(self):
        try:
            set_log_level(LogLevel.warning)
            CMSNSDK.start_device_scan(self._on_found)
            self.status = "Scanning for headset... (LED must blink blue)"
        except Exception as exc:
            self.status = f"Scan error: {exc}"

    def start_scan(self, target_name=None):
        if not SDK_AVAILABLE:
            self.status = "SDK unavailable: " + (SDK_IMPORT_ERROR or "not found")
            return
        # If we're already connected/streaming, don't restart.
        if self.device is not None:
            self.status = f"Already connected: {self.device_name}"
            return
        self.target_name = target_name
        self.streaming = False
        self._want_scan = True                 # enable periodic rescans
        self._last_scan_attempt = time.time()
        self._issue_scan()

    def _matches(self, name):
        name = name or ""
        if self.target_name:
            return name == self.target_name
        return any(p in name for p in ("FC11", "CMSN"))

    def _on_found(self, device):
        if not self._matches(device.name):
            return
        if self.device is not None:
            return  # already latched onto one
        try:
            CMSNSDK.stop_device_scan()
            self.device = device
            self.device_name = device.name
            self._want_scan = False        # stop rescanning; we have one
            self.status = f"Found {device.name} - connecting..."
            device.set_listener(self._make_listener())
            device.connect()
        except Exception as exc:
            self.status = f"Connect error: {exc} (retrying...)"
            self.device = None
            self._want_scan = True         # keep looking

    def _make_listener(self):
        state, outer = self.state, self

        class _L(CMSNDeviceListener):  # type: ignore[misc]
            def on_connectivity_change(self, c):
                if c == Connectivity.connected:
                    outer.status = f"{outer.device_name}: pairing..."
                    try:
                        outer.device.pair(lambda d, r: outer._on_pair(r))
                    except Exception as exc:
                        outer.status = f"Pair call error: {exc}"
                elif c == Connectivity.disconnected:
                    outer.status = f"{outer.device_name or 'device'}: disconnected (rescanning...)"
                    outer.streaming = False
                    outer.device = None
                    outer._want_scan = True          # auto-reconnect
                    outer._last_scan_attempt = 0.0    # rescan on next step

            def on_contact_state_change(self, cs):
                with state.lock:
                    state.contact = (cs == ContactState.contact)

            def on_brain_wave(self, bw):
                # BANDS order is [delta, theta, alpha, low_beta, high_beta, gamma]
                with state.lock:
                    state.bands[:] = [bw.delta, bw.theta, bw.alpha,
                                      bw.low_beta, bw.high_beta, bw.gamma]

            def on_attention(self, a):
                with state.lock:
                    state.attention = max(0.0, min(100.0, _scale_metric(a)))

            def on_meditation(self, m):
                with state.lock:
                    state.meditation = max(0.0, min(100.0, _scale_metric(m)))

            def on_eeg_data(self, eeg):
                # Skip the device's lead-off probe packets — not real EEG.
                if eeg.signal_type == AFEDataSignalType.lead_off_detection:
                    return
                with state.lock:
                    for s in eeg.eeg_data:
                        state.eeg_buffer[state.eeg_write] = s
                        state.eeg_write = (state.eeg_write + 1) % EEG_BUFFER_LEN

            def on_error(self, err):
                msg = getattr(err, "message", None)
                code = getattr(err, "code", err)
                outer.status = f"Error: {msg or ''} ({code})"

        return _L()

    def _on_pair(self, response):
        if response.success():
            self.status = f"{self.device_name}: paired, starting stream..."
            try:
                self.device.start_eeg_stream(cb=self._on_stream_started)
            except Exception as exc:
                self.status = f"Stream start error: {exc}"
        else:
            err = getattr(response, "error", "unknown")
            self.status = f"Pairing failed: {err}"

    def _on_stream_started(self, device, response):
        if response.success():
            self.streaming = True
            self.status = f"{self.device_name}: streaming EEG"
        else:
            err = getattr(response, "error", "unknown")
            self.status = f"Stream failed: {err}"

    def stop(self):
        if self.device is not None:
            try:
                self.device.stop_eeg_stream()
            except Exception:
                pass
            try:
                self.device.disconnect()
            except Exception:
                pass
            self.device = None
            self.streaming = False


# ---------------------------------------------------------------------------
# Multi-headset manager
# ---------------------------------------------------------------------------
class HeadsetSlot:
    """One connected headset: its own device, BrainState, exporter, and status.
    Streams independently of the others."""

    def __init__(self, name, out_dir=None):
        self.name = name
        self.state = BrainState()
        self.device = None
        self.status = "found"
        self.streaming = False
        self.contact = True
        # Per-headset export files (one set of files per headset).
        safe = "".join(c for c in name if c.isalnum() or c in "-_")
        self.exporter = DataExporter(out_dir=out_dir, terminal=False, hz=5.0,
                                     prefix=f"headset_{safe}")

    def close(self):
        if self.device is not None:
            try:
                self.device.stop_eeg_stream()
            except Exception:
                pass
            try:
                self.device.disconnect()
            except Exception:
                pass
            self.device = None
            self.streaming = False
        try:
            self.exporter.close()
        except Exception:
            pass


class MultiHeadsetManager:
    """Discovers and manages MULTIPLE FC-11/CMSN headsets concurrently.

    Bluetooth reality: pairing must be sequential (only one headset can be in
    pairing mode and paired at a time), but once paired, all headsets stream
    concurrently. Each headset gets its own BrainState + its own CSV/log files.
    """

    def __init__(self, out_dir=None):
        self.out_dir = out_dir
        self.slots = {}            # name -> HeadsetSlot
        self.order = []            # stable display order
        self.scanning = False
        self.status = "Idle" if SDK_AVAILABLE else (
            "SDK unavailable: " + (SDK_IMPORT_ERROR or "not found"))
        self._lock = threading.Lock()
        # Periodic rescan so headsets powered on AFTER launch are still found.
        self._want_scan = False
        self._last_scan_attempt = 0.0
        self._rescan_period = 4.0

    def step(self, dt, t_now):
        # Keep re-issuing the scan so newly powered-on headsets get discovered.
        # We never latch to a single device here (we want them all), so it's
        # safe to keep scanning for the whole session.
        if not SDK_AVAILABLE or not self._want_scan:
            return
        if t_now - self._last_scan_attempt >= self._rescan_period:
            self._last_scan_attempt = t_now
            self._issue_scan()

    def _issue_scan(self):
        try:
            set_log_level(LogLevel.warning)
            CMSNSDK.start_device_scan(self._on_found)
        except Exception as exc:
            self.status = f"Scan error: {exc}"

    def start_scan(self):
        """Scan and connect to EVERY FC-11/CMSN headset found (that we don't
        already have). Because they all advertise the same name prefix, put them
        into pairing mode one at a time for reliable sequential pairing.
        Keeps scanning for the whole session so late headsets are picked up."""
        if not SDK_AVAILABLE:
            self.status = "SDK unavailable: " + (SDK_IMPORT_ERROR or "not found")
            return
        self.scanning = True
        self._want_scan = True
        self._last_scan_attempt = time.time()
        n = len(self.slots)
        self.status = (f"Scanning ({n} connected) — put the next headset in "
                       f"pairing mode (blue blink)")
        self._issue_scan()

    def stop_scan(self):
        self._want_scan = False
        try:
            CMSNSDK.stop_device_scan()
        except Exception:
            pass
        self.scanning = False
        self.status = f"{len(self.slots)} headset(s) connected"

    def _on_found(self, device):
        name = device.name or ""
        if not any(p in name for p in ("FC11", "CMSN")):
            return
        with self._lock:
            if name in self.slots:
                return  # already have this one
            slot = HeadsetSlot(name, out_dir=self.out_dir)
            self.slots[name] = slot
            self.order.append(name)
        slot.device = device
        slot.status = "connecting"
        self.status = f"Connecting to {name}... ({len(self.slots)} total)"
        try:
            device.set_listener(self._make_listener(slot))
            device.connect()
            # Encourage a fresh scan soon so the next headset is found quickly.
            self._last_scan_attempt = 0.0
        except Exception as exc:
            slot.status = f"connect error: {exc}"

    def _make_listener(self, slot):
        mgr = self

        class _L(CMSNDeviceListener):  # type: ignore[misc]
            def on_connectivity_change(self, c):
                if c == Connectivity.connected:
                    slot.status = "pairing"
                    try:
                        slot.device.pair(lambda d, r: mgr._on_pair(slot, r))
                    except Exception as exc:
                        slot.status = f"pair error: {exc}"
                elif c == Connectivity.disconnected:
                    slot.status = "disconnected"
                    slot.streaming = False

            def on_contact_state_change(self, cs):
                good = (cs == ContactState.contact)
                slot.contact = good
                with slot.state.lock:
                    slot.state.contact = good

            def on_brain_wave(self, bw):
                with slot.state.lock:
                    slot.state.bands[:] = [bw.delta, bw.theta, bw.alpha,
                                           bw.low_beta, bw.high_beta, bw.gamma]

            def on_attention(self, a):
                with slot.state.lock:
                    slot.state.attention = max(0.0, min(100.0, _scale_metric(a)))

            def on_meditation(self, m):
                with slot.state.lock:
                    slot.state.meditation = max(0.0, min(100.0, _scale_metric(m)))

            def on_eeg_data(self, eeg):
                if eeg.signal_type == AFEDataSignalType.lead_off_detection:
                    return
                with slot.state.lock:
                    for s in eeg.eeg_data:
                        slot.state.eeg_buffer[slot.state.eeg_write] = s
                        slot.state.eeg_write = (slot.state.eeg_write + 1) % EEG_BUFFER_LEN

            def on_error(self, err):
                msg = getattr(err, "message", None)
                code = getattr(err, "code", err)
                slot.status = f"error: {msg or ''} ({code})"

        return _L()

    def _on_pair(self, slot, response):
        if response.success():
            slot.status = "paired, starting stream"
            try:
                slot.device.start_eeg_stream(
                    cb=lambda d, r: mgr_stream_started(slot, r))
            except Exception as exc:
                slot.status = f"stream error: {exc}"
        else:
            err = getattr(response, "error", "unknown")
            slot.status = f"pair failed: {err}"

    def export_all(self):
        """Push each headset's current readings to its own files. Call each
        frame; each slot's DataExporter rate-limits internally."""
        for name in self.order:
            slot = self.slots.get(name)
            if slot is None:
                continue
            with slot.state.lock:
                raw = slot.state.bands.copy()
                att = slot.state.attention
                med = slot.state.meditation
                con = slot.state.contact
            nb = np.clip(raw / BAND_MAX_SDK, 0.0, 1.0)
            slot.exporter.update("sdk", nb, raw, att, med, con, (0.5, 0.5, 0.5))

    def stop(self):
        self.stop_scan()
        for name in self.order:
            slot = self.slots.get(name)
            if slot:
                slot.close()


def mgr_stream_started(slot, response):
    if response.success():
        slot.streaming = True
        slot.status = "streaming"
    else:
        err = getattr(response, "error", "unknown")
        slot.status = f"stream failed: {err}"


# ---------------------------------------------------------------------------
# Busylight output — device wrapper, colour mapping, bridge
# ---------------------------------------------------------------------------
def bl_to_screen(r, g, b):
    """Device RGB (0-100) -> screen colour (0-255) for the on-screen strip."""
    s = lambda c: max(0, min(255, int(c * 255 / BL_MAX_RGB)))
    return (s(r), s(g), s(b))


def bl_from_palette(color255, level=1.0, saturate=True):
    """Palette colour (0-255) + brightness 0-1 -> device triple (0-100).

    The UI palette is tuned for dark-background rendering, so its colours top out
    well below full channel range — on an LED that just reads as murky. Scaling
    the peak channel up to full first preserves the hue but hands the whole
    0-100 range to the brightness term, which is what makes amplitude legible."""
    lvl = max(0.0, min(1.0, level))
    peak = max(color255) if saturate else 255
    if not peak:
        return (0, 0, 0)
    return tuple(max(0, min(BL_MAX_RGB, int(round(c / peak * BL_MAX_RGB * lvl))))
                 for c in color255)


def make_color_command(r, g, b):
    """Build a light-control frame for an arbitrary RGB triple (0-100 each).

    Same trick the control panel used: clone a known-good frame from the command
    table and patch only the three colour bytes, so whatever header, flags and
    trailer the device expects survive untouched."""
    cmd = COMMANDS['white'].copy()
    cmd[3:6] = [
        max(0, min(BL_MAX_RGB, int(r))),
        max(0, min(BL_MAX_RGB, int(g))),
        max(0, min(BL_MAX_RGB, int(b))),
    ]
    return cmd


class BusylightDevice:
    """Single busylight: owns its HID handle, write queue, and worker thread.

    Carried over from the control panel, minus the colour-cycle loop — the
    headset is the animator now. One writer thread per device keeps HID writes
    serialized, and the queue is bounded so a slow device drops stale frames
    instead of falling further and further behind the EEG stream."""

    def __init__(self, device_info, model_name):
        self.info = device_info
        self.model = model_name
        self.path = device_info['path']
        self.device = hid.device()
        self.device.open_path(self.path)

        self.queue = Queue()
        self.current_rgb = (0, 0, 0)
        self._shutdown = threading.Event()

        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        self._ka_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._ka_thread.start()

    # --- internal threads ---

    def _worker_loop(self):
        while not self._shutdown.is_set():
            try:
                item = self.queue.get(timeout=0.2)
            except Empty:
                continue
            if item is None:
                break
            cmd, rgb = item
            try:
                self.device.write(cmd)
                if rgb is not None:
                    self.current_rgb = rgb
            except Exception as exc:
                print(f"[busylight {self.model}] write error: {exc}")

    def _keepalive_loop(self):
        while not self._shutdown.wait(BL_KEEPALIVE_INTERVAL):
            if KEEPALIVE is not None:
                self.queue.put((KEEPALIVE, None))

    # --- public API ---

    def send_rgb(self, r, g, b):
        """Queue a colour. Cheap to call often: identical or near-identical
        colours are dropped, and so is anything arriving on a backed-up queue."""
        rgb = (int(r), int(g), int(b))
        if max(abs(a - c) for a, c in zip(rgb, self.current_rgb)) < BL_MIN_DELTA:
            return
        if self.queue.qsize() >= BL_QUEUE_MAX:
            return
        try:
            self.queue.put((make_color_command(*rgb), rgb))
        except Exception as exc:
            print(f"[busylight {self.model}] frame error: {exc}")

    def turn_off(self):
        try:
            self.queue.put((COMMANDS['off'], (0, 0, 0)))
        except Exception:
            pass

    def close(self):
        self._shutdown.set()
        try:
            self.device.write(COMMANDS['off'])
        except Exception:
            pass
        try:
            self.device.close()
        except Exception:
            pass


@dataclass
class LightSnapshot:
    """One brain's worth of state, flattened for the lights. In MULTI mode there
    is one of these per headset; otherwise there's exactly one."""
    name: str
    bands: np.ndarray                       # normalized 0-1, BANDS order
    attention: float                        # 0-100
    meditation: float                       # 0-100
    contact: bool
    rgb: Tuple[float, float, float]         # colour-net output, 0-1
    hidden: np.ndarray = None               # colour-net hidden layer, 0-1
    live: bool = True                       # is a real signal arriving?

    def __post_init__(self):
        if self.hidden is None:
            self.hidden = np.full(len(TinyMLP.HIDDEN_NAMES), 0.5)


class SignalTracker:
    """Per-headset adaptive normalizer + envelope follower.

    Absolute band amplitudes are why the lights felt sluggish: resting Delta runs
    an order of magnitude above Gamma and stays there, so an absolute ranking
    never changes hands and an absolute brightness sits parked mid-range. This
    tracks each channel's own slow baseline and its typical swing, then reports
    where the channel is RIGHT NOW relative to itself — a burst of Beta reads as
    a burst even though it's numerically small next to Delta.

    Output per channel is 0-1 with 0.5 meaning "at its own baseline"."""

    def __init__(self):
        self._seeded = False
        self.age = 0.0
        self.level = None       # bands, 0-1 relative
        self.slevel = None      # brain-state activations, 0-1 relative

    def reset(self):
        """Forget the baseline — used when a headset drops, so reconnecting
        gets a fresh warm-up (and the white 'connected' hold) rather than
        colours computed against a stale idea of normal."""
        self._seeded = False
        self.age = 0.0

    @property
    def warming(self):
        return (not self._seeded) or self.age < BL_WARMUP_S

    def update(self, bands, hidden, dt, gain):
        b = np.asarray(bands, dtype=float)
        h = np.asarray(hidden, dtype=float)
        if not self._seeded:
            self._base, self._dev = b.copy(), np.full(b.shape, 1e-3)
            self._sbase, self._sdev = h.copy(), np.full(h.shape, 1e-3)
            self.level = np.full(b.shape, 0.5)
            self.slevel = np.full(h.shape, 0.5)
            self.age = 0.0
            self._seeded = True
        self.age += dt
        self._base, self._dev, self.level = self._track(
            b, self._base, self._dev, self.level, dt, gain)
        self._sbase, self._sdev, self.slevel = self._track(
            h, self._sbase, self._sdev, self.slevel, dt, gain)
        return self.level, self.slevel

    @staticmethod
    def _track(x, base, dev, level, dt, gain):
        a_base = 1.0 - math.exp(-dt / BL_BASE_TAU)
        a_dev  = 1.0 - math.exp(-dt / BL_DEV_TAU)
        base = base + (x - base) * a_base
        dev  = dev + (np.abs(x - base) - dev) * a_dev
        # Contrast scale: bigger gain -> a smaller swing fills the whole range.
        scale = np.maximum(dev, 0.004) * (2.2 / max(0.25, gain))
        rel = 0.5 + 0.5 * np.tanh((x - base) / scale)
        # Fast attack, slower release: catch the burst, don't strobe on the way
        # back down.
        k = np.where(rel > level, BL_ATTACK, BL_RELEASE)
        return base, dev, level + (rel - level) * k


def _rank_counts(n):
    """Split n lights across the top three channels — most on the winner, fewer
    on the runner-up, a trailing one on third. 4 lights -> 2 / 1 / 1."""
    if n <= 0:
        return []
    if n <= 3:
        return [1] * n
    n1 = int(round(n * 0.5))
    n2 = int(round(n * 0.3))
    n3 = n - n1 - n2
    if n3 < 0:
        n2, n3 = n2 + n3, 0
    return [n1, n2, n3]


# Rank 1 at full brightness, 2nd and 3rd stepped down so the hierarchy is
# readable across the room without reading the labels.
RANK_DIM = (1.0, 0.72, 0.52)


def _rank_plan(n, scores, colors, names, floor=0.20):
    """Hand out n lights by rank: dominant channel first, then 2nd and 3rd."""
    scores = np.asarray(scores, dtype=float)
    order = list(np.argsort(scores)[::-1])
    out = []
    for rank, count in enumerate(_rank_counts(n)):
        if count <= 0:
            continue
        i = int(order[min(rank, len(order) - 1)])
        level = floor + (1.0 - floor) * float(np.clip(scores[i], 0.0, 1.0))
        col = bl_from_palette(colors[i], level * RANK_DIM[min(rank, len(RANK_DIM) - 1)])
        label = names[i] if rank == 0 else f"{names[i]}#{rank + 1}"
        out.extend([(label, col)] * count)
    return out[:n]


def _even_band_indices(n):
    """Pick n band indices spread as evenly as possible across Delta..Gamma.
    2 lights -> Delta/Gamma, 6 -> one each, more than 6 -> wrap around."""
    nb = len(BANDS)
    if n <= 1:
        return [0]
    if n <= nb:
        return [int(round(i * (nb - 1) / (n - 1))) for i in range(n)]
    return [i % nb for i in range(n)]


def _partition(n_dev, n_src):
    """Map each light to a source index, splitting lights evenly between
    headsets. More headsets than lights -> one headset per light, extras unlit."""
    if n_src <= 0:
        return [0] * n_dev
    if n_src >= n_dev:
        return list(range(n_dev))
    owner, base, extra = [], *divmod(n_dev, n_src)
    for s in range(n_src):
        owner.extend([s] * (base + (1 if s < extra else 0)))
    return owner


def plan_colors(snap, n, mapping, t_now, tracker=None):
    """Decide what n lights should show for one headset.
    Returns a list of (label, device-space RGB) of exactly length n."""
    if n <= 0:
        return []

    # Nothing real coming in — slow amber breathe so a dropped electrode or a
    # dead connection is obvious from across the room.
    if not snap.live or not snap.contact:
        pulse = 0.30 + 0.22 * math.sin(t_now * 2.2)
        return [("no signal", bl_from_palette(NO_SIGNAL_COLOR, pulse))] * n

    # Freshly connected: hold white until the tracker has a baseline to judge
    # "elevated" against. Doubles as the visible "headset is up" confirmation.
    if tracker is None or tracker.warming:
        return [("connected", bl_from_palette(WHITE_COLOR, 0.85))] * n

    bands = np.clip(np.asarray(snap.bands, dtype=float), 0.0, 1.0)
    hidden = np.clip(np.asarray(snap.hidden, dtype=float), 0.0, 1.0)
    b_lvl, s_lvl = tracker.level, tracker.slevel

    if mapping == "rank":
        # Blend absolute size with relative elevation: pure absolute never moves,
        # pure relative promotes whichever band happens to twitch.
        score = 0.35 * bands + 0.65 * b_lvl
        return _rank_plan(n, score, [b[6] for b in BANDS], [b[0] for b in BANDS])

    if mapping == "state":
        score = 0.35 * hidden + 0.65 * s_lvl
        return _rank_plan(n, score, list(TinyMLP.HIDDEN_COLORS),
                          [s.capitalize() for s in TinyMLP.HIDDEN_NAMES])

    if mapping == "spread":
        if n == 1:
            i = int(np.argmax(0.35 * bands + 0.65 * b_lvl))
            return [(BANDS[i][0], bl_from_palette(BANDS[i][6],
                                                  0.15 + 0.85 * float(b_lvl[i])))]
        return [(BANDS[i][0], bl_from_palette(BANDS[i][6],
                                              0.12 + 0.88 * float(b_lvl[i])))
                for i in _even_band_indices(n)]

    if mapping == "mood":
        rgb = np.clip(np.asarray(snap.rgb, dtype=float), 0.0, 1.0)
        peak = float(rgb.max())
        # The net's sigmoid output clusters near mid-grey; normalizing the peak
        # channel to 1 recovers the hue so it actually reads on the hardware.
        if peak > 1e-6:
            rgb = rgb / peak
        col = tuple(int(c * 255) for c in rgb)
        level = 0.40 + 0.60 * float(np.clip(b_lvl.mean() * 1.4, 0.0, 1.0))
        return [("mood", bl_from_palette(col, level))] * n

    if mapping == "metric":
        warm, cool = (255, 90, 40), (40, 140, 255)
        att, med = snap.attention / 100.0, snap.meditation / 100.0
        if n == 1:
            mix = att / max(1e-6, att + med)
            col = tuple(int(w * mix + c * (1 - mix)) for w, c in zip(warm, cool))
            return [("att/med", bl_from_palette(col, 0.20 + 0.80 * max(att, med)))]
        n_att = (n + 1) // 2
        return ([("attn", bl_from_palette(warm, 0.15 + 0.85 * att))] * n_att +
                [("med",  bl_from_palette(cool, 0.15 + 0.85 * med))] * (n - n_att))

    # "emotion" — same palette as the on-screen brain-state bars.
    label, conf = infer_emotion(bands, snap.attention, snap.meditation, snap.contact)
    idx = EMOTION_TO_STATE.get(label)
    col = WHITE_COLOR if idx is None else TinyMLP.HIDDEN_COLORS[idx]
    return [(label.lower(), bl_from_palette(col, 0.25 + 0.75 * conf))] * n


class BusylightBridge:
    """Owns every Busylight on the bus and paints them from live brain state."""

    def __init__(self, enabled=True, mapping="rank"):
        self.devices = []
        self.enabled = enabled
        self.mapping = mapping if mapping in LIGHT_MAPPINGS else LIGHT_MAPPINGS[0]
        self.brightness = 1.0
        self.gain = BL_GAIN
        self.labels = []          # per-light caption for the on-screen strip
        self.colors = []          # per-light device-space colour, for the strip
        self.status = ""
        self._paths = set()
        self._last_write = 0.0
        self._trackers = {}       # headset name -> SignalTracker
        self._was_live = {}       # headset name -> last seen live flag
        if not BUSYLIGHT_AVAILABLE:
            self.status = ("lights unavailable: " +
                           (BUSYLIGHT_IMPORT_ERROR or "hid / busylight_commands not found"))
        else:
            self.discover()

    # --- discovery ---

    def discover(self):
        """Open every Busylight we don't already hold. Safe to call repeatedly."""
        if not BUSYLIGHT_AVAILABLE:
            return 0
        added = 0
        try:
            for pid, model in BL_MODELS.items():
                for info in hid.enumerate(BL_VENDOR_ID, pid):
                    path = info.get('path')
                    if path in self._paths:
                        continue
                    try:
                        self.devices.append(BusylightDevice(info, model))
                        self._paths.add(path)
                        added += 1
                    except Exception as exc:
                        print(f"[busylight] could not open {path}: {exc}")
        except Exception as exc:
            self.status = f"enumerate error: {exc}"
            return added
        self.labels = [""] * len(self.devices)
        self.colors = [(0, 0, 0)] * len(self.devices)
        self._refresh_status()
        return added

    def _refresh_status(self):
        if not self.devices:
            self.status = "no lights found — K to rescan"
        else:
            n_o = sum(1 for d in self.devices if d.model == "Omega")
            n_a = len(self.devices) - n_o
            self.status = f"{len(self.devices)} light(s) · {n_o} Omega / {n_a} Alpha"

    # --- per-frame drive ---

    def update(self, t_now, snapshots):
        """Called every frame; rate-limits itself down to BL_UPDATE_HZ."""
        n = len(self.devices)
        if n == 0:
            return
        if t_now - self._last_write < 1.0 / BL_UPDATE_HZ:
            return
        dt = min(0.5, t_now - self._last_write) if self._last_write else 1.0 / BL_UPDATE_HZ
        self._last_write = t_now

        if len(self.labels) != n:                    # devices changed under us
            self.labels = [""] * n
            self.colors = [(0, 0, 0)] * n

        if not self.enabled:
            for i, dev in enumerate(self.devices):
                self.labels[i], self.colors[i] = "off", (0, 0, 0)
                dev.send_rgb(0, 0, 0)
            return

        snaps = list(snapshots) if snapshots else []
        if not snaps:
            snaps = [LightSnapshot("—", np.zeros(len(BANDS)), 0.0, 0.0,
                                   False, (0.5, 0.5, 0.5), live=False)]

        owner = _partition(n, len(snaps))
        multi = len(snaps) > 1
        for s_idx, snap in enumerate(snaps):
            group = [i for i, o in enumerate(owner) if o == s_idx]
            if not group:
                continue
            tracker = self._tracker_for(snap, dt)
            plan = plan_colors(snap, len(group), self.mapping, t_now, tracker)
            for slot_i, dev_i in enumerate(group):
                label, rgb = plan[slot_i]
                rgb = tuple(int(c * self.brightness) for c in rgb)
                self.labels[dev_i] = f"{_short_name(snap.name)}·{label}" if multi else label
                self.colors[dev_i] = rgb
                self.devices[dev_i].send_rgb(*rgb)

    def _tracker_for(self, snap, dt):
        """One adaptive tracker per headset, advanced once per update.

        A dead stream resets the baseline so a reconnect gets a fresh warm-up
        (and the white hold). A momentary lead-off does not — contact flapping
        shouldn't throw away a baseline that took ten seconds to build."""
        tr = self._trackers.get(snap.name)
        if tr is None:
            tr = self._trackers[snap.name] = SignalTracker()
        if not snap.live:
            tr.reset()
            self._was_live[snap.name] = False
            return tr
        if not self._was_live.get(snap.name, False):
            tr.reset()                       # first frame of a new connection
            self._was_live[snap.name] = True
        if snap.contact:
            tr.update(snap.bands, snap.hidden, dt, self.gain)
        return tr

    # --- controls / teardown ---

    def cycle_mapping(self, step=1):
        i = LIGHT_MAPPINGS.index(self.mapping)
        self.mapping = LIGHT_MAPPINGS[(i + step) % len(LIGHT_MAPPINGS)]
        return self.mapping

    def nudge_brightness(self, delta):
        self.brightness = max(0.1, min(1.0, self.brightness + delta))
        self._last_write = 0.0                       # let the change land now
        for d in self.devices:                       # defeat the delta filter
            d.current_rgb = (-99, -99, -99)
        return self.brightness

    def nudge_gain(self, delta):
        """Sensitivity: how much of a swing it takes to drive a light end to
        end. Higher = twitchier."""
        self.gain = max(0.3, min(4.0, self.gain + delta))
        return self.gain

    def toggle(self):
        self.enabled = not self.enabled
        self._last_write = 0.0
        if not self.enabled:
            for d in self.devices:
                d.turn_off()
        return self.enabled

    def close(self):
        for d in self.devices:
            d.close()
        self.devices, self._paths = [], set()


def _short_name(name):
    """Trim an FC11-XXXXXX advertisement name down to something that fits."""
    name = name or "?"
    return name if len(name) <= 9 else name[-8:]


def draw_light_strip(screen, rect, bridge, fonts):
    """Bottom strip: one dot per Busylight, showing exactly what the hardware is
    being told to display, plus mapping / brightness state."""
    f_ui, f_ui_sm, f_mono = fonts
    pygame.draw.rect(screen, (15, 10, 10), rect, border_radius=6)
    pygame.draw.rect(screen, BORDER, rect, 1, border_radius=6)

    x = rect.x + 12
    head = "BUSYLIGHTS" if bridge.enabled else "BUSYLIGHTS · OFF"
    screen.blit(f_ui_sm.render(head, True, ACCENT if bridge.enabled else TEXT_FAINT),
                (x, rect.y + 8))
    screen.blit(f_ui_sm.render(bridge.status, True, TEXT_DIM), (x, rect.y + 24))
    screen.blit(f_mono.render(
        f"map {bridge.mapping.upper()} · {bridge.brightness * 100:.0f}%"
        f" · {bridge.gain:.2f}x  ·  L M K [ ] , .",
        True, TEXT_FAINT), (x, rect.y + 41))

    # Dots are right-aligned so the caption block never collides with them; if
    # there are more lights than fit, the overflow is counted rather than clipped.
    spacing, text_end = 54, x + 210
    room = max(0, (rect.right - 12 - text_end) // spacing)
    shown = bridge.devices[:room]
    dot_x = rect.right - 12 - len(shown) * spacing
    for i, dev in enumerate(shown):
        col = bl_to_screen(*(bridge.colors[i] if i < len(bridge.colors) else (0, 0, 0)))
        cx, cy = dot_x + spacing // 2, rect.y + 32
        # faint halo so a lit light reads as lit even at low brightness
        halo = pygame.Surface((44, 44), pygame.SRCALPHA)
        pygame.draw.circle(halo, (*col, 60), (22, 22), 19)
        screen.blit(halo, (cx - 22, cy - 22))
        pygame.draw.circle(screen, col, (cx, cy), 12)
        pygame.draw.circle(screen, BORDER, (cx, cy), 12, 1)
        t2 = f_ui_sm.render(dev.model, True, TEXT_FAINT)
        screen.blit(t2, (cx - t2.get_width() // 2, rect.y + 4))
        lab = (bridge.labels[i] if i < len(bridge.labels) else "")[:12]
        t = f_ui_sm.render(lab, True, TEXT_DIM)
        screen.blit(t, (cx - t.get_width() // 2, cy + 15))
        dot_x += spacing
    if len(shown) < len(bridge.devices):
        screen.blit(f_ui_sm.render(f"+{len(bridge.devices) - len(shown)} more",
                                   True, TEXT_FAINT), (text_end, rect.y + 26))


def build_snapshots(mode, state, bands_norm, rgb, hidden, mlp_bl, multi_mgr, sdk_src):
    """Flatten whatever is currently streaming into LightSnapshots — one per
    headset in MULTI mode, one overall otherwise."""
    if mode == "multi" and multi_mgr is not None and multi_mgr.order:
        snaps = []
        for name in multi_mgr.order:
            slot = multi_mgr.slots.get(name)
            if slot is None:
                continue
            with slot.state.lock:
                raw = slot.state.bands.copy()
                att, med = slot.state.attention, slot.state.meditation
                con = slot.state.contact
            nb = np.clip(raw / BAND_MAX_SDK, 0.0, 1.0)
            # A private MLP instance: the on-screen net viz reads mlp.last_*,
            # and we don't want the lights overwriting what it's showing.
            srgb, shid = mlp_bl.forward(nb)
            snaps.append(LightSnapshot(name, nb, att, med, con, tuple(srgb),
                                       hidden=shid.copy(), live=slot.streaming))
        if snaps:
            return snaps

    with state.lock:
        att, med, con = state.attention, state.meditation, state.contact
    if mode == "sdk":
        live = bool(sdk_src and sdk_src.streaming)
        name = (sdk_src.device_name if sdk_src and sdk_src.device_name else "headset")
    elif mode == "multi":
        live, name = False, "waiting"
    else:
        live, name = True, mode           # synth/manual always "live"
    return [LightSnapshot(name, bands_norm, att, med, con, tuple(rgb),
                          hidden=np.asarray(hidden).copy(), live=live)]


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------
class Slider:
    def __init__(self, x, y, w, label, value=0.4, color=ACCENT):
        self.rect = pygame.Rect(x, y, w, 8)
        self.label = label
        self.value = value
        self.color = color
        self.dragging = False

    def handle(self, event):
        hit = self.rect.inflate(0, 22)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and hit.collidepoint(event.pos):
            self.dragging = True; self._set_from(event.pos[0])
        elif event.type == pygame.MOUSEBUTTONUP:
            self.dragging = False
        elif event.type == pygame.MOUSEMOTION and self.dragging:
            self._set_from(event.pos[0])

    def _set_from(self, mx):
        self.value = max(0.0, min(1.0, (mx - self.rect.x) / self.rect.w))

    def draw(self, surf, font):
        surf.blit(font.render(self.label, True, TEXT), (self.rect.x, self.rect.y - 17))
        surf.blit(font.render(f"{self.value*100:5.1f}", True, TEXT_DIM),
                  (self.rect.right - 36, self.rect.y - 17))
        pygame.draw.rect(surf, (28, 18, 18), self.rect, border_radius=4)
        fw = int(self.rect.w * self.value)
        if fw > 1:
            pygame.draw.rect(surf, self.color,
                             pygame.Rect(self.rect.x, self.rect.y, fw, self.rect.h),
                             border_radius=4)
        kx = self.rect.x + fw
        pygame.draw.circle(surf, (240, 235, 230), (kx, self.rect.centery), 7)
        pygame.draw.circle(surf, self.color, (kx, self.rect.centery), 5)


class Button:
    def __init__(self, label, active=False):
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.label = label
        self.active = active
        self.hover = False

    def set_rect(self, x, y, w, h):
        self.rect = pygame.Rect(x, y, w, h)

    def handle(self, event):
        if event.type == pygame.MOUSEMOTION:
            self.hover = self.rect.collidepoint(event.pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.rect.collidepoint(event.pos):
            return True
        return False

    def draw(self, surf, font):
        if self.active:
            bg, brd, fg = ACCENT_DIM, ACCENT, TEXT
        elif self.hover:
            bg, brd, fg = (44, 26, 26), BORDER, TEXT
        else:
            bg, brd, fg = (24, 16, 16), BORDER, TEXT_DIM
        pygame.draw.rect(surf, bg, self.rect, border_radius=4)
        pygame.draw.rect(surf, brd, self.rect, 1, border_radius=4)
        t = font.render(self.label, True, fg)
        surf.blit(t, t.get_rect(center=self.rect.center))


class Toggle:
    """Compact two-state toggle for pattern selection."""
    def __init__(self, label, on=False):
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.label = label
        self.on = on
        self.hover = False

    def set_rect(self, x, y, w, h):
        self.rect = pygame.Rect(x, y, w, h)

    def handle(self, event):
        if event.type == pygame.MOUSEMOTION:
            self.hover = self.rect.collidepoint(event.pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.rect.collidepoint(event.pos):
            self.on = not self.on
            return True
        return False

    def draw(self, surf, font):
        if self.on:
            bg, brd, fg = (60, 18, 18), ACCENT, TEXT
        elif self.hover:
            bg, brd, fg = (28, 20, 20), BORDER, TEXT
        else:
            bg, brd, fg = (20, 14, 14), (40, 26, 26), TEXT_DIM
        pygame.draw.rect(surf, bg, self.rect, border_radius=4)
        pygame.draw.rect(surf, brd, self.rect, 1, border_radius=4)
        cy = self.rect.centery
        dx = self.rect.x + 10
        if self.on:
            pygame.draw.circle(surf, ACCENT, (dx, cy), 4)
            pygame.draw.circle(surf, (255, 230, 230), (dx, cy), 1)
        else:
            pygame.draw.circle(surf, (50, 35, 35), (dx, cy), 4)
        surf.blit(font.render(self.label, True, fg), (dx + 12, cy - font.get_height() // 2))


# ---------------------------------------------------------------------------
# Pattern: Mandala
# ---------------------------------------------------------------------------
def draw_mandala(screen, ctx):
    cx, cy   = ctx["center"]
    band_norm = ctx["bands"]
    rgb       = ctx["rgb"]
    t         = ctx["t"]
    R         = ctx["radius"]
    base_r, ring_step = int(R * 0.18), int(R * 0.13)

    rings = []
    for i, b in enumerate(BANDS):
        ring_idx = 5 - i
        rings.append({
            "i": i, "r": base_r + ring_idx * ring_step,
            "amp": float(band_norm[i]), "n": b[4],
            "rot": t * (b[3] / 30.0) * 0.35,
            "freq_norm": b[3] / 56.0,
        })
    rings.sort(key=lambda x: -x["r"])

    for ring in rings:
        rr = max(0, min(1, rgb[0] + ring["freq_norm"] * 0.20 - 0.05))
        gg = max(0, min(1, rgb[1] + 0.05))
        bb = max(0, min(1, rgb[2] + (1 - ring["freq_norm"]) * 0.20 - 0.05))
        col_core = (int(rr*255), int(gg*255), int(bb*255))
        alpha = int(60 + ring["amp"] * 180)
        psize = max(2, int(5 + ring["amp"] * 16))

        ring_a = int(15 + ring["amp"] * 50)
        rsurf = pygame.Surface((ring["r"]*2 + 8, ring["r"]*2 + 8), pygame.SRCALPHA)
        pygame.draw.circle(rsurf, (*col_core, ring_a),
                           (ring["r"]+4, ring["r"]+4), ring["r"], 1)
        screen.blit(rsurf, (cx - ring["r"] - 4, cy - ring["r"] - 4))

        halo_r = int(psize * 1.9)
        halo_d = halo_r * 2
        halo = pygame.Surface((halo_d, halo_d), pygame.SRCALPHA)
        pygame.draw.circle(halo, (*col_core, max(0, alpha // 3)),
                           (halo_r, halo_r), halo_r)

        core_d = psize * 2 + 2
        core = pygame.Surface((core_d, core_d), pygame.SRCALPHA)
        pygame.draw.circle(core, (*col_core, alpha), (psize+1, psize+1), psize)
        pygame.draw.circle(core, (255, 255, 255, min(255, alpha + 60)),
                           (psize+1, psize+1), max(1, psize // 4))

        for p in range(ring["n"]):
            ang = (p / ring["n"]) * 2 * math.pi + ring["rot"]
            px = cx + math.cos(ang) * ring["r"]
            py = cy + math.sin(ang) * ring["r"]
            screen.blit(halo, (px - halo_r, py - halo_r),
                        special_flags=pygame.BLEND_ADD)
            screen.blit(core, (px - psize - 1, py - psize - 1))

    # flower of life seed
    overall = float(np.mean(band_norm))
    seed_r = (R * 0.05 + overall * 6) * (1.0 + 0.08 * math.sin(t * 2.0))
    seed_col = (int(rgb[0]*255), int(rgb[1]*255), int(rgb[2]*255), 130)
    side = int(seed_r * 6)
    sseed = pygame.Surface((side, side), pygame.SRCALPHA)
    sx = sy = side // 2
    pygame.draw.circle(sseed, seed_col, (sx, sy), int(seed_r), 2)
    for k in range(6):
        a = (k / 6) * 2 * math.pi + t * 0.15
        px = sx + math.cos(a) * seed_r
        py = sy + math.sin(a) * seed_r
        pygame.draw.circle(sseed, seed_col, (int(px), int(py)), int(seed_r), 2)
    screen.blit(sseed, (cx - side // 2, cy - side // 2))


# ---------------------------------------------------------------------------
# Pattern: Sri Yantra
# ---------------------------------------------------------------------------
def draw_sri_yantra(screen, ctx):
    cx, cy   = ctx["center"]
    band_norm = ctx["bands"]
    rgb       = ctx["rgb"]
    t         = ctx["t"]
    R         = ctx["radius"]
    base_col  = (int(rgb[0]*255), int(rgb[1]*255), int(rgb[2]*255))

    side = int(R * 2.2)
    overlay = pygame.Surface((side, side), pygame.SRCALPHA)
    ox = oy = side // 2

    # Outer enclosing rings (lotus)
    pygame.draw.circle(overlay, (*base_col, 70), (ox, oy), int(R * 0.95), 1)
    pygame.draw.circle(overlay, (*base_col, 40), (ox, oy), int(R * 0.85), 1)

    # 9 triangles, alternating up/down, scaled by band amps
    n = 9
    for k in range(n):
        amp = float(band_norm[k % 6])
        scale = R * (0.30 + 0.65 * (n - k) / n) * (0.85 + amp * 0.30)
        pointing_up = (k % 2 == 0)
        rot = t * (0.04 if pointing_up else -0.04) + k * 0.05
        verts = []
        for v in range(3):
            ang = rot + v * 2 * math.pi / 3 + (0 if pointing_up else math.pi)
            verts.append((ox + math.cos(ang - math.pi / 2) * scale,
                          oy + math.sin(ang - math.pi / 2) * scale))
        alpha = int(50 + amp * 120)
        rr = min(255, int(base_col[0] * 0.7 + (k % 2) * 60))
        gg = min(255, int(base_col[1] * 0.7 + 30))
        bb = min(255, int(base_col[2] * 0.7 + ((k + 1) % 2) * 60))
        pygame.draw.polygon(overlay, (rr, gg, bb, alpha), verts, 1)

    # bindu - the central seed point
    overall = float(np.mean(band_norm))
    bindu_r = int(3 + overall * 6)
    pygame.draw.circle(overlay, (*base_col, 240), (ox, oy), bindu_r)
    pygame.draw.circle(overlay, (255, 255, 255, 200), (ox, oy), max(1, bindu_r // 2))

    screen.blit(overlay, (cx - ox, cy - oy), special_flags=pygame.BLEND_ADD)


# ---------------------------------------------------------------------------
# Pattern: Metatron's Cube
# ---------------------------------------------------------------------------
def draw_metatron(screen, ctx):
    cx, cy   = ctx["center"]
    band_norm = ctx["bands"]
    rgb       = ctx["rgb"]
    t         = ctx["t"]
    R         = ctx["radius"]
    base = (int(rgb[0]*255), int(rgb[1]*255), int(rgb[2]*255))

    overall = float(np.mean(band_norm))
    spacing = R * 0.30 * (0.9 + overall * 0.2)
    rot = t * 0.05

    pts = [(cx, cy)]
    for i in range(6):
        a = i * math.pi / 3 + rot
        pts.append((cx + math.cos(a) * spacing, cy + math.sin(a) * spacing))
    for i in range(6):
        a = i * math.pi / 3 + rot * 0.5
        pts.append((cx + math.cos(a) * spacing * 2, cy + math.sin(a) * spacing * 2))

    overlay = pygame.Surface((screen.get_width(), screen.get_height()), pygame.SRCALPHA)
    for i, p1 in enumerate(pts):
        for j in range(i + 1, len(pts)):
            p2 = pts[j]
            band_idx = (i + j) % 6
            amp = float(band_norm[band_idx])
            alpha = int(8 + amp * 70)
            pygame.draw.line(overlay, (*base, alpha),
                             (int(p1[0]), int(p1[1])),
                             (int(p2[0]), int(p2[1])), 1)
    for i, p in enumerate(pts):
        amp = float(band_norm[i % 6])
        size = int(R * 0.05 * (0.7 + amp))
        alpha = int(80 + amp * 160)
        pygame.draw.circle(overlay, (*base, alpha),
                           (int(p[0]), int(p[1])), size, 1)
        pygame.draw.circle(overlay, (*base, min(255, alpha + 60)),
                           (int(p[0]), int(p[1])), max(1, size // 3))
    screen.blit(overlay, (0, 0))


# ---------------------------------------------------------------------------
# Pattern: Golden Spiral
# ---------------------------------------------------------------------------
def draw_spiral(screen, ctx):
    cx, cy   = ctx["center"]
    band_norm = ctx["bands"]
    t         = ctx["t"]
    R         = ctx["radius"]

    overall = float(np.mean(band_norm))
    n_pts = int(140 + overall * 80)
    rotation = t * 0.08
    growth = R / 22 * (0.85 + overall * 0.30)
    overlay = pygame.Surface((screen.get_width(), screen.get_height()), pygame.SRCALPHA)

    for i in range(n_pts):
        ang = i * (2 * math.pi / (PHI ** 2)) + rotation
        r = math.sqrt(i) * growth
        if r > R * 1.05:
            continue
        x = cx + math.cos(ang) * r
        y = cy + math.sin(ang) * r

        band_idx = i % 6
        amp = float(band_norm[band_idx])
        col = BANDS[band_idx][6]
        size = max(1, int(2 + amp * 7 * (0.5 + r / R)))
        alpha = int(60 + amp * 170)

        d = size * 2 + 2
        blob = pygame.Surface((d, d), pygame.SRCALPHA)
        pygame.draw.circle(blob, (*col, alpha), (size + 1, size + 1), size)
        pygame.draw.circle(blob, (255, 255, 255, min(255, alpha + 80)),
                           (size + 1, size + 1), max(1, size // 3))
        overlay.blit(blob, (int(x - size - 1), int(y - size - 1)))

    screen.blit(overlay, (0, 0), special_flags=pygame.BLEND_ADD)


# ---------------------------------------------------------------------------
# Pattern: Lissajous
# ---------------------------------------------------------------------------
def draw_lissajous(screen, ctx):
    cx, cy   = ctx["center"]
    band_norm = ctx["bands"]
    t         = ctx["t"]
    R         = ctx["radius"]

    overlay = pygame.Surface((screen.get_width(), screen.get_height()), pygame.SRCALPHA)
    pairs = [(0, 3), (1, 4), (2, 5)]   # delta-lowbeta, theta-highbeta, alpha-gamma

    for idx, (i, j) in enumerate(pairs):
        ai = max(1, round(BANDS[i][3] / 4.0))
        bj = max(1, round(BANDS[j][3] / 4.0))
        amp = (float(band_norm[i]) + float(band_norm[j])) * 0.5
        if amp < 0.05:
            continue
        scale = R * (0.55 + 0.30 * idx) * (0.4 + amp * 0.8)
        phase = t * (0.6 + 0.2 * idx)
        col = BANDS[j][6]
        alpha = int(60 + amp * 180)

        n_steps = 220
        pts = []
        for k in range(n_steps + 1):
            tk = (k / n_steps) * 2 * math.pi
            x = cx + math.sin(ai * tk + phase) * scale
            y = cy + math.sin(bj * tk) * scale * 0.9
            pts.append((x, y))
        if len(pts) > 2:
            pygame.draw.aalines(overlay, (*col, alpha), True, pts)

    screen.blit(overlay, (0, 0), special_flags=pygame.BLEND_ADD)


# ---------------------------------------------------------------------------
# Pattern: Formless (flow-field particle cloud)
# ---------------------------------------------------------------------------
class FormlessField:
    def __init__(self, n=110):
        self.n = n
        self.x  = np.random.uniform(-1, 1, n)
        self.y  = np.random.uniform(-1, 1, n)
        self.vx = np.random.uniform(-0.05, 0.05, n)
        self.vy = np.random.uniform(-0.05, 0.05, n)
        self.phase = np.random.uniform(0, 2 * math.pi, n)
        self.band  = np.random.randint(0, 6, n)

    def step(self, band_norm, t):
        chaos = (band_norm[5] + band_norm[4]) * 0.5
        flow  = (band_norm[2] + band_norm[1]) * 0.5
        slow  = float(band_norm[0])
        for i in range(self.n):
            nx = (math.sin(self.x[i] * 2.7 + t * 0.5 + self.phase[i]) +
                  math.cos(self.y[i] * 3.1 - t * 0.3))
            ny = (math.cos(self.x[i] * 2.3 + t * 0.4) +
                  math.sin(self.y[i] * 2.9 + t * 0.6 + self.phase[i]))
            self.vx[i] += nx * chaos * 0.005 - self.x[i] * slow * 0.005 - self.x[i] * 0.001
            self.vy[i] += ny * chaos * 0.005 - self.y[i] * slow * 0.005 - self.y[i] * 0.001
            damp = 0.96 - flow * 0.05
            self.vx[i] *= damp
            self.vy[i] *= damp
            self.x[i] += self.vx[i]
            self.y[i] += self.vy[i]
            if self.x[i] >  1.2: self.x[i], self.vx[i] = -1.2, -self.vx[i] * 0.5
            if self.x[i] < -1.2: self.x[i], self.vx[i] =  1.2, -self.vx[i] * 0.5
            if self.y[i] >  1.2: self.y[i], self.vy[i] = -1.2, -self.vy[i] * 0.5
            if self.y[i] < -1.2: self.y[i], self.vy[i] =  1.2, -self.vy[i] * 0.5


def draw_formless(screen, ctx):
    cx, cy   = ctx["center"]
    band_norm = ctx["bands"]
    t         = ctx["t"]
    R         = ctx["radius"]
    field     = ctx["formless"]
    field.step(band_norm, t)

    overall = float(np.mean(band_norm))
    for i in range(field.n):
        x = int(cx + field.x[i] * R * 0.95)
        y = int(cy + field.y[i] * R * 0.95)
        b_idx = field.band[i]
        amp = float(band_norm[b_idx])
        col = BANDS[b_idx][6]
        size = int(R * 0.035 * (0.6 + amp + overall * 0.5))
        if size < 2:
            continue
        alpha = int(20 + amp * 70)
        d = size * 2
        blob = pygame.Surface((d, d), pygame.SRCALPHA)
        pygame.draw.circle(blob, (*col, alpha), (size, size), size)
        pygame.draw.circle(blob, (*col, min(255, alpha + 80)),
                           (size, size), max(1, size // 3))
        screen.blit(blob, (x - size, y - size), special_flags=pygame.BLEND_ADD)


# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------
PATTERNS: List[Tuple[str, Callable]] = [
    ("Mandala",    draw_mandala),
    ("Sri Yantra", draw_sri_yantra),
    ("Metatron",   draw_metatron),
    ("Spiral",     draw_spiral),
    ("Lissajous",  draw_lissajous),
    ("Formless",   draw_formless),
]


# ---------------------------------------------------------------------------
# Sub-views: NN viz + EEG waveform
# ---------------------------------------------------------------------------
def draw_nn(screen, rect, mlp, font):
    overlay = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
    pad_l, pad_r, pad_t, pad_b = 60, 60, 26, 18
    n_in, n_hid, n_out = 6, 4, 3
    in_x, hid_x, out_x = pad_l, rect.w // 2, rect.w - pad_r
    in_ys  = [int(pad_t + i * (rect.h - pad_t - pad_b) / (n_in - 1)) for i in range(n_in)]
    hid_ys = [int(pad_t + 18 + i * (rect.h - pad_t - pad_b - 36) / (n_hid - 1)) for i in range(n_hid)]
    out_ys = [int(pad_t + 36 + i * (rect.h - pad_t - pad_b - 72) / (n_out - 1)) for i in range(n_out)]

    for i in range(n_in):
        for j in range(n_hid):
            w = mlp.W1[j, i]
            act = mlp.last_input[i] * abs(w)
            a = int(min(255, 25 + act * 220))
            col = (220, 90, 90, a) if w > 0 else (90, 130, 220, a)
            pygame.draw.line(overlay, col, (in_x, in_ys[i]), (hid_x, hid_ys[j]), 1)
    for i in range(n_hid):
        for j in range(n_out):
            w = mlp.W2[j, i]
            act = mlp.last_hidden[i] * abs(w)
            a = int(min(255, 25 + act * 220))
            col = (220, 90, 90, a) if w > 0 else (90, 130, 220, a)
            pygame.draw.line(overlay, col, (hid_x, hid_ys[i]), (out_x, out_ys[j]), 1)
    screen.blit(overlay, rect.topleft)

    for i in range(n_in):
        act = mlp.last_input[i]
        c = int(60 + act * 195)
        cx, cy = rect.x + in_x, rect.y + in_ys[i]
        pygame.draw.circle(screen, (c, c, c), (cx, cy), 5)
        pygame.draw.circle(screen, BORDER, (cx, cy), 5, 1)
        screen.blit(font.render(BANDS[i][0][:3], True, TEXT_DIM), (cx - 32, cy - 7))
    for i in range(n_hid):
        act = mlp.last_hidden[i]
        base = TinyMLP.HIDDEN_COLORS[i]
        col = tuple(int(50 + (cv - 50) * act) for cv in base)
        cx, cy = rect.x + hid_x, rect.y + hid_ys[i]
        pygame.draw.circle(screen, col, (cx, cy), 8)
        pygame.draw.circle(screen, BORDER, (cx, cy), 8, 1)
        lbl = font.render(TinyMLP.HIDDEN_NAMES[i], True, TEXT_DIM)
        screen.blit(lbl, (cx - lbl.get_width() // 2, cy + 12))
    out_lbls = ["R", "G", "B"]
    out_base = [(255, 80, 80), (80, 220, 100), (80, 130, 240)]
    for i in range(n_out):
        act = mlp.last_output[i]
        col = tuple(int(40 + (cv - 40) * act) for cv in out_base[i])
        cx, cy = rect.x + out_x, rect.y + out_ys[i]
        pygame.draw.circle(screen, col, (cx, cy), 8)
        pygame.draw.circle(screen, BORDER, (cx, cy), 8, 1)
        screen.blit(font.render(out_lbls[i], True, TEXT_DIM), (cx + 14, cy - 7))

    r, g, b = mlp.last_output
    sw = pygame.Rect(rect.right - 36, rect.centery - 14, 28, 28)
    pygame.draw.rect(screen, (int(r*255), int(g*255), int(b*255)), sw, border_radius=4)
    pygame.draw.rect(screen, BORDER, sw, 1, border_radius=4)


def draw_eeg(screen, rect, state):
    pygame.draw.rect(screen, (15, 10, 10), rect, border_radius=4)
    pygame.draw.rect(screen, BORDER, rect, 1, border_radius=4)
    with state.lock:
        buf = state.eeg_buffer
        wi  = state.eeg_write
        n   = len(buf)
    cy = rect.centery
    h2 = rect.h / 2 - 4
    step = rect.w / n
    pygame.draw.line(screen, (40, 25, 25), (rect.x, cy), (rect.right, cy), 1)
    pts = []
    for i in range(0, n, 2):
        idx = (wi + i) % n
        x = rect.x + i * step
        y = cy - (buf[idx] / 150.0) * h2
        pts.append((x, y))
    if len(pts) > 1:
        col = ACCENT if state.contact else (130, 120, 60)
        pygame.draw.aalines(screen, col, False, pts)


def draw_multi_grid(screen, area, manager, mlp, t_anim, fonts):
    """Draw a grid of headset tiles in MULTI mode. Each tile: a small mandala
    (driven by that headset's own bands), the device name, connection status,
    and attention/meditation/contact. Grid auto-sizes to the number of headsets."""
    f_serif_md, f_ui, f_ui_sm, f_mono = fonts
    x0, y0, w, h = area
    order = list(manager.order)

    if not order:
        msg = f_ui.render(
            "No headsets yet. Put ONE headset in pairing mode (blue blink),",
            True, TEXT_DIM)
        msg2 = f_ui.render(
            "let it connect, then pair the next. All stream together.",
            True, TEXT_DIM)
        screen.blit(msg,  (x0 + w // 2 - msg.get_width() // 2,  y0 + h // 2 - 18))
        screen.blit(msg2, (x0 + w // 2 - msg2.get_width() // 2, y0 + h // 2 + 2))
        return

    n = len(order)
    cols = 1 if n == 1 else (2 if n <= 4 else 3)
    rows = (n + cols - 1) // cols
    gap = 14
    tile_w = (w - gap * (cols - 1)) // cols
    tile_h = (h - gap * (rows - 1)) // rows

    for idx, name in enumerate(order):
        slot = manager.slots.get(name)
        if slot is None:
            continue
        c = idx % cols
        r = idx // cols
        tx = x0 + c * (tile_w + gap)
        ty = y0 + r * (tile_h + gap)

        # Tile frame
        pygame.draw.rect(screen, (16, 11, 11),
                         pygame.Rect(tx, ty, tile_w, tile_h), border_radius=6)
        border_col = ACCENT if slot.streaming else BORDER
        pygame.draw.rect(screen, border_col,
                         pygame.Rect(tx, ty, tile_w, tile_h), 1, border_radius=6)

        # Read this headset's state
        with slot.state.lock:
            raw = slot.state.bands.copy()
            att = slot.state.attention
            med = slot.state.meditation
            con = slot.state.contact
        nb = np.clip(raw / BAND_MAX_SDK, 0.0, 1.0)
        rgb, _ = mlp.forward(nb)

        # Mini mandala centered in the upper part of the tile
        m_cx = tx + tile_w // 2
        m_cy = ty + int(tile_h * 0.42)
        m_r = int(min(tile_w, tile_h) * 0.32)
        draw_mandala(screen, {
            "center": (m_cx, m_cy), "radius": m_r,
            "bands": nb, "rgb": rgb, "t": t_anim, "formless": None,
        })

        # Header: name + status
        screen.blit(f_ui.render(name, True, TEXT), (tx + 10, ty + 8))
        st_col = (110, 200, 120) if slot.streaming else (210, 170, 90)
        screen.blit(f_ui_sm.render(slot.status, True, st_col), (tx + 10, ty + 26))

        # Footer metrics
        emo, conf = infer_emotion(nb, att, med, con)
        fy = ty + tile_h - 44
        screen.blit(f_mono.render(
            f"att {att:5.1f}  med {med:5.1f}", True, TEXT_DIM), (tx + 10, fy))
        cc = (110, 200, 120) if con else (210, 150, 80)
        pygame.draw.circle(screen, cc, (tx + 15, fy + 24), 4)
        screen.blit(f_ui_sm.render(
            f"{'contact' if con else 'lead-off'} · {emo}", True, TEXT_DIM),
            (tx + 26, fy + 17))



def compute_layout(w, h, strip=0):
    """`strip` is the height reserved at the bottom for the Busylight strip; it
    is 0 when no lights are connected, so the layout is unchanged from v2."""
    panel_x  = 22
    panel_w  = 282
    right_w  = min(360, max(280, w - panel_x - panel_w - 60))
    right_x  = w - right_w - 22
    center_x = (panel_x + panel_w + right_x) // 2
    center_y = (h - strip) // 2 - 14
    radius = max(140, min(center_x - panel_x - panel_w - 20,
                          right_x - center_x - 20,
                          (h - 80 - strip) // 2))
    return dict(panel_x=panel_x, panel_w=panel_w,
                right_x=right_x, right_w=right_w,
                center_x=center_x, center_y=center_y, radius=radius,
                strip=strip)


def position_widgets(layout, sliders, mode_btns, pattern_toggles, scan_toggle=None):
    px, pw = layout["panel_x"], layout["panel_w"]
    n_btns = max(1, len(mode_btns))
    gap = 6
    btn_w = (pw - gap * (n_btns - 1)) // n_btns
    for i, b in enumerate(mode_btns):
        b.set_rect(px + i * (btn_w + gap), 110, btn_w, 30)
    # Scan toggle sits on the status line, right-aligned in the panel.
    if scan_toggle is not None:
        stw = 96
        scan_toggle.set_rect(px + pw - stw, 146, stw, 22)
    tw = (pw - 8) // 2
    th = 28
    for i, t in enumerate(pattern_toggles):
        col = i % 2
        row = i // 2
        t.set_rect(px + col * (tw + 8), 200 + row * (th + 6), tw, th)
    sy = 320
    for s in sliders:
        s.rect = pygame.Rect(px, sy, pw, 8)
        sy += 44


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    pygame.init()

    # Auto-size to ~85% of desktop, with min/max caps
    info = pygame.display.Info()
    dw, dh = info.current_w, info.current_h
    W = max(MIN_W, min(1400, int(dw * 0.85)))
    H = max(MIN_H, min(860,  int(dh * 0.85)))

    pygame.display.set_caption("Crimson Mandala — Beta POC v2")
    screen = pygame.display.set_mode((W, H), pygame.RESIZABLE)
    clock = pygame.time.Clock()

    def sysfont(*names, size=14, bold=False, italic=False):
        for n in names:
            f = pygame.font.match_font(n, bold=bold, italic=italic)
            if f: return pygame.font.Font(f, size)
        return pygame.font.Font(None, size + 2)

    f_serif_big = sysfont("georgia", "times new roman", size=40)
    f_serif_md  = sysfont("georgia", "times new roman", size=24)
    f_ui        = sysfont("helvetica", "arial", size=13)
    f_ui_sm     = sysfont("helvetica", "arial", size=11)
    f_mono      = sysfont("menlo", "consolas", "courier", size=12)

    state = BrainState()
    mlp   = TinyMLP()
    formless_field = FormlessField(n=110)

    # Busylights. Discovery happens once here; K rescans for units plugged in
    # later. mlp_bl is a second copy of the colour net so per-headset colour
    # computation for the lights doesn't clobber what the NN panel is drawing.
    bridge = BusylightBridge(enabled=True, mapping="rank")
    mlp_bl = TinyMLP()

    def strip_h():
        return BL_STRIP_H if bridge.devices else 0

    synth_src = SyntheticSource(state)
    sdk_src   = SDKSource(state) if SDK_AVAILABLE else None
    multi_mgr = MultiHeadsetManager() if SDK_AVAILABLE else None

    btn_synth  = Button("SYNTH")
    btn_manual = Button("MANUAL")
    btn_sdk    = Button("SDK", True)
    btn_multi  = Button("MULTI")
    mode_btns  = [btn_synth, btn_manual, btn_sdk, btn_multi]

    pattern_toggles = [Toggle(name, on=(i == 0))
                       for i, (name, _) in enumerate(PATTERNS)]

    # Auto-scan toggle. ON = keep scanning every few seconds for headsets
    # (default). OFF = stop scanning (freeze current connections, no rescans).
    scan_toggle = Toggle("Auto-scan", on=True)

    sliders = [Slider(0, 0, 100, b[0], 0.4, b[6]) for b in BANDS]

    layout = compute_layout(W, H, strip_h())
    position_widgets(layout, sliders, mode_btns, pattern_toggles, scan_toggle)

    slider_values = [s.value for s in sliders]
    manual_src = ManualSource(state, slider_values)

    # Default to SDK mode so the app tries to connect to the headset on launch.
    # If the SDK isn't available, fall back to SYNTH so the app is still usable.
    if SDK_AVAILABLE and sdk_src is not None:
        mode = "sdk"
        try:
            sdk_src.start_scan()   # begin looking for the headset immediately
        except Exception as exc:
            print(f"[sdk] auto-scan failed: {exc}")
        print("[startup] SDK mode: scanning for headset (LED must blink blue).")
        print("[startup] Press 1 for SYNTH or 2 for MANUAL if you just want a demo.")
    else:
        mode = "synth"
        btn_sdk.active = False
        btn_synth.active = True
        print("[startup] SDK unavailable — starting in SYNTH mode.")
        if SDK_IMPORT_ERROR:
            print(f"[startup]   reason: {SDK_IMPORT_ERROR}")

    # Data export: writes .log + .csv next to this script, and echoes a live
    # readout to the terminal. Rate-limited to ~5 Hz.
    exporter = DataExporter(terminal=True, hz=5.0)
    print(f"[startup] Exporting readings to:\n"
          f"          {exporter.log_path}\n          {exporter.csv_path}\n")
    print(f"[busylight] {bridge.status}")
    if bridge.devices:
        print(f"[busylight] mapping '{bridge.mapping}' — "
              f"L toggles, M cycles, K rescans, [ / ] dim.")

    light_count = len(bridge.devices)

    is_fullscreen = False
    windowed_w, windowed_h = W, H
    last_t = time.time()
    start_t = last_t
    running = True

    while running:
        now = time.time()
        dt  = min(0.1, now - last_t)
        last_t = now
        t_anim = now - start_t

        # --------- events ---------
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.VIDEORESIZE:
                new_w, new_h = max(MIN_W, event.w), max(MIN_H, event.h)
                # Only rebuild the surface if the size ACTUALLY changed.
                # VIDEORESIZE fires many times during a drag/maximize; calling
                # set_mode() every time tears down and recreates the display
                # surface, which is what causes the flicker. Guarding on real
                # size change collapses that to a single rebuild.
                if not is_fullscreen and (new_w, new_h) != (W, H):
                    W, H = new_w, new_h
                    screen = pygame.display.set_mode((W, H), pygame.RESIZABLE)
                    layout = compute_layout(W, H, strip_h())
                    position_widgets(layout, sliders, mode_btns, pattern_toggles, scan_toggle)
            elif event.type == pygame.KEYDOWN:
                if   event.key == pygame.K_ESCAPE:
                    if is_fullscreen:
                        # ESC leaves fullscreen first rather than quitting.
                        is_fullscreen = False
                        screen = pygame.display.set_mode((windowed_w, windowed_h),
                                                         pygame.RESIZABLE)
                        W, H = windowed_w, windowed_h
                        layout = compute_layout(W, H, strip_h())
                        position_widgets(layout, sliders, mode_btns, pattern_toggles, scan_toggle)
                    else:
                        running = False
                elif event.key == pygame.K_f:
                    # Toggle borderless fullscreen at the desktop resolution.
                    is_fullscreen = not is_fullscreen
                    if is_fullscreen:
                        windowed_w, windowed_h = W, H     # remember to restore
                        info = pygame.display.Info()
                        W, H = info.current_w, info.current_h
                        screen = pygame.display.set_mode(
                            (W, H), pygame.FULLSCREEN | pygame.SCALED)
                    else:
                        W, H = windowed_w, windowed_h
                        screen = pygame.display.set_mode((W, H), pygame.RESIZABLE)
                    layout = compute_layout(W, H, strip_h())
                    position_widgets(layout, sliders, mode_btns, pattern_toggles, scan_toggle)
                elif event.key == pygame.K_1: mode = "synth"
                elif event.key == pygame.K_2: mode = "manual"
                elif event.key == pygame.K_3:
                    mode = "sdk"
                    if sdk_src: sdk_src.start_scan()
                elif event.key == pygame.K_4:
                    mode = "multi"
                    if multi_mgr: multi_mgr.start_scan()
                elif event.key == pygame.K_c:
                    with state.lock:
                        state.contact = not state.contact
                elif event.key == pygame.K_r:
                    for i, t in enumerate(pattern_toggles):
                        t.on = (i == 0)
                elif event.key == pygame.K_l:
                    on = bridge.toggle()
                    print(f"[busylight] output {'on' if on else 'off'}")
                elif event.key == pygame.K_m:
                    print(f"[busylight] mapping -> {bridge.cycle_mapping()}")
                elif event.key == pygame.K_k:
                    added = bridge.discover()
                    print(f"[busylight] rescan: +{added} — {bridge.status}")
                elif event.key in (pygame.K_LEFTBRACKET, pygame.K_RIGHTBRACKET):
                    step = 0.1 if event.key == pygame.K_RIGHTBRACKET else -0.1
                    bridge.nudge_brightness(step)
                elif event.key in (pygame.K_COMMA, pygame.K_PERIOD):
                    step = 0.25 if event.key == pygame.K_PERIOD else -0.25
                    print(f"[busylight] sensitivity {bridge.nudge_gain(step):.2f}x")

            if btn_synth.handle(event):  mode = "synth"
            if btn_manual.handle(event): mode = "manual"
            if btn_sdk.handle(event):
                mode = "sdk"
                if sdk_src: sdk_src.start_scan()
            if btn_multi.handle(event):
                mode = "multi"
                if multi_mgr: multi_mgr.start_scan()
            for tog in pattern_toggles: tog.handle(event)
            if scan_toggle.handle(event):
                # Toggle just changed. Apply immediately to the active source.
                if scan_toggle.on:
                    # Re-enable scanning in the current SDK/MULTI source.
                    if mode == "sdk" and sdk_src and sdk_src.device is None:
                        sdk_src.start_scan()
                    elif mode == "multi" and multi_mgr:
                        multi_mgr.start_scan()
                else:
                    # Stop scanning; keep any existing connections streaming.
                    if sdk_src:
                        sdk_src._want_scan = False
                        try: CMSNSDK.stop_device_scan()
                        except Exception: pass
                    if multi_mgr:
                        multi_mgr.stop_scan()
            for s in sliders: s.handle(event)

        btn_synth.active  = (mode == "synth")
        btn_manual.active = (mode == "manual")
        btn_sdk.active    = (mode == "sdk")
        btn_multi.active  = (mode == "multi")

        # Real hardware uses larger amplitudes than the synthetic engine, so
        # SDK mode normalizes against its own ceilings.
        active_ceiling = BAND_MAX_SDK if mode == "sdk" else BAND_MAX

        # Slider sync
        bands_norm = state.normalized_bands(active_ceiling)
        if mode != "manual":
            for i, s in enumerate(sliders):
                s.value = float(bands_norm[i])
        else:
            for i, s in enumerate(sliders):
                slider_values[i] = s.value

        # --------- step source ---------
        if mode == "synth":
            synth_src.step(dt, t_anim)
        elif mode == "manual":
            manual_src.step(dt, t_anim)
        elif mode == "sdk":
            if sdk_src:
                # The Auto-scan toggle gates periodic rescanning. When OFF, we
                # force _want_scan off so step() won't re-issue scans (existing
                # connection keeps streaming). When ON, step() rescans as normal
                # while no device is connected.
                if not scan_toggle.on:
                    sdk_src._want_scan = False
                elif sdk_src.device is None and not sdk_src._want_scan:
                    sdk_src._want_scan = True
                sdk_src.step(dt, t_anim)
        elif mode == "multi":
            if multi_mgr:
                if not scan_toggle.on:
                    multi_mgr._want_scan = False
                elif not multi_mgr._want_scan:
                    multi_mgr._want_scan = True
                multi_mgr.step(dt, t_anim)

        # In MULTI mode, mirror the FIRST connected headset into the shared
        # `state` so the left-panel metrics + color net still show something,
        # and export every headset to its own files.
        if mode == "multi" and multi_mgr is not None and multi_mgr.order:
            first = multi_mgr.slots.get(multi_mgr.order[0])
            if first is not None:
                with first.state.lock:
                    state.bands[:] = first.state.bands
                    state.attention = first.state.attention
                    state.meditation = first.state.meditation
                    state.contact = first.state.contact

        bands_norm = state.normalized_bands(active_ceiling)
        rgb, _ = mlp.forward(bands_norm)

        # Export + terminal readout (rate-limited internally). Uses raw uV/Hz
        # band values for the CSV, plus the inferred emotion.
        with state.lock:
            raw_bands = state.bands.copy()
            cur_att = state.attention
            cur_med = state.meditation
            cur_contact = state.contact
        if mode == "multi" and multi_mgr is not None:
            multi_mgr.export_all()          # each headset -> its own files
        else:
            exporter.update(mode, bands_norm, raw_bands, cur_att, cur_med,
                            cur_contact, rgb)

        # --------- drive the busylights ---------
        # One snapshot per headset in MULTI, one overall otherwise; the bridge
        # splits the available lights between them and rate-limits its own
        # writes, so calling this every frame is fine.
        bridge.update(now, build_snapshots(mode, state, bands_norm, rgb,
                                           mlp.last_hidden, mlp_bl,
                                           multi_mgr, sdk_src))
        if len(bridge.devices) != light_count:
            # A rescan found (or lost) hardware — the strip's footprint changed.
            light_count = len(bridge.devices)
            layout = compute_layout(W, H, strip_h())
            position_widgets(layout, sliders, mode_btns, pattern_toggles, scan_toggle)

        # --------- render ---------
        screen.fill(BG_DARK)

        # Atmospheric glow behind centre
        glow = pygame.Surface((W, H), pygame.SRCALPHA)
        for i in range(28):
            r = layout["radius"] + 60 + i * 14
            a = max(0, 16 - i // 2)
            pygame.draw.circle(glow, (90, 18, 18, a),
                               (layout["center_x"], layout["center_y"]), r, 2)
        screen.blit(glow, (0, 0))

        # ----- LEFT PANEL -----
        px, pw = layout["panel_x"], layout["panel_w"]
        screen.blit(f_serif_big.render("Crimson", True, TEXT), (px, 22))
        screen.blit(f_ui_sm.render("MANDALA  ·  BETA POC v2", True, ACCENT), (px, 68))
        screen.blit(f_ui.render("Sacred geometry × tiny neural net", True, TEXT_DIM),
                    (px, 84))

        for b in mode_btns: b.draw(screen, f_ui)

        if mode == "synth":
            hint = "Auto-drifting synthetic signals"
        elif mode == "manual":
            hint = "Drag sliders → drives the visuals"
        elif mode == "multi":
            if multi_mgr:
                hint = f"{len(multi_mgr.slots)} headset(s) · {multi_mgr.status}"
            else:
                hint = "SDK unavailable: " + (SDK_IMPORT_ERROR or "not found")
        else:
            hint = (sdk_src.status if sdk_src
                    else ("SDK unavailable: " + (SDK_IMPORT_ERROR or "not found")))
        # When the scan toggle is shown it occupies the right ~100px of this
        # line, so keep the hint short enough not to run underneath it.
        if mode in ("sdk", "multi"):
            max_chars = 30
            hint_disp = hint if len(hint) <= max_chars else hint[:max_chars - 1] + "…"
        else:
            hint_disp = hint
        screen.blit(f_ui_sm.render(hint_disp, True, TEXT_DIM), (px, 150))

        # Auto-scan toggle — only meaningful in SDK / MULTI modes.
        if mode in ("sdk", "multi"):
            scan_toggle.draw(screen, f_ui_sm)

        screen.blit(f_ui_sm.render("PATTERNS  ·  fuse freely", True, ACCENT), (px, 180))
        for tog in pattern_toggles: tog.draw(screen, f_ui)

        section = "BAND CONTROLS" if mode == "manual" else "BAND READOUT"
        screen.blit(f_ui_sm.render(section, True, ACCENT), (px, 296))
        for s in sliders: s.draw(screen, f_ui)

        stats_y = sliders[-1].rect.y + 26
        screen.blit(f_ui_sm.render("ATTENTION", True, TEXT_DIM), (px, stats_y))
        screen.blit(f_serif_big.render(f"{state.attention:.0f}", True, (240, 100, 100)),
                    (px, stats_y + 14))
        screen.blit(f_ui_sm.render("MEDITATION", True, TEXT_DIM), (px + 150, stats_y))
        screen.blit(f_serif_big.render(f"{state.meditation:.0f}", True, (100, 160, 240)),
                    (px + 150, stats_y + 14))
        contact_y = stats_y + 76
        cdot = (90, 200, 110) if state.contact else (240, 170, 60)
        pygame.draw.circle(screen, cdot, (px + 5, contact_y + 6), 4)
        cstr = "CONTACT — good" if state.contact else "LEAD-OFF — adjust"
        screen.blit(f_ui_sm.render(cstr, True, TEXT_DIM), (px + 16, contact_y))
        screen.blit(f_ui_sm.render("(C toggles · R resets patterns)", True, TEXT_FAINT),
                    (px + 16, contact_y + 14))

        # Inferred emotion / mood label
        emo_label, emo_conf = infer_emotion(bands_norm, cur_att, cur_med, cur_contact)
        emo_y = contact_y + 36
        screen.blit(f_ui_sm.render("INFERRED MOOD", True, ACCENT), (px, emo_y))
        emo_col = (235, 225, 215) if cur_contact else (150, 140, 135)
        screen.blit(f_serif_md.render(emo_label, True, emo_col), (px, emo_y + 14))
        # small confidence bar
        bar_w = 150
        bar_x = px
        bar_y = emo_y + 42
        pygame.draw.rect(screen, (30, 20, 20),
                         pygame.Rect(bar_x, bar_y, bar_w, 5), border_radius=2)
        pygame.draw.rect(screen, ACCENT,
                         pygame.Rect(bar_x, bar_y, int(bar_w * emo_conf), 5),
                         border_radius=2)
        screen.blit(f_ui_sm.render(f"{emo_conf*100:.0f}% conf", True, TEXT_FAINT),
                    (bar_x + bar_w + 8, bar_y - 4))

        # ----- CENTER: patterns (or headset grid in MULTI mode) -----
        # multi_mgr is None when the SDK didn't import; without this guard,
        # pressing 4 on a machine with no SDK crashes in draw_multi_grid.
        if mode == "multi" and multi_mgr is not None:
            grid_area = (
                layout["panel_x"] + layout["panel_w"] + 24,
                60,
                layout["right_x"] - (layout["panel_x"] + layout["panel_w"]) - 48,
                H - 100 - layout["strip"],
            )
            draw_multi_grid(screen, grid_area, multi_mgr, mlp, t_anim,
                            (f_serif_md, f_ui, f_ui_sm, f_mono))
        else:
            ctx = {
                "center":   (layout["center_x"], layout["center_y"]),
                "radius":   layout["radius"],
                "bands":    bands_norm,
                "rgb":      rgb,
                "t":        t_anim,
                "formless": formless_field,
            }
            any_on = False
            for tog, (_, fn) in zip(pattern_toggles, PATTERNS):
                if tog.on:
                    fn(screen, ctx)
                    any_on = True
            if not any_on:
                msg = f_ui.render("No patterns selected — toggle one on the left.",
                                  True, TEXT_FAINT)
                screen.blit(msg, msg.get_rect(center=(layout["center_x"], layout["center_y"])))

        # ----- RIGHT PANEL -----
        rx, rw = layout["right_x"], layout["right_w"]
        screen.blit(f_ui_sm.render("COLOR NEURAL NET  ·  6 → 4 → 3", True, ACCENT),
                    (rx, 30))
        nn_rect = pygame.Rect(rx, 50, rw, 220)
        pygame.draw.rect(screen, (15, 10, 10), nn_rect, border_radius=4)
        pygame.draw.rect(screen, BORDER, nn_rect, 1, border_radius=4)
        draw_nn(screen, nn_rect, mlp, f_ui_sm)

        r, g, b = mlp.last_output
        hex_s = f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}"
        screen.blit(f_mono.render(
            f"RGB ({r:.2f}, {g:.2f}, {b:.2f})   {hex_s}", True, TEXT_DIM), (rx, 277))

        screen.blit(f_ui_sm.render("EEG  ·  Fp1–Fp2  ·  250 Hz  ·  ±150 µV",
                    True, ACCENT), (rx, 308))
        draw_eeg(screen, pygame.Rect(rx, 328, rw, 100), state)

        screen.blit(f_ui_sm.render("BRAIN STATE  ·  hidden activations", True, ACCENT),
                    (rx, 446))
        for i, name in enumerate(TinyMLP.HIDDEN_NAMES):
            act = mlp.last_hidden[i]
            y = 471 + i * 36
            screen.blit(f_ui.render(name.upper(), True, TEXT), (rx, y))
            screen.blit(f_mono.render(f"{act*100:5.1f}%", True, TEXT_DIM),
                        (rx + rw - 50, y))
            track = pygame.Rect(rx, y + 18, rw, 6)
            pygame.draw.rect(screen, (30, 20, 20), track, border_radius=3)
            fill = pygame.Rect(rx, y + 18, int(rw * act), 6)
            pygame.draw.rect(screen, TinyMLP.HIDDEN_COLORS[i], fill, border_radius=3)

        # ----- BOTTOM: busylight strip -----
        # Starts right of the left panel: that panel runs tall at small window
        # heights, and a full-width strip would collide with its footer.
        if bridge.devices:
            sx = layout["panel_x"] + layout["panel_w"] + 24
            strip_rect = pygame.Rect(sx, H - BL_STRIP_H - 26,
                                     W - sx - 22, BL_STRIP_H)
            draw_light_strip(screen, strip_rect, bridge, (f_ui, f_ui_sm, f_mono))

        screen.blit(f_ui_sm.render(
            "ESC quit/exit-FS · 1/2/3/4 modes · C contact · R reset · F fullscreen · "
            "L lights · M map · K rescan · [ ] bright · , . sensitivity",
            True, TEXT_FAINT), (22, H - 20))

        pygame.display.flip()
        clock.tick(FPS)

    if sdk_src:
        sdk_src.stop()
    if multi_mgr:
        multi_mgr.stop()
    bridge.close()          # turns every light off before releasing the handles
    try:
        exporter.close()
    except Exception:
        pass
    pygame.quit()


if __name__ == "__main__":
    main()