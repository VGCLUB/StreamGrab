import base64
import http.cookiejar
import http.server
import json
import threading
from pathlib import Path

import pytest
import requests
from yt_dlp.utils import DownloadError

from streamgrab import engine
from streamgrab.browser_login import LoginSnapshot
from streamgrab.douyin_browser import BrowserDetail
from streamgrab.download import run_download
from streamgrab.models import Cancelled, DownloadSpec, LoginOptions, UserError


DOUYIN = "https://www.douyin.com/video/123456789"
XHS = "https://www.xiaohongshu.com/explore/abc?xsec_token=fixture"


def video_detail():
    return {"aweme_id": "123456789", "images": None, "desc": "fixture", "video": {
        "duration": 3000, "width": 2160, "height": 3840,
        "play_addr": {"url_list": ["https://media.example.org/fixture.mp4"]}}}


def browser_result(detail=None):
    return BrowserDetail(detail or video_detail(), LoginSnapshot("抖音", [
        {"name": "fixture", "value": "synthetic-only", "domain": ".douyin.com", "session": True}
    ], "FixtureBrowser/99"))


@pytest.fixture
def backend(monkeypatch):
    state = {"error": DownloadError("Failed to decrypt with DPAPI"), "anonymous_error": None,
             "browser_loads": [], "options": []}

    class FakeYDL:
        def __init__(self, options):
            self.params = options
            self.jar = None
            state["options"].append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        @property
        def cookiejar(self):
            if self.jar is None:
                browser = self.params.get("cookiesfrombrowser")
                if browser:
                    state["browser_loads"].append(browser)
                    error = state["error"]
                    if callable(error):
                        error = error()
                else:
                    error = state["anonymous_error"]
                if error:
                    raise error
                self.jar = http.cookiejar.CookieJar()
            return self.jar

    def forbidden_browser(*_):
        raise AssertionError("a real browser must never be launched by these tests")

    original_request = requests.Session.request

    def local_only(self, method, url, *args, **kwargs):
        assert url.startswith("http://127.0.0.1:"), "unexpected external HTTP request"
        return original_request(self, method, url, *args, **kwargs)

    monkeypatch.setattr(engine.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(engine, "extract_douyin", lambda *_: None)
    monkeypatch.setattr(engine, "fetch_douyin_browser", forbidden_browser)
    monkeypatch.setattr(requests.Session, "request", local_only)
    return state


@pytest.mark.parametrize("message", [
    "Could not copy Chrome cookie database", "Failed to decrypt with DPAPI",
    "database is locked", "unable to open database file", "Permission denied",
])
def test_known_cookie_load_failures_retry_once_without_daily_browser(backend, monkeypatch, message):
    backend["error"] = DownloadError(message)
    calls = []
    monkeypatch.setattr(engine, "fetch_douyin_browser", lambda *args: calls.append(args) or browser_result())
    login = LoginOptions(browser="edge")
    media = engine.parse_media(DOUYIN, login)
    assert media.media_id == "123456789" and [q.key for q in media.qualities] == ["best", "2160"]
    assert backend["browser_loads"] == [("edge",)] and len(calls) == 1
    assert len(backend["options"]) == 2
    assert login.browser == "" and login.browser_login is True
    assert login.user_agent == "FixtureBrowser/99" and login.cookie_data[0]["name"] == "fixture"


def test_wrapped_permission_error_is_recognized_at_cookie_loading_boundary(backend, monkeypatch):
    wrapped = DownloadError("failed to load cookies")
    wrapped.__cause__ = PermissionError("local profile is inaccessible")
    backend["error"] = wrapped
    monkeypatch.setattr(engine, "fetch_douyin_browser", lambda *_: browser_result())
    login = LoginOptions(browser="edge")
    assert engine.parse_media(DOUYIN, login).has_video
    assert login.browser == "" and len(backend["options"]) == 2


def test_gallery_download_uses_successful_context_and_never_reopens_failed_source(backend, monkeypatch, tmp_path):
    picture = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(picture)))
            self.end_headers()
            self.wfile.write(picture)

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    detail = {"aweme_id": "123456789", "desc": "fixture gallery", "images": [
        {"url_list": [f"http://127.0.0.1:{server.server_port}/fixture.png"]}]}
    monkeypatch.setattr(engine, "fetch_douyin_browser", lambda *_: browser_result(detail))
    login = LoginOptions(browser="edge")
    try:
        media = engine.parse_media(DOUYIN, login)
        paths = run_download(DownloadSpec(media, tmp_path, mode="images", login=login), threading.Event(), lambda *_: None)
    finally:
        server.shutdown()
        server.server_close()
    assert (Path(paths[0]) / "001.png").read_bytes() == picture
    assert backend["browser_loads"] == [("edge",)]
    assert "cookiesfrombrowser" not in backend["options"][-1]
    assert received[0]["User-Agent"] == "FixtureBrowser/99"
    assert "Cookie" not in received[0]  # Platform cookies remain off an unrelated host.


def test_xhs_public_retry_keeps_explicit_imports_and_context_for_download(backend, monkeypatch, tmp_path):
    cookie_file = tmp_path / "fixture-cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n.xiaohongshu.com\tTRUE\t/\tTRUE\t2147483647\tfile_fixture\tsynthetic-only\n")
    note = {"noteId": "abc", "type": "video", "title": "fixture", "video": {"media": {"stream": {"h264": [
        {"masterUrl": "https://media.example.org/fixture.mp4", "width": 2160, "height": 3840, "duration": 3000}]}}}}
    page = "window.__INITIAL_STATE__=" + json.dumps({"note": {"noteDetailMap": {"abc": {"note": note}}}})

    def public_page(session, url, **_):
        assert url == XHS
        headers = session.prepare_request(requests.Request("GET", url)).headers
        assert "file_fixture=synthetic-only" in headers["Cookie"] and "pasted_fixture=synthetic-only" in headers["Cookie"]
        response = requests.Response()
        response.status_code = 200
        response._content = page.encode()
        response.encoding = "utf-8"
        return response

    monkeypatch.setattr(requests.Session, "get", public_page)
    login = LoginOptions(browser="edge", cookie_file=str(cookie_file), raw_cookie="pasted_fixture=synthetic-only", user_agent="Fixture/1")
    media = engine.parse_media(XHS, login)
    assert [q.key for q in media.qualities] == ["best", "2160"]
    assert login.browser == "" and not login.browser_login
    assert login.cookie_file == str(cookie_file) and login.raw_cookie == "pasted_fixture=synthetic-only"
    assert login.user_agent == "Fixture/1"
    with engine.downloader(login, XHS) as ydl:
        assert ydl.cookiejar
    assert backend["browser_loads"] == [("edge",)]


def test_usable_advanced_browser_keeps_its_credentials_and_high_quality(backend, monkeypatch):
    backend["error"] = None
    monkeypatch.setattr(engine, "extract_douyin", lambda *_: engine.parse_douyin_detail(video_detail(), DOUYIN))
    login = LoginOptions(browser="edge", browser_login=False)
    media = engine.parse_media(DOUYIN, login)
    assert login.browser == "edge" and login.browser_login is False
    assert [q.key for q in media.qualities] == ["best", "2160"]
    assert len(backend["options"]) == 1


@pytest.mark.parametrize("error,expected", [
    (DownloadError("HTTP Error 403: private content"), UserError),
    (requests.Timeout("platform connection timed out"), UserError),
    (Cancelled("cancelled in the extractor"), Cancelled),
])
def test_platform_errors_and_cancel_are_not_cookie_source_failures(backend, monkeypatch, error, expected):
    backend["error"] = None

    def fail(*_):
        raise error

    monkeypatch.setattr(engine, "extract_douyin", fail)
    login = LoginOptions(browser="edge")
    with pytest.raises(expected):
        engine.parse_media(DOUYIN, login)
    assert len(backend["options"]) == 1 and login.browser == "edge"


@pytest.mark.parametrize("error", [DownloadError("Unknown browser container"), Cancelled("permission denied while cancelling")])
def test_unknown_cookie_error_and_cancellation_are_not_retried(backend, error):
    backend["error"] = error
    with pytest.raises(Cancelled if isinstance(error, Cancelled) else UserError):
        engine.parse_media(DOUYIN, LoginOptions(browser="edge"))
    assert len(backend["options"]) == 1


def test_other_platforms_do_not_silently_drop_browser_credentials(backend):
    with pytest.raises(UserError, match="无法读取日常浏览器"):
        engine.parse_media("https://www.bilibili.com/video/BV1xx411c79H", LoginOptions(browser="edge"))
    assert len(backend["options"]) == 1


def test_cancel_after_cookie_failure_prevents_retry(backend):
    cancel = threading.Event()

    def fail_and_cancel():
        cancel.set()
        return PermissionError("profile locked")

    backend["error"] = fail_and_cancel
    with pytest.raises(Cancelled):
        engine.parse_media(DOUYIN, LoginOptions(browser="edge"), cancel)
    assert len(backend["options"]) == 1


def test_failed_retry_is_bounded_and_preserves_original_login(backend):
    backend["anonymous_error"] = PermissionError("local retry failed")
    login = LoginOptions(browser="edge")
    with pytest.raises(PermissionError, match="local retry failed"):
        engine.parse_media(DOUYIN, login)
    assert len(backend["options"]) == 2 and login.browser == "edge" and not login.browser_login


def test_pre_cancelled_parse_never_loads_browser_credentials(backend):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        engine.parse_media(DOUYIN, LoginOptions(browser="edge"), cancel)
    assert not backend["options"]
