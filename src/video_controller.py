#!/usr/bin/env python3
"""
HTTP API Controller for the Raspberry Pi Multiviewer
Flask web server with REST API and web dashboard
"""

import atexit
import os
import signal
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

from layout_manager import LayoutManager
from multiviewer import TEMPLATES, Multiviewer
from zone_player import TEST_SOURCES

BASE_DIR = Path(os.environ.get("MV_BASE_DIR", "/opt/rpi-video-player"))
VIDEO_DIR = BASE_DIR / "data" / "videos"
ALLOWED_EXTENSIONS = {"mp4", "avi", "mkv", "mov", "webm", "flv", "wmv", "m4v", "ts", "mxf"}
MAX_FILE_SIZE = 5 * 1024 * 1024 * 1024  # 5GB

app = Flask(__name__, template_folder="../web/templates", static_folder="../web/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

mv = Multiviewer(base_dir=BASE_DIR)
layouts = LayoutManager(BASE_DIR / "config" / "layouts.json")


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def body():
    return request.get_json(silent=True) or {}


def api(fn):
    """Uniform error handling."""
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except KeyError as e:
            return jsonify({"error": str(e).strip("'\"")}), 404
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 500
    wrapper.__name__ = fn.__name__
    return wrapper


# =============================================================================
# Web interface
# =============================================================================

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "zones": len(mv.zones)})


# =============================================================================
# Global status / settings
# =============================================================================

@app.route("/api/status", methods=["GET"])
@api
def api_status():
    return jsonify(mv.status())


@app.route("/api/settings", methods=["GET", "POST"])
@api
def api_settings():
    if request.method == "POST":
        return jsonify(mv.update_settings(body()))
    return jsonify(mv.settings)


@app.route("/api/display/resolution", methods=["GET", "POST"])
@api
def api_resolution():
    if request.method == "POST":
        d = body()
        if not d.get("width") or not d.get("height"):
            raise ValueError("Width and height required")
        mv.update_settings({"display_width": int(d["width"]), "display_height": int(d["height"])})
    return jsonify({"width": mv.settings["display_width"], "height": mv.settings["display_height"]})


@app.route("/api/templates", methods=["GET"])
def api_templates():
    return jsonify({name: len(cells) for name, (_, _, cells) in TEMPLATES.items()})


@app.route("/api/test-sources", methods=["GET"])
def api_test_sources():
    return jsonify(sorted(TEST_SOURCES))


# =============================================================================
# Zones
# =============================================================================

@app.route("/api/zones", methods=["GET"])
@api
def api_list_zones():
    return jsonify(mv.status()["zones"])


@app.route("/api/zones", methods=["POST"])
@api
def api_add_zone():
    d = body()
    zone = mv.add_zone(d, play=d.get("play", bool(d.get("source"))))
    return jsonify(zone.to_status()), 201


@app.route("/api/zones/<int:zone_id>", methods=["GET"])
@api
def api_get_zone(zone_id):
    return jsonify(mv.get(zone_id).to_status())


@app.route("/api/zones/<int:zone_id>", methods=["PATCH", "PUT"])
@api
def api_update_zone(zone_id):
    return jsonify(mv.update_zone(zone_id, body()).to_status())


@app.route("/api/zones/<int:zone_id>", methods=["DELETE"])
@api
def api_delete_zone(zone_id):
    mv.remove_zone(zone_id)
    return jsonify({"status": "deleted", "id": zone_id})


@app.route("/api/zones/<int:zone_id>/play", methods=["POST"])
@api
def api_zone_play(zone_id):
    d = body()
    source = d.pop("source", None)
    ok = mv.play_zone(zone_id, source, **d)
    z = mv.get(zone_id)
    if not ok:
        return jsonify({"error": z.last_error or "Failed to start playback", **z.to_status()}), 500
    return jsonify(z.to_status())


@app.route("/api/zones/<int:zone_id>/stop", methods=["POST"])
@api
def api_zone_stop(zone_id):
    mv.stop_zone(zone_id)
    return jsonify(mv.get(zone_id).to_status())


@app.route("/api/zones/<int:zone_id>/pause", methods=["POST"])
@api
def api_zone_pause(zone_id):
    d = body()
    status = mv.get(zone_id).pause(d.get("pause"))
    return jsonify({"id": zone_id, "status": status})


@app.route("/api/zones/<int:zone_id>/seek", methods=["POST"])
@api
def api_zone_seek(zone_id):
    d = body()
    z = mv.get(zone_id)
    if "seconds" in d:
        z.seek(d["seconds"], relative=True)
    elif "position" in d:
        z.seek(d["position"])
    else:
        raise ValueError("position or seconds required")
    return jsonify({"status": "ok"})


@app.route("/api/zones/<int:zone_id>/volume", methods=["POST"])
@api
def api_zone_volume(zone_id):
    d = body()
    if d.get("volume") is None:
        raise ValueError("No volume provided")
    mv.get(zone_id).set_volume(d["volume"])
    mv.save_state()
    return jsonify({"status": "ok", "volume": mv.get(zone_id).volume})


@app.route("/api/zones/<int:zone_id>/geometry", methods=["POST"])
@api
def api_zone_geometry(zone_id):
    d = body()
    if any(d.get(k) is None for k in ("x", "y", "width", "height")):
        raise ValueError("Missing geometry parameters")
    return jsonify(mv.update_zone(zone_id, {"geometry": d}).to_status())


@app.route("/api/zones/<int:zone_id>/tally", methods=["POST"])
@api
def api_zone_tally(zone_id):
    mv.set_tally({zone_id: body().get("state", "off")})
    return jsonify(mv.get(zone_id).to_status())


@app.route("/api/zones/<int:zone_id>/layer", methods=["POST"])
@api
def api_zone_layer(zone_id):
    mv.move_layer(zone_id, body().get("direction", "top"))
    return jsonify([z.id for z in mv.zones])


# =============================================================================
# Multi-zone operations
# =============================================================================

@app.route("/api/play-all", methods=["POST"])
@api
def api_play_all():
    return jsonify(mv.play_all())


@app.route("/api/stop-all", methods=["POST"])
@api
def api_stop_all():
    mv.stop_all()
    return jsonify({"status": "stopped"})


@app.route("/api/audio", methods=["POST"])
@api
def api_audio_follow():
    """Audio follow: {"zone": 3} -> only zone 3 is audible. {"zone": null} -> all muted."""
    mv.set_audio_follow(body().get("zone"))
    return jsonify({"audio_zone": next((z.id for z in mv.zones if z.audio), None)})


@app.route("/api/tally", methods=["POST"])
@api
def api_tally_bulk():
    """{"program": 1, "preview": 2} or {"1": "program", "2": "preview"}.
    With program/preview keys, every other zone is cleared."""
    d = body()
    if "program" in d or "preview" in d:
        mapping = {z.id: "off" for z in mv.zones}
        if d.get("preview") is not None:
            mapping[int(d["preview"])] = "preview"
        if d.get("program") is not None:
            mapping[int(d["program"])] = "program"
    else:
        mapping = {int(k): v for k, v in d.items()}
    mv.set_tally(mapping)
    return jsonify({z.id: z.tally for z in mv.zones})


@app.route("/api/layout/template", methods=["POST"])
@api
def api_layout_template():
    d = body()
    return jsonify(mv.apply_template(d.get("template"), d.get("cols"), d.get("rows"), d.get("gap")))


@app.route("/api/layout", methods=["GET"])
@api
def api_layout_export():
    return jsonify(mv.export_layout())


@app.route("/api/layout", methods=["POST"])
@api
def api_layout_import():
    d = body()
    return jsonify(mv.load_layout(d, autoplay=d.get("autoplay", True)))


# =============================================================================
# Layout presets
# =============================================================================

@app.route("/api/presets", methods=["GET"])
def api_list_presets():
    return jsonify({"presets": layouts.list(), "default": layouts.default})


@app.route("/api/presets", methods=["POST"])
@api
def api_save_preset():
    d = body()
    name = (d.get("name") or "").strip()
    if not name:
        raise ValueError("Name required")
    layouts.save(name, d.get("layout") or mv.export_layout(), d.get("description", ""))
    return jsonify({"status": "saved", "name": name})


@app.route("/api/presets/<name>", methods=["GET"])
@api
def api_get_preset(name):
    p = layouts.get(name)
    if not p:
        raise KeyError("Preset not found")
    return jsonify(p)


@app.route("/api/presets/<name>/load", methods=["POST"])
@api
def api_load_preset(name):
    p = layouts.get(name)
    if not p:
        raise KeyError("Preset not found")
    return jsonify(mv.load_layout(p, autoplay=body().get("autoplay", True)))


@app.route("/api/presets/<name>", methods=["DELETE"])
@api
def api_delete_preset(name):
    if not layouts.delete(name):
        raise KeyError("Preset not found")
    return jsonify({"status": "deleted", "name": name})


@app.route("/api/presets/<name>/set-default", methods=["POST"])
@api
def api_set_default_preset(name):
    if not layouts.set_default(name):
        raise KeyError("Preset not found")
    return jsonify({"status": "default set", "name": name})


@app.route("/api/presets/clear-default", methods=["POST"])
@api
def api_clear_default_preset():
    layouts.set_default(None)
    return jsonify({"status": "default cleared"})


# =============================================================================
# Legacy single-zone endpoints (Node-RED compatibility) - act on ?zone=<id> or zone 1st
# =============================================================================

def _legacy_zone():
    zid = request.args.get("zone") or body().get("zone")
    if zid is not None:
        return mv.get(zid)
    if not mv.zones:
        W, H = mv.settings["display_width"], mv.settings["display_height"]
        return mv.add_zone({"geometry": {"x": 0, "y": 0, "width": W, "height": H}}, play=False)
    return mv.zones[0]


@app.route("/api/play", methods=["POST"])
@api
def api_play_legacy():
    d = body()
    z = _legacy_zone()
    if not d.get("source"):
        raise ValueError("No source provided")
    ok = mv.play_zone(z.id, d["source"], loop=d.get("loop", True), volume=d.get("volume", 50))
    if not ok:
        return jsonify({"error": "Failed to start playback"}), 500
    return jsonify({"status": "playing", "source": d["source"], "zone": z.id})


@app.route("/api/stop", methods=["POST"])
@api
def api_stop_legacy():
    zid = request.args.get("zone") or body().get("zone")
    if zid is None:
        mv.stop_all()
    else:
        mv.stop_zone(zid)
    return jsonify({"status": "stopped"})


@app.route("/api/pause", methods=["POST"])
@api
def api_pause_legacy():
    return jsonify({"status": _legacy_zone().pause()})


@app.route("/api/geometry", methods=["POST"])
@api
def api_geometry_legacy():
    d = body()
    z = _legacy_zone()
    return jsonify(mv.update_zone(z.id, {"geometry": d}).to_status())


@app.route("/api/volume", methods=["POST"])
@api
def api_volume_legacy():
    z = _legacy_zone()
    z.set_volume(body().get("volume", 50))
    return jsonify({"status": "ok", "volume": z.volume})


# =============================================================================
# Files
# =============================================================================

@app.route("/api/files", methods=["GET"])
@api
def api_list_files():
    files = []
    if VIDEO_DIR.exists():
        for f in VIDEO_DIR.iterdir():
            if f.is_file() and allowed_file(f.name):
                st = f.stat()
                files.append({"name": f.name, "size": st.st_size, "modified": st.st_mtime})
    files.sort(key=lambda x: x["name"].lower())
    return jsonify(files)


@app.route("/api/upload", methods=["POST"])
@api
def api_upload():
    if "file" not in request.files:
        raise ValueError("No file provided")
    file = request.files["file"]
    if not file.filename:
        raise ValueError("No file selected")
    if not allowed_file(file.filename):
        raise ValueError("File type not allowed")
    filename = secure_filename(file.filename)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    path = VIDEO_DIR / filename
    file.save(str(path))
    return jsonify({"status": "uploaded", "filename": filename, "size": path.stat().st_size})


@app.route("/api/files/<filename>", methods=["DELETE"])
@api
def api_delete_file(filename):
    path = VIDEO_DIR / secure_filename(filename)
    if not path.exists():
        raise FileNotFoundError("File not found")
    path.unlink()
    return jsonify({"status": "deleted", "filename": path.name})


# =============================================================================
# Startup
# =============================================================================

def startup():
    if layouts.default and layouts.get(layouts.default):
        print(f"Loading default layout '{layouts.default}'")
        mv.load_layout(layouts.get(layouts.default), autoplay=True)
    else:
        mv.resume()
    mv.start_watchdog()


def _shutdown(*_):
    mv.shutdown()
    sys.exit(0)


if __name__ == "__main__":
    print("=" * 60)
    print("Raspberry Pi Multiviewer")
    print(f"Base dir: {BASE_DIR}")
    print("=" * 60)
    signal.signal(signal.SIGTERM, _shutdown)
    atexit.register(mv.shutdown)
    startup()
    app.run(host="0.0.0.0", port=int(os.environ.get("MV_PORT", 5000)), debug=False, threaded=True)
