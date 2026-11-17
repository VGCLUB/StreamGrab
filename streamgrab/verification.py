"""Headless, explicit CLI verification of one caller-supplied media link."""
import threading
from pathlib import Path

from .download import probe, run_download
from .engine import parse_media
from .models import DownloadSpec, LoginOptions


def verify_video(url: str, report_path: Path) -> dict:
    login = LoginOptions(browser_login=True, remember_login=False)
    media = parse_media(url, login)
    output = report_path.parent / "verified-media"
    video = run_download(DownloadSpec(media, output, login=login), threading.Event(), lambda *_: None)
    data = probe(Path(video[0]))
    result = {"status": "passed", "background": True, "video_duration": float(data["format"]["duration"]),
              "video_resolution": [[s.get("width"), s.get("height")] for s in data["streams"] if s.get("codec_type") == "video"],
              "qualities": [q.key for q in media.qualities], "paths": video}
    if media.has_audio:
        start = 1 if (media.duration or 0) > 4 else 0
        end = min(4, media.duration) if media.duration else 4
        audio = run_download(DownloadSpec(media, output, mode="audio", start=start, end=end, login=login),
                             threading.Event(), lambda *_: None)
        result["audio_clip_duration"] = float(probe(Path(audio[0]))["format"]["duration"])
        result["paths"].extend(audio)
    return result


def verify_recording(url: str, report_path: Path) -> dict:
    from .recording import RecordingSpec, run_recording
    paths = run_recording(RecordingSpec(url, report_path.parent / 'verified-recordings', start=1, end=4),
                          threading.Event(), lambda *_: None)
    result = probe(Path(paths[0]))
    return {'status': 'passed', 'method': 'browser playback recording', 'background': True,
            'selection_seconds': [1, 4], 'duration': float(result['format']['duration']),
            'resolution': [[s.get('width'), s.get('height')] for s in result['streams'] if s.get('codec_type') == 'video'],
            'codecs': [s.get('codec_name') for s in result['streams']], 'paths': paths}
