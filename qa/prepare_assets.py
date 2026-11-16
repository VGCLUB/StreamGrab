"""Prepare the app icon, bundle existing FFmpeg and preserve dependency notices."""
import importlib.metadata
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QGuiApplication, QIcon

root = Path(__file__).resolve().parents[1]
app = QGuiApplication([])
icon = QIcon(str(root / "assets/app.svg"))
assert icon.pixmap(128, 128).save(str(root / "assets/app.ico"), "ICO")
tools = root / "assets/ffmpeg"
tools.mkdir(parents=True, exist_ok=True)
for name in ("ffmpeg", "ffprobe"):
    target = tools / (name + ".exe")
    if not target.exists():
        source = shutil.which(name)
        if not source:
            raise SystemExit(f"Missing {name}; put the existing tool in assets/ffmpeg first.")
        shutil.copy2(source, target)

for name in ("PySide6-Essentials", "shiboken6", "yt-dlp", "yt-dlp-ejs", "requests", "urllib3", "idna", "charset-normalizer", "certifi", "pyinstaller", "pycryptodomex", "brotli", "mutagen", "websockets"):
    dist = importlib.metadata.distribution(name)
    destination = root / "licenses" / name
    destination.mkdir(parents=True, exist_ok=True)
    metadata = dist.read_text("METADATA") or ""
    # Preserve the full metadata, license directories and notices in the installed wheel.
    (destination / "METADATA.txt").write_text(metadata, encoding="utf-8")
    for file in dist.files or []:
        if ".dist-info/" in str(file).replace("\\", "/") and any(key in str(file).lower() for key in ("license", "copying", "notice")):
            source = Path(dist.locate_file(file))
            if source.is_file():
                relative = Path(*file.parts[1:])
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
print("Assets and third-party notices ready.")
