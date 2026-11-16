import functools
import http.server
import json
import subprocess
import threading
import zipfile

import pytest

from streamgrab.download import export_media, probe, run_download, tool_path
from streamgrab.engine import parse_media
from streamgrab.models import Cancelled, DownloadSpec, ImageAsset, LoginOptions, Media


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    root = tmp_path_factory.mktemp("origin")
    subprocess.run([tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000", "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(root / "fixture.mp4")], check=True)
    for i, color in enumerate(["red", "blue", "green"]):
        subprocess.run([tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=64x64", "-frames:v", "1", str(root / f"{i}.png")], check=True)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield root, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_real_http_video_download_and_precise_clip(origin, tmp_path):
    _, base = origin
    media = parse_media(base + "/fixture.mp4", LoginOptions())
    paths = run_download(DownloadSpec(media, tmp_path, start=1.2, end=3.6), threading.Event(), lambda *_: None)
    result = probe(paths[0])
    assert abs(float(result["format"]["duration"]) - 2.4) < .12
    assert {s["codec_type"] for s in result["streams"]} == {"video", "audio"}
    assert result["streams"][0]["width"] == 320
    assert not (tmp_path / ".streamgrab-work").exists()


@pytest.mark.parametrize("audio_format", ["mp3", "m4a", "wav", "flac"])
def test_extract_audio_and_range(origin, tmp_path, audio_format):
    root, _ = origin
    media = Media("http://example.org/v.mp4", "其他", "fixture", "1", duration=6)
    spec = DownloadSpec(media, tmp_path, mode="audio", audio_format=audio_format, start=.5, end=2)
    target = tmp_path / ("audio." + audio_format)
    export_media(root / "fixture.mp4", target, spec, threading.Event(), lambda *_: None)
    details = probe(target)
    assert [s["codec_type"] for s in details["streams"]] == ["audio"]
    assert abs(float(details["format"]["duration"]) - 1.5) < .12


def test_gallery_every_picture_in_order_and_zip(origin, tmp_path):
    root, base = origin
    images = [ImageAsset(base + f"/{i}.png") for i in range(3)]
    media = Media(base + "/post", "其他", "测试图文", "123", images=images, has_video=False, has_audio=False)
    paths = run_download(DownloadSpec(media, tmp_path, mode="images"), threading.Event(), lambda *_: None)
    from pathlib import Path
    folder = Path(paths[0])
    assert [f.name for f in sorted(folder.iterdir())] == ["001.png", "002.png", "003.png"]
    for i, file in enumerate(sorted(folder.iterdir())):
        assert file.read_bytes() == (root / f"{i}.png").read_bytes()
    with zipfile.ZipFile(paths[1]) as zf:
        assert len(zf.namelist()) == 3


def test_cancel_during_transfer_cleans_staging(origin, tmp_path):
    _, base = origin
    media = parse_media(base + "/fixture.mp4", LoginOptions())
    cancel = threading.Event()

    def progress(*_):
        cancel.set()

    with pytest.raises(Cancelled):
        run_download(DownloadSpec(media, tmp_path), cancel, progress)
    assert not list(tmp_path.glob("*.mp4"))
    assert not (tmp_path / ".streamgrab-work").exists()


def test_existing_result_never_overwritten(origin, tmp_path):
    _, base = origin
    media = parse_media(base + "/fixture.mp4", LoginOptions())
    first = run_download(DownloadSpec(media, tmp_path), threading.Event(), lambda *_: None)
    second = run_download(DownloadSpec(media, tmp_path), threading.Event(), lambda *_: None)
    assert first != second


def test_partial_gallery_never_succeeds(origin, tmp_path):
    from streamgrab.models import UserError
    _, base = origin
    media = Media(base + "/post", "其他", "图集", "123", images=[ImageAsset(base + "/0.png"), ImageAsset(base + "/missing.png")], has_video=False, has_audio=False)
    with pytest.raises(UserError, match="2 / 2"):
        run_download(DownloadSpec(media, tmp_path, mode="images"), threading.Event(), lambda *_: None)
    assert not list(tmp_path.glob("*.zip"))
    assert not (tmp_path / ".streamgrab-work").exists()


def test_audio_end_to_end_download(origin, tmp_path):
    _, base = origin
    media = parse_media(base + "/fixture.mp4", LoginOptions())
    paths = run_download(DownloadSpec(media, tmp_path, mode="audio", start=1, end=2.5), threading.Event(), lambda *_: None)
    details = probe(paths[0])
    assert [s["codec_type"] for s in details["streams"]] == ["audio"]
    assert abs(float(details["format"]["duration"]) - 1.5) < .12
