"""Read-only public-platform parse check; never reads the user's browser cookies."""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamgrab.engine import parse_media, public_error
from streamgrab.models import LoginOptions

links = [
    "https://www.bilibili.com/video/BV1xx411c79H",
    "https://www.douyin.com/video/6961737553342991651",
    "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9",
]
results = []
for url in links:
    started = time.monotonic()
    try:
        media = parse_media(url, LoginOptions())
        row = {"url": url, "status": "parsed", "platform": media.platform, "title": media.title,
               "duration": media.duration, "qualities": [q.label for q in media.qualities], "images": len(media.images)}
    except Exception as exc:
        row = {"url": url, "status": "needs_login_or_unavailable", "message": public_error(exc)}
    row["elapsed_seconds"] = round(time.monotonic() - started, 2)
    results.append(row)
    print(json.dumps(row, ensure_ascii=True), flush=True)
Path(__file__).with_name("live-platform-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
