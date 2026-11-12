#!/usr/bin/env python3
"""
ZonePlayer - one MPV instance = one multiviewer tile.

Each zone runs its own borderless MPV window with its own IPC socket, so a
dead or frozen feed never takes the other tiles down with it.
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

STREAM_PREFIXES = ("rtsp://", "rtsps://", "rtmp://", "rtmps://", "srt://", "udp://",
                   "rtp://", "tcp://", "http://", "https://", "av://")

# Shortcuts usable as "source" without any camera attached
TEST_SOURCES = {
    "testpattern": "av://lavfi:smptehdbars=size=1280x720:rate=25",
    "testpattern-bars": "av://lavfi:smptehdbars=size=1280x720:rate=25",
    "testpattern-motion": "av://lavfi:testsrc2=size=1280x720:rate=25",
    "black": "av://lavfi:color=c=black:size=640x360:rate=5",
}

TALLY_COLORS = {          # ASS colours are &HBBGGRR&
    "program": "&H0000FF&",   # red
    "preview": "&H00FF00&",   # green
    "standby": "&H00C8FF&",   # amber
}

LABEL_POSITIONS = {
    "top-left": 7, "top-center": 8, "top-right": 9,
    "bottom-left": 1, "bottom-center": 2, "bottom-right": 3,
}


def _ass_escape(text):
    return str(text).replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


class ZonePlayer:
    """Manages one MPV process that renders into one rectangle of the screen."""

    def __init__(self, zone_id, video_dir, settings, config=None):
        self.id = int(zone_id)
        self.video_dir = Path(video_dir)
        self.settings = settings          # shared dict owned by the Multiviewer
        self.socket_path = f"/tmp/mpv-mv-zone-{self.id}.sock"
        self.window_title = f"mvzone-{self.id}"
        self.process = None
        self.lock = threading.RLock()
        self._req_id = 0

        # --- persisted configuration -------------------------------------
        self.name = f"Input {self.id}"
        self.source = None              # what the user asked for (file name / URL / shortcut)
        self.geometry = {"x": 0, "y": 0, "width": 960, "height": 540}
        self.volume = 50
        self.loop = True
        self.audio = False              # only one zone normally has audio ("audio follow")
        self.show_label = True
        self.label_position = "bottom-left"
        self.tally = "off"              # off | preview | program | standby
        self.low_latency = True         # for live streams
        self.low_cpu = False            # skip loop filter + frame dropping
        self.autoplay = False           # should be playing (restored after reboot / by watchdog)

        # --- runtime state -----------------------------------------------
        self.status = "stopped"         # stopped | starting | playing | paused | error
        self.health = "idle"            # idle | ok | reconnecting | lost
        self.position = 0.0
        self.duration = 0.0
        self.last_progress_time = None
        self.last_position = None
        self.restart_attempts = 0
        self.next_restart_at = 0.0
        self.last_error = None

        if config:
            self.apply_config(config)
        self._cleanup_socket()

    # ------------------------------------------------------------------ config
    CONFIG_KEYS = ("name", "source", "volume", "loop", "audio", "show_label",
                   "label_position", "tally", "low_latency", "low_cpu", "autoplay")

    def apply_config(self, cfg):
        for key in self.CONFIG_KEYS:
            if key in cfg and cfg[key] is not None:
                setattr(self, key, cfg[key])
        if "geometry" in cfg and cfg["geometry"]:
            g = cfg["geometry"]
            self.geometry = {k: int(round(float(g[k]))) for k in ("x", "y", "width", "height")}
        self.volume = max(0, min(100, int(self.volume)))
        if self.tally not in ("off", *TALLY_COLORS):
            self.tally = "off"
        if self.label_position not in LABEL_POSITIONS:
            self.label_position = "bottom-left"

    def to_config(self):
        cfg = {k: getattr(self, k) for k in self.CONFIG_KEYS}
        cfg["id"] = self.id
        cfg["geometry"] = dict(self.geometry)
        return cfg

    def to_status(self):
        cfg = self.to_config()
        cfg.update({
            "status": self.status,
            "health": self.health,
            "position": self.position,
            "duration": self.duration,
            "is_stream": self.is_stream,
            "error": self.last_error,
            "pid": self.process.pid if self.is_running() else None,
        })
        return cfg

    # ------------------------------------------------------------- properties
    @property
    def is_stream(self):
        src = self.resolved_source(check=False)
        return bool(src) and src.startswith(STREAM_PREFIXES) and not src.startswith("av://lavfi")

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def resolved_source(self, check=True):
        src = (self.source or "").strip()
        if not src:
            return None
        if src in TEST_SOURCES:
            return TEST_SOURCES[src]
        if src.startswith(STREAM_PREFIXES) or src.startswith("/"):
            return src
        path = self.video_dir / src
        if check and not path.exists():
            raise FileNotFoundError(f"Video file not found: {src}")
        return str(path.absolute())

    # --------------------------------------------------------------- playback
    def play(self, source=None, **opts):
        with self.lock:
            previous = self.source
            if source is not None:
                self.source = source
            try:
                if not self.source:
                    raise ValueError("No source set for this zone")
                resolved = self.resolved_source()
            except Exception:
                self.source = previous          # don't keep a broken source
                raise
            self.apply_config(opts)

            self._kill()
            self.status = "starting"
            self.last_error = None
            cmd = self._build_command(resolved)
            self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, start_new_session=True)

            waited = 0.0
            while not os.path.exists(self.socket_path) and waited < 5:
                if self.process.poll() is not None:
                    break
                time.sleep(0.1)
                waited += 0.1

            if not os.path.exists(self.socket_path):
                self.status = "error"
                self.last_error = "MPV did not start (no IPC socket)"
                self._kill()
                return False

            self.autoplay = True
            self.status = "playing"
            self.health = "ok"
            self.position = 0.0
            self.last_position = None
            self.last_progress_time = time.time()
            self.update_overlay()
            return True

    def stop(self):
        with self.lock:
            self.autoplay = False
            self._kill()
            self.status = "stopped"
            self.health = "idle"
            self.position = 0.0
            self.duration = 0.0
            self.restart_attempts = 0

    def pause(self, state=None):
        with self.lock:
            if self.status not in ("playing", "paused"):
                return self.status
            target = (self.status == "playing") if state is None else bool(state)
            self._send({"command": ["set_property", "pause", target]})
            self.status = "paused" if target else "playing"
            self.last_progress_time = time.time()
            return self.status

    def seek(self, position, relative=False):
        self._send({"command": ["seek", float(position), "relative" if relative else "absolute"]})

    def set_volume(self, volume):
        self.volume = max(0, min(100, int(volume)))
        self._send({"command": ["set_property", "volume", self.volume]})

    def set_audio(self, enabled):
        """Audio follow: deselecting the track closes the audio output entirely."""
        self.audio = bool(enabled)
        if self.is_running():
            self._send({"command": ["set_property", "aid", "auto" if self.audio else "no"]})
            if self.audio:
                self._send({"command": ["set_property", "volume", self.volume]})

    def set_tally(self, tally):
        self.tally = tally if tally in TALLY_COLORS else "off"
        self.update_overlay()

    def set_geometry(self, x, y, width, height):
        """Move/resize the live window without restarting the feed (xdotool)."""
        with self.lock:
            self.geometry = {"x": int(x), "y": int(y),
                             "width": max(32, int(width)), "height": max(32, int(height))}
            if not self.is_running():
                return True
            g = self.geometry
            # keep the option in sync so a later video reconfig uses the new size
            self._send({"command": ["set_property", "geometry",
                                    f"{g['width']}x{g['height']}+{g['x']}+{g['y']}"]})
            moved = self._xdotool_move()
            if not moved:
                # No xdotool available -> restart at new position
                pos = self.position if not self.is_stream else None
                was_paused = self.status == "paused"
                try:
                    self.play()
                except Exception as e:  # noqa: BLE001
                    self.status, self.last_error = "error", str(e)
                    return False
                if pos:
                    time.sleep(0.3)
                    self.seek(pos)
                if was_paused:
                    self.pause(True)
            self.update_overlay()
            return True

    def raise_window(self):
        if self.is_running() and shutil.which("xdotool"):
            self._xdotool(["search", "--name", f"^{self.window_title}$", "windowraise"])

    # ---------------------------------------------------------------- overlay
    def update_overlay(self):
        """Draw label, tally border and 'NO SIGNAL' via MPV's OSD overlay (ASS)."""
        if not self.is_running():
            return
        w, h = self.geometry["width"], self.geometry["height"]
        events = []

        color = TALLY_COLORS.get(self.tally)
        if color:
            t = max(4, int(min(w, h) * 0.015))
            shape = (f"m 0 0 l {w} 0 l {w} {t} l 0 {t} "
                     f"m 0 {h - t} l {w} {h - t} l {w} {h} l 0 {h} "
                     f"m 0 {t} l {t} {t} l {t} {h - t} l 0 {h - t} "
                     f"m {w - t} {t} l {w} {t} l {w} {h - t} l {w - t} {h - t}")
            events.append(f"{{\\an7\\pos(0,0)\\bord0\\shad0\\1c{color}\\1a&H00&\\p1}}{shape}{{\\p0}}")

        fs = max(14, int(h * 0.065))
        margin = max(6, int(fs * 0.5))
        if self.show_label and self.name:
            an = LABEL_POSITIONS.get(self.label_position, 1)
            px = {1: margin, 4: margin, 7: margin, 2: w // 2, 8: w // 2}.get(an, w - margin)
            py = margin if an in (7, 8, 9) else h - margin
            events.append(f"{{\\an{an}\\pos({px},{py})\\fs{fs}\\b1\\bord{max(2, fs // 12)}"
                          f"\\shad0\\1c&HFFFFFF&\\3c&H000000&}}{_ass_escape(self.name)}")

        if self.health in ("reconnecting", "lost"):
            txt = "NO SIGNAL" if self.health == "lost" else "RECONNECTING…"
            events.append(f"{{\\an5\\pos({w // 2},{h // 2})\\fs{int(fs * 1.3)}\\b1\\bord3\\shad0"
                          f"\\1c&H3030FF&\\3c&H000000&}}{txt}")

        self._send({"command": {"name": "osd-overlay", "id": 1, "format": "ass-events",
                                "res_x": w, "res_y": h, "z": 0, "hidden": False,
                                "data": "\n".join(events) if events else ""}})

    # ------------------------------------------------------------ monitoring
    def poll(self):
        """Refresh position/duration. Returns False if MPV died."""
        if not self.is_running():
            return False
        pos = self._get("time-pos")
        if pos is not None:
            self.position = float(pos)
        dur = self._get("duration")
        if dur is not None:
            self.duration = float(dur)
        if self.last_position is None or abs(self.position - self.last_position) > 0.05:
            self.last_position = self.position
            self.last_progress_time = time.time()
        return True

    # --------------------------------------------------------------- internals
    def _build_command(self, source):
        g = self.geometry
        s = self.settings
        cmd = [
            "mpv",
            f"--title={self.window_title}",
            "--no-border", "--no-osc", "--no-osd-bar", "--osd-level=0",
            "--really-quiet", "--no-input-default-bindings", "--input-vo-keyboard=no",
            "--keep-open=yes", "--force-window=yes", "--idle=yes", "--ontop=yes",
            "--cursor-autohide=always", "--no-terminal",
            f"--vo={s.get('vo', 'xv')}",
            f"--hwdec={s.get('hwdec', 'auto')}", "--hwdec-codecs=all",
            f"--geometry={g['width']}x{g['height']}+{g['x']}+{g['y']}",
            "--autofit-larger=100%x100%", "--no-keepaspect-window",
            f"--input-ipc-server={self.socket_path}",
            f"--volume={self.volume}",
            f"--vd-lavc-threads={int(s.get('decoder_threads', 2))}",
            "--network-timeout=10", "--rtsp-transport=tcp",
        ]
        cmd.append("--keepaspect=no" if s.get("stretch", True) else "--keepaspect=yes")
        cmd.append("--aid=auto" if self.audio else "--aid=no")

        if source.startswith(STREAM_PREFIXES) and not source.startswith("av://"):
            if self.low_latency:
                cmd += ["--profile=low-latency", "--cache=no",
                        "--demuxer-lavf-o-add=fflags=+nobuffer",
                        "--demuxer-lavf-analyzeduration=0.5"]
            else:
                cmd += ["--cache=yes", "--demuxer-max-bytes=20M", "--demuxer-readahead-secs=2"]
        else:
            cmd += ["--loop-file=inf" if self.loop else "--loop-file=no",
                    "--cache=yes", "--demuxer-max-bytes=50M"]

        if self.low_cpu:
            cmd += ["--vd-lavc-skiploopfilter=all", "--vd-lavc-fast", "--framedrop=decoder+vo"]

        extra = s.get("extra_mpv_args") or []
        cmd += list(extra)
        cmd.append(source)
        return cmd

    def _xdotool(self, args):
        try:
            r = subprocess.run(["xdotool", *args], capture_output=True, timeout=3)
            return r.returncode == 0
        except Exception:
            return False

    def _xdotool_move(self):
        if not shutil.which("xdotool"):
            return False
        g = self.geometry
        name = f"^{self.window_title}$"
        ok = self._xdotool(["search", "--name", name, "windowsize", "--sync",
                            str(g["width"]), str(g["height"])])
        ok = self._xdotool(["search", "--name", name, "windowmove", "--sync",
                            str(g["x"]), str(g["y"])]) and ok
        return ok

    def _kill(self):
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        self.process = None
        self._cleanup_socket()

    def _cleanup_socket(self):
        try:
            if os.path.exists(self.socket_path):
                os.remove(self.socket_path)
        except OSError:
            pass

    def _get(self, prop):
        resp = self._send({"command": ["get_property", prop]})
        if resp and resp.get("error") == "success":
            return resp.get("data")
        return None

    def _send(self, command, timeout=1.0):
        """Send one IPC command and wait for the reply with the matching request_id
        (MPV also pushes unsolicited event lines on the same socket)."""
        if not os.path.exists(self.socket_path):
            return None
        self._req_id += 1
        rid = self._req_id
        command = dict(command)
        command["request_id"] = rid
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(self.socket_path)
                sock.sendall((json.dumps(command) + "\n").encode())
                buf = b""
                deadline = time.time() + timeout
                while time.time() < deadline:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            msg = json.loads(line)
                        except ValueError:
                            continue
                        if msg.get("request_id") == rid:
                            return msg
        except Exception as e:  # noqa: BLE001
            self.last_error = f"IPC: {e}"
        return None
