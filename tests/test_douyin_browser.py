import base64
import json
import threading
from contextlib import contextmanager

import pytest

from streamgrab import douyin_browser as module
from streamgrab.browser_login import BrowserLogin, LoginSnapshot
from streamgrab.engine import parse_douyin_detail
from streamgrab.models import Cancelled, LoginOptions, UserError


def detail(media_id="123456789"):
    return {"aweme_id": media_id, "desc": "fixture clip", "images": None,
            "video": {"duration": 3000, "width": 1080, "height": 1920,
                      "play_addr": {"url_list": ["https://media.example.org/video.mp4"]}}}


def test_real_video_schema_with_null_images_is_valid():
    media = parse_douyin_detail(detail(), "https://www.douyin.com/video/123456789")
    assert media.has_video and not media.images and media.duration == 3


def test_partial_video_response_is_not_misclassified_as_background_music():
    payload = {"aweme_id": "123456789", "images": None, "video": {"duration": 3000},
               "music": {"play_url": {"url_list": ["https://media.example.org/background-music.mp3"]}}}
    media = parse_douyin_detail(payload, "https://www.douyin.com/video/123456789")
    assert not media.has_audio and not media.has_video


def test_browser_response_matches_only_the_requested_work():
    target = detail()
    assert module.find_detail({"aweme_list": [detail("999999999"), target]}, "123456789") is target
    assert module.find_detail({"aweme_detail": detail("999999999")}, "123456789") is None


def test_only_the_platform_detail_endpoint_is_read():
    assert module.is_detail_response("https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=123456789")
    assert not module.is_detail_response("https://www.douyin.com.attacker.example/aweme/v1/web/aweme/detail/")
    assert not module.is_detail_response("https://www.douyin.com/aweme/v1/web/user/detail/")


def test_base64_browser_response_and_invalid_payload():
    body = json.dumps({"aweme_detail": detail()}).encode()
    assert module.decode_detail({"body": base64.b64encode(body).decode(), "base64Encoded": True}, "123456789") == detail()
    assert module.decode_detail({"body": "verification page"}, "123456789") is None


def test_existing_login_browser_is_not_raised_or_claimed(monkeypatch, tmp_path):
    service = BrowserLogin(tmp_path)
    monkeypatch.setattr(service, "_version", lambda _: (9222, {"User-Agent": "Fixture/1"}))

    def forbidden(*args, **kwargs):
        raise AssertionError("background reuse must not launch or raise a window")

    monkeypatch.setattr("streamgrab.browser_login.subprocess.Popen", forbidden)
    assert service.start("抖音", background=True, initial_url="about:blank") == "Fixture/1"
    assert not service.started


def test_browser_fallback_passes_fresh_context_to_download(monkeypatch):
    from streamgrab import engine
    result = module.BrowserDetail(detail(), LoginSnapshot("抖音", [{"name": "fixture", "value": "synthetic-only", "domain": ".douyin.com", "session": True}], "Fixture/1"))
    partial = parse_douyin_detail({"aweme_id": "123456789", "images": None,
                                  "video": {"download_addr": {"url_list": ["https://media.example.org/marked.mp4"]}}},
                                 "https://www.douyin.com/video/123456789")
    assert partial.has_video and not partial.qualities
    monkeypatch.setattr(engine, "extract_douyin", lambda *_: partial)
    monkeypatch.setattr(engine, "fetch_douyin_browser", lambda *_: result)
    login = LoginOptions(browser_login=True)
    media = engine.parse_media("https://www.douyin.com/video/123456789", login)
    assert media.media_id == "123456789" and login.cookie_data == result.snapshot.cookies
    assert all(f["http_headers"]["User-Agent"] == "Fixture/1" for f in media.info["formats"])


def test_pre_cancelled_browser_parse_never_starts_a_browser():
    event = threading.Event()
    event.set()
    with pytest.raises(Cancelled):
        module.fetch_detail("https://www.douyin.com/video/123456789", event)
