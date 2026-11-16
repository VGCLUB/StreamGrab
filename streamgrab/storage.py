from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def state_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "StreamGrab"
    base.mkdir(parents=True, exist_ok=True)
    return base


def load_json(name: str, default):
    try:
        return json.loads((state_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(name: str, value):
    path = state_dir() / name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def resource_path(relative: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / relative


def default_downloads() -> str:
    return str(Path.home() / "Downloads" / "拾流下载")
