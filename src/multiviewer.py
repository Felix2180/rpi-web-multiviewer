#!/usr/bin/env python3
"""
Multiviewer manager - owns all zones, layout templates, audio follow,
persistence (survives reboots) and the built-in per-zone stream watchdog.
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from zone_player import ZonePlayer

BASE_DIR = Path("/opt/rpi-video-player")

DEFAULT_SETTINGS = {
    "display_width": 1920,
    "display_height": 1080,
    "vo": "xv",                 # "xv" (original default) or "gpu"
    "hwdec": "auto",
    "decoder_threads": 2,       # per zone - lower than single-zone since several decoders run
    "stretch": True,            # True = fill tile, False = letterbox inside the tile
    "gap": 4,                   # default gap between tiles for templates (px)
    "watchdog": True,
    "freeze_timeout": 12,       # seconds without progress before a stream is restarted
    "nice": 5,                  # run decoders at lower priority so the system stays responsive
    "overload_guard": True,     # postpone watchdog restarts while the CPU is saturated
    "extra_mpv_args": [],
}

# Templates: (cols, rows, [(col, row, colspan, rowspan), ...])
TEMPLATES = {
    "1":    (1, 1, [(0, 0, 1, 1)]),
    "2x1":  (2, 1, [(0, 0, 1, 1), (1, 0, 1, 1)]),
    "1x2":  (1, 2, [(0, 0, 1, 1), (0, 1, 1, 1)]),
    "2x2":  (2, 2, [(c, r, 1, 1) for r in range(2) for c in range(2)]),
    "3x2":  (3, 2, [(c, r, 1, 1) for r in range(2) for c in range(3)]),
    "3x3":  (3, 3, [(c, r, 1, 1) for r in range(3) for c in range(3)]),
    "4x4":  (4, 4, [(c, r, 1, 1) for r in range(4) for c in range(4)]),
    "1+5":  (3, 3, [(0, 0, 2, 2), (2, 0, 1, 1), (2, 1, 1, 1), (0, 2, 1, 1), (1, 2, 1, 1), (2, 2, 1, 1)]),
    "1+7":  (4, 4, [(0, 0, 3, 3), (3, 0, 1, 1), (3, 1, 1, 1), (3, 2, 1, 1),
                    (0, 3, 1, 1), (1, 3, 1, 1), (2, 3, 1, 1), (3, 3, 1, 1)]),
    "2+8":  (4, 4, [(0, 0, 2, 2), (2, 0, 2, 2)] + [(c, r, 1, 1) for r in (2, 3) for c in range(4)]),
    "1+3":  (3, 3, [(0, 0, 3, 2), (0, 2, 1, 1), (1, 2, 1, 1), (2, 2, 1, 1)]),
}


def grid_cells(cols, rows):
    return [(c, r, 1, 1) for r in range(rows) for c in range(cols)]


class Multiviewer:
    def __init__(self, base_dir=BASE_DIR):
        self.base_dir = Path(base_dir)
        self.video_dir = self.base_dir / "data" / "videos"
        self.state_file = self.base_dir / "config" / "state.json"
        self.settings = dict(DEFAULT_SETTINGS)
        self.zones = []                     # list order == stacking order (last = on top)
        self.next_id = 1
        self.lock = threading.RLock()
        self._stop_event = threading.Event()
        self.system = {}
        self._load_state()
        self.update_system_stats()

    # ------------------------------------------------------------ lookup
    def get(self, zone_id):
        for z in self.zones:
            if z.id == int(zone_id):
                return z
        raise KeyError(f"Zone {zone_id} not found")

    def status(self):
        with self.lock:
            audio = next((z.id for z in self.zones if z.audio), None)
            return {
                "display": {"width": self.settings["display_width"],
                            "height": self.settings["display_height"]},
                "settings": self.settings,
                "audio_zone": audio,
                "system": self.system,
                "zones": [z.to_status() for z in self.zones],
            }

    # ------------------------------------------------------------ zones
    def add_zone(self, cfg=None, play=None):
        cfg = dict(cfg or {})
        with self.lock:
            zid = self.next_id
            self.next_id += 1
            if "geometry" not in cfg:
                W, H = self.settings["display_width"], self.settings["display_height"]
                cfg["geometry"] = {"x": W // 4, "y": H // 4, "width": W // 2, "height": H // 2}
            cfg.setdefault("name", f"Input {zid}")
            zone = ZonePlayer(zid, self.video_dir, self.settings, cfg)
            # first zone gets audio if no one else has it
            if "audio" not in cfg and not any(z.audio for z in self.zones):
                zone.audio = True
            self.zones.append(zone)
            self.save_state()
        if (play if play is not None else zone.autoplay) and zone.source:
            zone.play()
        return zone

    def update_zone(self, zone_id, data):
        zone = self.get(zone_id)
        data = dict(data)
        source_changed = "source" in data and data["source"] != zone.source
        restart_keys = {"low_latency", "low_cpu", "loop"}
        needs_restart = any(k in data and data[k] != getattr(zone, k) for k in restart_keys)

        geometry = data.pop("geometry", None)
        audio = data.pop("audio", None)
        tally = data.pop("tally", None)
        volume = data.pop("volume", None)
        zone.apply_config({k: v for k, v in data.items() if k in ZonePlayer.CONFIG_KEYS})

        if geometry:
            zone.set_geometry(geometry["x"], geometry["y"], geometry["width"], geometry["height"])
        if audio is not None:
            if audio:
                self.set_audio_follow(zone.id)
            else:
                zone.set_audio(False)
        if tally is not None:
            zone.set_tally(tally)
        if volume is not None:
            zone.set_volume(volume)

        if zone.is_running() and (source_changed or needs_restart):
            zone.play()
        else:
            zone.update_overlay()
        self.save_state()
        return zone

    def remove_zone(self, zone_id):
        with self.lock:
            zone = self.get(zone_id)
            zone.stop()
            self.zones.remove(zone)
            if zone.audio and self.zones:
                self.set_audio_follow(self.zones[0].id)
            self.save_state()

    def move_layer(self, zone_id, direction):
        """direction: 'up' | 'down' | 'top' | 'bottom'"""
        with self.lock:
            zone = self.get(zone_id)
            i = self.zones.index(zone)
            self.zones.pop(i)
            if direction == "top":
                self.zones.append(zone)
            elif direction == "bottom":
                self.zones.insert(0, zone)
            elif direction == "up":
                self.zones.insert(min(len(self.zones), i + 1), zone)
            else:
                self.zones.insert(max(0, i - 1), zone)
            self.restack()
            self.save_state()

    def restack(self):
        for z in self.zones:
            z.raise_window()

    # ------------------------------------------------------------ playback
    def play_zone(self, zone_id, source=None, **opts):
        zone = self.get(zone_id)
        ok = zone.play(source, **opts)
        self.restack()
        self.save_state()
        return ok

    def stop_zone(self, zone_id):
        self.get(zone_id).stop()
        self.save_state()

    def play_all(self):
        results = {}
        for z in list(self.zones):
            if z.source:
                try:
                    results[z.id] = z.play()
                except Exception as e:  # noqa: BLE001
                    z.status, z.last_error = "error", str(e)
                    results[z.id] = False
        self.restack()
        self.save_state()
        return results

    def stop_all(self):
        for z in list(self.zones):
            z.stop()
        self.save_state()

    def set_audio_follow(self, zone_id):
        with self.lock:
            target = self.get(zone_id) if zone_id is not None else None
            for z in self.zones:
                if z is not target and z.audio:
                    z.set_audio(False)
            if target:
                target.set_audio(True)
            self.save_state()

    def set_tally(self, mapping):
        """mapping: {zone_id: 'program'|'preview'|'standby'|'off'}"""
        for zid, state in mapping.items():
            self.get(zid).set_tally(state)
        self.save_state()

    # ------------------------------------------------------------ layout
    def _cells_to_geometry(self, cols, rows, cells, gap):
        W, H = self.settings["display_width"], self.settings["display_height"]
        cw = (W - gap * (cols + 1)) / cols
        ch = (H - gap * (rows + 1)) / rows
        out = []
        for c, r, cs, rs in cells:
            out.append({"x": int(round(gap + c * (cw + gap))),
                        "y": int(round(gap + r * (ch + gap))),
                        "width": int(round(cs * cw + (cs - 1) * gap)),
                        "height": int(round(rs * ch + (rs - 1) * gap))})
        return out

    def apply_template(self, template=None, cols=None, rows=None, gap=None):
        """Re-arrange into a template. Existing zones keep their sources (in order);
        missing zones are created, surplus zones are stopped and removed."""
        gap = self.settings["gap"] if gap is None else int(gap)
        if template:
            if template not in TEMPLATES:
                raise ValueError(f"Unknown template '{template}'. Available: {', '.join(TEMPLATES)}")
            c, r, cells = TEMPLATES[template]
        else:
            c, r = int(cols), int(rows)
            if not (1 <= c <= 8 and 1 <= r <= 8):
                raise ValueError("cols/rows must be 1..8")
            cells = grid_cells(c, r)
        geoms = self._cells_to_geometry(c, r, cells, gap)

        with self.lock:
            # sort current zones by visual reading order so sources stay where expected
            ordered = sorted(self.zones, key=lambda z: (z.geometry["y"], z.geometry["x"]))
            for i, g in enumerate(geoms):
                if i < len(ordered):
                    ordered[i].set_geometry(g["x"], g["y"], g["width"], g["height"])
                else:
                    self.add_zone({"geometry": g}, play=False)
            for z in ordered[len(geoms):]:
                z.stop()
                self.zones.remove(z)
            if self.zones and not any(z.audio for z in self.zones):
                self.zones[0].audio = True
            self.save_state()
        return self.status()

    def export_layout(self):
        with self.lock:
            return {
                "display": {"width": self.settings["display_width"],
                            "height": self.settings["display_height"]},
                "zones": [{k: v for k, v in z.to_config().items() if k not in ("id", "autoplay")}
                          for z in self.zones],
            }

    def load_layout(self, layout, autoplay=True):
        """Switch to a saved layout. Running zones whose source also appears in the new
        layout are re-used (just moved) so live feeds don't drop out while switching."""
        new_cfgs = layout.get("zones", [])
        with self.lock:
            pool = list(self.zones)
            new_zones = []
            for cfg in new_cfgs:
                reuse = next((z for z in pool if z.source and z.source == cfg.get("source")
                              and z.is_running()), None)
                if reuse:
                    pool.remove(reuse)
                    g = cfg.get("geometry", reuse.geometry)
                    reuse.apply_config({k: v for k, v in cfg.items()
                                        if k not in ("geometry", "audio", "tally")})
                    reuse.set_geometry(g["x"], g["y"], g["width"], g["height"])
                    reuse.set_tally(cfg.get("tally", "off"))
                    reuse.set_audio(cfg.get("audio", False))
                    new_zones.append(reuse)
                else:
                    zid = self.next_id
                    self.next_id += 1
                    new_zones.append(ZonePlayer(zid, self.video_dir, self.settings, cfg))
            for z in pool:
                z.stop()
            self.zones = new_zones
            self.save_state()

        if autoplay:
            for z in self.zones:
                if z.source and not z.is_running():
                    try:
                        z.play()
                    except Exception as e:  # noqa: BLE001
                        z.status, z.last_error = "error", str(e)
            self.restack()
            self.save_state()
        return self.status()

    # ------------------------------------------------------------ settings
    def update_settings(self, data):
        with self.lock:
            for k, v in data.items():
                if k in DEFAULT_SETTINGS:
                    self.settings[k] = type(DEFAULT_SETTINGS[k])(v) if not isinstance(
                        DEFAULT_SETTINGS[k], list) else list(v)
            self.save_state()
        return self.settings

    # ------------------------------------------------------------ persistence
    def save_state(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {"settings": self.settings, "next_id": self.next_id,
                    "zones": [z.to_config() for z in self.zones]}
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(self.state_file)
        except Exception as e:  # noqa: BLE001
            print(f"State save failed: {e}")

    def _load_state(self):
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text())
            self.settings.update({k: v for k, v in data.get("settings", {}).items()
                                  if k in DEFAULT_SETTINGS})
            for cfg in data.get("zones", []):
                self.zones.append(ZonePlayer(cfg["id"], self.video_dir, self.settings, cfg))
            self.next_id = max([data.get("next_id", 1)] + [z.id + 1 for z in self.zones])
            print(f"Restored {len(self.zones)} zones from state")
        except Exception as e:  # noqa: BLE001
            print(f"State load failed: {e}")

    def resume(self):
        """Start every zone that was playing before shutdown/reboot."""
        for z in self.zones:
            if z.autoplay and z.source:
                try:
                    z.play()
                except Exception as e:  # noqa: BLE001
                    z.status, z.last_error = "error", str(e)
        self.restack()

    # ------------------------------------------------------------ watchdog
    def start_watchdog(self, interval=2.0):
        t = threading.Thread(target=self._watchdog_loop, args=(interval,), daemon=True)
        t.start()
        return t

    def shutdown(self):
        self._stop_event.set()
        for z in list(self.zones):
            z._kill()  # keep autoplay flag so zones come back after restart

    def _watchdog_loop(self, interval):
        cycle = 0
        while not self._stop_event.wait(interval):
            cycle += 1
            if cycle % 2 == 0:
                self.update_system_stats()
            self._restarted_this_cycle = False
            for z in list(self.zones):
                try:
                    self._check_zone(z)
                    if cycle % 3 == 0:
                        z.poll_decoder()
                except Exception as e:  # noqa: BLE001
                    print(f"Watchdog zone {z.id}: {e}")

    # ------------------------------------------------------------ system stats
    def update_system_stats(self):
        st = {"cores": os.cpu_count() or 1}
        try:
            st["load"] = round(os.getloadavg()[0], 2)
        except OSError:
            pass
        try:
            st["temp_c"] = round(int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000, 1)
        except Exception:  # noqa: BLE001
            pass
        try:
            mem = {}
            for line in open("/proc/meminfo"):
                k, v = line.split(":", 1)
                mem[k] = int(v.split()[0])
            st["mem_total_mb"] = mem["MemTotal"] // 1024
            st["mem_avail_mb"] = mem["MemAvailable"] // 1024
        except Exception:  # noqa: BLE001
            pass
        try:
            st["model"] = open("/proc/device-tree/model").read().strip("\x00").strip()
        except Exception:  # noqa: BLE001
            pass
        try:
            out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                                 text=True, timeout=2).stdout
            val = int(out.strip().split("=")[1], 16)
            st["throttled_raw"] = hex(val)
            st["undervoltage_now"] = bool(val & 0x1)
            st["throttled_now"] = bool(val & 0x4) or bool(val & 0x2)
            st["soft_temp_limit_now"] = bool(val & 0x8)
            st["undervoltage_since_boot"] = bool(val & 0x10000)
            st["throttled_since_boot"] = bool(val & 0x40000) or bool(val & 0x20000)
        except Exception:  # noqa: BLE001
            pass
        self.system = st
        return st

    def overloaded(self):
        load, cores = self.system.get("load"), self.system.get("cores", 1)
        return load is not None and load > cores * 1.5

    def _check_zone(self, z):
        if z.status in ("stopped",) or not z.autoplay:
            return
        alive = z.poll()
        now = time.time()
        if not self.settings.get("watchdog", True):
            return

        problem = None
        if not alive:
            problem = "process died"
        elif z.is_stream and z.status == "playing" and z.last_progress_time and \
                now - z.last_progress_time > float(self.settings["freeze_timeout"]):
            problem = "frozen"

        if not problem:
            if z.health != "ok":
                z.health = "ok"
                z.restart_attempts = 0
                z.update_overlay()
            return

        if now < z.next_restart_at:
            return
        # Restarting while the CPU is saturated just adds load (new connection, demuxer
        # probing, keyframe wait) and can snowball. One restart per cycle, none on overload.
        if getattr(self, "_restarted_this_cycle", False):
            return
        if self.settings.get("overload_guard", True) and self.overloaded() and alive:
            z.health = "reconnecting"
            z.next_restart_at = now + 10
            print(f"Watchdog: zone {z.id} {problem}, but CPU overloaded "
                  f"(load {self.system.get('load')}) -> postponing restart")
            z.update_overlay()
            return
        self._restarted_this_cycle = True
        z.restart_attempts += 1
        z.health = "reconnecting" if z.restart_attempts <= 3 else "lost"
        backoff = min(60, 3 * (2 ** min(z.restart_attempts - 1, 5)))
        z.next_restart_at = now + backoff
        print(f"Watchdog: zone {z.id} ({z.name}) {problem} -> restart #{z.restart_attempts}")
        z.update_overlay()
        try:
            if z.play():
                z.health = "reconnecting" if z.restart_attempts <= 3 else "lost"
                z.last_progress_time = time.time()
                z.update_overlay()
                self.restack()
        except Exception as e:  # noqa: BLE001
            z.last_error = str(e)

