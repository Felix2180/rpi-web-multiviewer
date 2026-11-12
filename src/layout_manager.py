#!/usr/bin/env python3
"""
Layout presets for the multiviewer.
A preset stores the complete picture: every zone with geometry, source, label,
tally, audio flag and stream options.
"""

import json
from pathlib import Path


class LayoutManager:
    def __init__(self, layouts_file="/opt/rpi-video-player/config/layouts.json"):
        self.file = Path(layouts_file)
        self.layouts = {}
        self.default = None
        self._load()

    def _load(self):
        if not self.file.exists():
            return
        try:
            data = json.loads(self.file.read_text())
            self.default = data.pop("_default", None)
            self.layouts = data
            print(f"Loaded {len(self.layouts)} layout presets")
        except Exception as e:  # noqa: BLE001
            print(f"Error loading layouts: {e}")

    def _save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        data = dict(self.layouts)
        if self.default:
            data["_default"] = self.default
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.file)

    def list(self):
        return {name: {"description": l.get("description", ""),
                       "zones": len(l.get("zones", [])),
                       "display": l.get("display")}
                for name, l in self.layouts.items()}

    def get(self, name):
        return self.layouts.get(name)

    def save(self, name, layout, description=""):
        if not name or name.startswith("_"):
            raise ValueError("Invalid preset name")
        layout = dict(layout)
        layout["description"] = description
        self.layouts[name] = layout
        self._save()

    def delete(self, name):
        if name not in self.layouts:
            return False
        del self.layouts[name]
        if self.default == name:
            self.default = None
        self._save()
        return True

    def set_default(self, name):
        if name is not None and name not in self.layouts:
            return False
        self.default = name
        self._save()
        return True
