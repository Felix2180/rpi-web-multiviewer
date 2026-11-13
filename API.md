# Multiviewer HTTP API

Base URL: `http://<pi-ip>:5000`. All bodies are JSON (`Content-Type: application/json`).
Errors return `{"error": "..."}` with status 400/404/500.

## Zone object

```json
{
  "id": 1, "name": "CAM 1", "source": "rtsp://10.0.0.11/stream2",
  "geometry": {"x": 4, "y": 4, "width": 954, "height": 534},
  "volume": 50, "loop": true, "audio": true,
  "show_label": true, "label_position": "bottom-left",
  "tally": "off", "low_latency": true, "low_cpu": false, "autoplay": true,
  "status": "playing", "health": "ok", "position": 12.3, "duration": 0,
  "is_stream": true, "error": null, "pid": 1234
}
```

- `status`: `stopped | starting | playing | paused | error`
- `health`: `idle | ok | reconnecting | lost`
- `tally`: `off | preview | program | standby`
- `label_position`: `top-left | top-center | top-right | bottom-left | bottom-center | bottom-right`
- `source`: file name from the upload folder, absolute path, `rtsp:// srt:// udp:// rtp:// rtmp:// http(s):// av://` URL, or `testpattern | testpattern-bars | testpattern-motion | black`

## Status & settings

| Method | Path | Body | |
|---|---|---|---|
| GET | `/api/status` | – | display, settings, `audio_zone`, all zones |
| GET/POST | `/api/settings` | any subset of settings | see README |
| GET/POST | `/api/display/resolution` | `{"width":1920,"height":1080}` | canvas coordinate space |
| GET | `/api/templates` | – | `{name: tileCount}` |
| GET | `/api/test-sources` | – | built-in test sources |

## Zones

| Method | Path | Body |
|---|---|---|
| GET | `/api/zones` | – |
| POST | `/api/zones` | zone fields (all optional), `"play": true` to start immediately |
| GET | `/api/zones/<id>` | – |
| PATCH | `/api/zones/<id>` | any zone fields. A changed `source`, `loop`, `low_latency` or `low_cpu` restarts a running zone; `geometry` is applied live |
| DELETE | `/api/zones/<id>` | – |
| POST | `/api/zones/<id>/play` | optional `source` + zone fields |
| POST | `/api/zones/<id>/stop` | – |
| POST | `/api/zones/<id>/pause` | optional `{"pause": true/false}` (toggles if omitted) |
| POST | `/api/zones/<id>/seek` | `{"position": 30}` or `{"seconds": -10}` |
| POST | `/api/zones/<id>/volume` | `{"volume": 0-100}` |
| POST | `/api/zones/<id>/geometry` | `{"x","y","width","height"}` |
| POST | `/api/zones/<id>/tally` | `{"state": "program"}` |
| POST | `/api/zones/<id>/layer` | `{"direction": "top|bottom|up|down"}` |
| POST | `/api/zones/<id>/renumber` | `{"id": 3}`: new ID; if 3 is taken, the two zones swap IDs. A running feed keeps playing |
| POST | `/api/zones/renumber-by-position` | –: renumber all zones 1..n from top-left to bottom-right |

## Multi-zone

| Method | Path | Body |
|---|---|---|
| POST | `/api/play-all` | – (starts every zone with a source) |
| POST | `/api/stop-all` | – |
| POST | `/api/audio` | `{"zone": 3}` (audio follow) or `{"zone": null}` (mute all) |
| POST | `/api/tally` | `{"program": 3, "preview": 1}` (others → off) or `{"1": "program", "4": "standby"}` |
| POST | `/api/layout/template` | `{"template": "2x2", "gap": 4}` or `{"cols": 3, "rows": 2, "gap": 4}` |
| GET | `/api/layout` | – export current layout (zones without runtime state) |
| POST | `/api/layout` | layout object (same format as GET), `"autoplay": true` |

## Presets

| Method | Path | Body |
|---|---|---|
| GET | `/api/presets` | – `{"presets": {...}, "default": "name"}` |
| POST | `/api/presets` | `{"name": "Show A", "description": "..."}` saves the current layout (or pass `"layout"`) |
| GET | `/api/presets/<name>` | – |
| POST | `/api/presets/<name>/load` | optional `{"autoplay": false}` |
| DELETE | `/api/presets/<name>` | – |
| POST | `/api/presets/<name>/set-default` | – loaded at boot |
| POST | `/api/presets/clear-default` | – boot restores last state instead |

### Stable zone IDs

- New zones always get the **lowest free ID** (Quad → Fullscreen → Quad gives 1–4 again, never 5–7).
- Presets store each zone's ID. Loading a preset restores exactly these IDs, so `/api/zones/2/...` always addresses the same tile of a layout.
- Older presets without stored IDs get 1..n in saved order. Save them again to pin their IDs.
- Running feeds are kept while switching: same ID + same source → only moved; same source under another ID → renumbered without reconnect.

## Files

| Method | Path | |
|---|---|---|
| GET | `/api/files` | list uploads |
| POST | `/api/upload` | multipart field `file` |
| DELETE | `/api/files/<name>` | |

## Legacy single-zone endpoints

Kept for existing Node-RED flows. They act on `?zone=<id>` (or `"zone"` in the body), otherwise on the first zone (created fullscreen if none exists):
`POST /api/play`, `/api/stop` (without zone = stop all), `/api/pause`, `/api/geometry`, `/api/volume`.

## Node-RED example: tally from a switcher

```
[http request] POST http://pi:5000/api/tally
msg.payload = {"program": msg.pgm, "preview": msg.pvw}
```
