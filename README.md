# rpi-web-multiviewer

**Repo:** https://github.com/Felix2180/rpi-web-multiviewer

Web-gesteuerter Multiviewer für Raspberry Pi 5 und Pi 4 auf Basis von
[raspberry-pi-single-zone-video-player](https://github.com/keep-on-walking/raspberry-pi-single-zone-video-player).
Statt einer Videozone gibt es beliebig viele **Kacheln**. Jede Kachel ist ein eigener mpv-Prozess
mit eigener Quelle, eigenem Label, Tally-Rahmen und eigenem Watchdog.

## Features

- **Mehrere Kacheln** frei positionier- und skalierbar (Drag & Drop im Browser, Änderung live per `xdotool`, ohne den Feed neu zu starten)
- **Vorlagen**: `1`, `2x1`, `1x2`, `2x2`, `3x2`, `3x3`, `4x4`, `1+3`, `1+5`, `1+7`, `2+8` sowie ein frei wählbares Raster (bis 8×8) mit Abstand (Gap)
- **Quellen pro Kachel**: lokale Dateien, `rtsp://`, `srt://`, `udp://`, `rtmp://`, `http(s)://` (HLS), Testbilder (`testpattern`, `testpattern-motion`, `black`)
- **Label/UMD** pro Kachel (6 Positionen), eingeblendet über das OSD von mpv
- **Tally-Rahmen**: PGM (rot), PVW (grün), STBY (gelb), steuerbar per API (z. B. aus Node-RED/Companion)
- **Audio-Follow**: immer nur eine Kachel ist hörbar, umschaltbar
- **Watchdog pro Kachel**: erkennt abgestürzte oder eingefrorene Streams, reconnectet mit Backoff und blendet „RECONNECTING / NO SIGNAL“ ein. Die anderen Kacheln laufen unbeeinflusst weiter.
- **Layout-Presets** speichern Geometrie, Quellen, Labels, Tally und Audio. Beim Umschalten werden laufende Feeds mit derselben Quelle **weiterverwendet** (kein Schwarzbild beim Layoutwechsel).
- **Boot-Verhalten**: Das als ⭐ markierte Preset wird beim Start geladen; ohne Default-Preset wird der letzte Zustand wiederhergestellt.
- **Low latency** (für Live-Streams) und **Low CPU** (Loopfilter überspringen, Frames droppen) pro Kachel
- **Kompatibel** mit den alten Single-Zone-Endpunkten (`/api/play`, `/api/stop`, `/api/geometry` …), die auf die erste Kachel bzw. `?zone=<id>` wirken

## Installation

Voraussetzung: Raspberry Pi OS Lite (64-bit), frisch aufgesetzt, Netzwerkverbindung.

**Einzeiler** (per SSH auf dem Pi):

```bash
curl -sSL https://raw.githubusercontent.com/Felix2180/rpi-web-multiviewer/main/install.sh | sudo bash
```

**Oder manuell:**

```bash
git clone https://github.com/Felix2180/rpi-web-multiviewer.git
cd rpi-web-multiviewer
sudo bash install.sh
sudo reboot
```

Danach `http://<pi-ip>:5000` öffnen. Der Bildschirm bleibt schwarz, bis eine Kachel läuft.

### Update

```bash
cd rpi-web-multiviewer
git pull
sudo bash install.sh
sudo reboot
```

Zustand (`config/state.json`), Presets (`config/layouts.json`) und hochgeladene Videos bleiben beim Update erhalten.

Das Upgrade über eine bestehende Single-Zone-Installation funktioniert (gleiche Pfade unter `/opt/rpi-video-player`).
Der Installer entfernt dabei den alten `stream-watchdog`-Dienst. Der hat bei jedem Neustart **alle** mpv-Prozesse beendet
und würde den Multiviewer komplett abschießen. Alte Geometrie-Presets (`presets.json`) werden nicht übernommen,
die neuen Layouts liegen in `config/layouts.json`.

Neu gegenüber dem Original:
- `xdotool` wird installiert (Kacheln ohne Neustart verschieben)
- der X-Server startet mit `-s 0 -dpms`, damit der Bildschirm nicht nach 10 Minuten schwarz wird

## Bedienung

1. Vorlage wählen (z. B. `2x2`), oder per **＋ Add tile** einzeln Kacheln anlegen
2. Kachel anklicken → rechts Name und Quelle eintragen → **▶ Play**
3. Kacheln ziehen oder Ecken greifen. Die Änderung wird beim Loslassen übernommen. Snap 4/8/20 px.
4. Tally, Audio-Follow, Label-Position und Low-Latency/Low-CPU pro Kachel einstellen
5. **Layout preset** speichern, mit ☆ als Boot-Layout markieren

Beim Anwenden einer Vorlage behalten die vorhandenen Kacheln ihre Quellen (in Leserichtung oben-links → unten-rechts).
Fehlende Kacheln werden leer angelegt, überzählige gestoppt und entfernt.

## Performance – realistisch planen

Jede Kachel dekodiert ihren Stream komplett, egal wie klein sie dargestellt wird. Auf dem **Pi 5** gilt:

| Codec | Dekodierung | Grobe Richtwerte |
|---|---|---|
| HEVC/H.265 | Hardware | mehrere 1080p-Streams möglich (das HW-Dekoder-Budget teilen sich alle Kacheln, ca. 4K60 gesamt) |
| H.264 | **nur Software** (der Pi 5 hat keinen H.264-HW-Dekoder) | ca. 2× 1080p30 **oder** 4–6× 720p/D1-Substreams |

Empfehlungen:
- Bei IP-Kameras für den Multiviewer den **Substream** nehmen (z. B. 640×360 oder 720p), nicht den 4K-Mainstream
- Kameras möglichst auf **H.265** stellen
- Bei vielen H.264-Kacheln **Low CPU** aktivieren und in den Settings `decoder_threads` auf `1` setzen
- Mit `htop` prüfen, wie die CPU-Last ist; eine Kachel ruckelt erst, wenn alle Kerne am Anschlag sind

Diese Zahlen sind Schätzwerte. Es lohnt sich, die eigenen Kamera-Streams einmal mit `testpattern`-Kacheln als Referenz durchzumessen.

### Raspberry Pi 4

Der Multiviewer läuft auch auf dem Pi 4. Unterschiede zum Pi 5:

- Der Pi 4 hat einen **H.264-Hardware-Decoder** (ca. 1080p60 gesamt, geteilt über alle Kacheln), also grob 2× 1080p30 oder ca. 4× 720p30 in Hardware. Der Rest läuft in Software auf der deutlich schwächeren CPU.
- Ob `hwdec=auto` den H.264-Decoder nutzt, kann je nach mpv-Version variieren. Im Zweifel explizit setzen und mit `htop` prüfen:
  ```bash
  curl -X POST http://<pi>:5000/api/settings -H 'Content-Type: application/json' -d '{"hwdec":"v4l2m2m-copy"}'
  ```
- Startet X nicht: mit `ls /dev/dri/` prüfen, ob `card0` statt `card1` existiert, und `/etc/X11/xorg.conf.d/20-modesetting.conf` anpassen. Den HDMI-Ausgangsnamen für `xrandr` im `x11-server.service` mit `DISPLAY=:1 xrandr` prüfen.
- RAM: ca. 100–150 MB pro Kachel.

Die Pi-4-Angaben sind Schätzwerte und nicht auf echter Hardware gemessen.

## Einstellungen (`POST /api/settings`)

| Key | Default | Bedeutung |
|---|---|---|
| `vo` | `xv` | Video-Output. `gpu` liefert schärfere Labels und Tally-Rahmen (das OSD wird bei `xv` mit dem Video skaliert), ausprobieren |
| `hwdec` | `auto` | Hardware-Dekodierung |
| `decoder_threads` | `2` | Software-Decoder-Threads **pro Kachel** |
| `stretch` | `true` | `true` = Bild füllt die Kachel, `false` = Letterbox innerhalb der Kachel |
| `gap` | `4` | Standard-Abstand für Vorlagen (px) |
| `watchdog` | `true` | Auto-Reconnect an/aus |
| `freeze_timeout` | `12` | Sekunden ohne Fortschritt → Stream gilt als eingefroren |
| `extra_mpv_args` | `[]` | zusätzliche mpv-Argumente für alle Kacheln |

Settings, die den mpv-Start betreffen, wirken beim nächsten Play bzw. Reconnect der jeweiligen Kachel.

## HTTP-API (Kurzfassung)

Siehe [API.md](API.md) für alle Endpunkte.

```bash
# 2x2-Raster
curl -X POST http://pi:5000/api/layout/template -H 'Content-Type: application/json' -d '{"template":"2x2"}'

# Kachel 1 mit Kamera starten
curl -X POST http://pi:5000/api/zones/1/play -H 'Content-Type: application/json' \
     -d '{"source":"rtsp://admin:pw@10.0.0.11:554/stream2","name":"CAM 1"}'

# Tally: Kachel 3 PGM, Kachel 1 PVW (alle anderen aus)
curl -X POST http://pi:5000/api/tally -H 'Content-Type: application/json' -d '{"program":3,"preview":1}'

# Audio auf Kachel 2
curl -X POST http://pi:5000/api/audio -H 'Content-Type: application/json' -d '{"zone":2}'

# Preset laden
curl -X POST http://pi:5000/api/presets/Show%20A/load
```

## Troubleshooting

```bash
sudo systemctl status x11-server video-player
tail -f /opt/rpi-video-player/logs/app.log        # inkl. "Watchdog: zone X ... restart"
DISPLAY=:1 xdotool search --name '^mvzone-'        # laufende Kachel-Fenster
cat /opt/rpi-video-player/config/state.json        # aktueller Zustand
```

- **Kachel bleibt schwarz:** Quelle prüfen mit `DISPLAY=:1 mpv <url>`. Bei RTSP Benutzer/Passwort in der URL kontrollieren.
- **Verschieben startet den Feed neu:** `xdotool` fehlt (`sudo apt install xdotool`).
- **Labels fehlen:** Mit `vo=xv` muss das OSD-Rendering funktionieren. Sonst `vo` auf `gpu` umstellen.
- **Kacheln überlagern sich falsch:** Mit „⬆ Front“ / „⬇ Back“ die Stapelreihenfolge ändern (wichtig für Bild-in-Bild).

## Dateistruktur

```
/opt/rpi-video-player/
├── src/
│   ├── video_controller.py   # Flask-API + Startlogik
│   ├── multiviewer.py        # Kachel-Verwaltung, Vorlagen, Watchdog, Persistenz
│   ├── zone_player.py        # ein mpv-Prozess pro Kachel (IPC, Overlay, xdotool)
│   └── layout_manager.py     # Layout-Presets
├── web/                      # Dashboard
├── config/
│   ├── state.json            # letzter Zustand (Resume nach Reboot)
│   └── layouts.json          # Presets
└── data/videos/              # Uploads
```

## Issues & Beiträge

Fehler und Wünsche bitte als Issue: https://github.com/Felix2180/rpi-web-multiviewer/issues

## Lizenz

MIT (wie das Original), siehe LICENSE. Basiert auf
[keep-on-walking/raspberry-pi-single-zone-video-player](https://github.com/keep-on-walking/raspberry-pi-single-zone-video-player).
