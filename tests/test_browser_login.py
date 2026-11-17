import http.cookiejar
import json
import os
import time
from dataclasses import asdict

import pytest
import requests

from streamgrab.browser_login import (CredentialVault, LoginSnapshot, UserError,
                                     _dpapi, _validated_ws, add_cookies_to_jar,
                                     domain_matches, filter_cookies)
from streamgrab.engine import downloader, session_for
from streamgrab.download import choose_format
from streamgrab.models import DownloadSpec, LoginOptions, Media


def cookie(name="sessionid", domain=".douyin.com", **extra):
    return {"name": name, "value": "synthetic-login-token", "domain": domain,
            "path": "/", "secure": True, "httpOnly": True, "expires": time.time() + 3600,
            "session": False, **extra}


def test_captured_cookie_domain_boundary_and_expiry():
    values = [cookie(), cookie(domain=".douyin.com.attacker.example"),
              cookie(domain=".evil-douyin.com"), cookie("expired", expires=time.time() - 10),
              cookie("session", expires=-1, session=True)]
    assert [item["name"] for item in filter_cookies(values, ("douyin.com",))] == ["sessionid", "session"]
    assert domain_matches("www.douyin.com", ("douyin.com",))


def test_snapshot_cookie_reaches_only_matching_https_site(tmp_path):
    with downloader(LoginOptions(cookie_data=[cookie()], user_agent="FixtureBrowser/1.0"), "https://www.douyin.com/") as ydl:
        with session_for(ydl, "https://www.douyin.com/") as session:
            good = session.prepare_request(requests.Request("GET", "https://www.douyin.com/video/1"))
            foreign = session.prepare_request(requests.Request("GET", "https://media.example.org/a.mp4"))
            insecure = session.prepare_request(requests.Request("GET", "http://www.douyin.com/"))
            assert good.headers["Cookie"] == "sessionid=synthetic-login-token"
            assert good.headers["User-Agent"] == "FixtureBrowser/1.0"
            assert "Cookie" not in foreign.headers and "Cookie" not in insecure.headers
            stored = next(iter(ydl.cookiejar))
            assert stored.has_nonstandard_attr("HttpOnly")
    media = Media("https://www.douyin.com/video/1", "抖音", "fixture", "1", info={
        "id": "1", "title": "fixture", "formats": [{"url": "https://media.example.org/video.mp4", "vcodec": "h264", "acodec": "aac",
                                                  "http_headers": {"User-Agent": "old", "Referer": "https://www.douyin.com/"}}]})
    info, _ = choose_format(DownloadSpec(media, tmp_path, login=LoginOptions(user_agent="FixtureBrowser/1.0")))
    assert info["http_headers"]["User-Agent"] == "FixtureBrowser/1.0"
    assert info["formats"][0]["http_headers"] == {"User-Agent": "FixtureBrowser/1.0", "Referer": "https://www.douyin.com/"}


@pytest.mark.parametrize("url", ["ws://example.org:9222/devtools/browser/abc", "wss://127.0.0.1:9222/devtools/page/abc",
                               "ws://127.0.0.1:9000/devtools/browser/abc", "ws://user:password@127.0.0.1:9222/devtools/page/abc",
                               "ws://127.0.0.1:9222/arbitrary"])
def test_reject_non_owned_or_non_loopback_endpoint(url):
    with pytest.raises(UserError):
        _validated_ws(url, 9222)


def test_vault_uses_protector_and_forgets_session_only_snapshot(tmp_path):
    # A deterministic fake codec checks storage wiring; Windows DPAPI is tested separately.
    encoded = {}

    def protect(payload):
        encoded[b"opaque-fixture"] = payload
        return b"opaque-fixture"

    vault = CredentialVault(tmp_path, protect=protect, unprotect=lambda value: encoded[value])
    snapshot = LoginSnapshot("抖音", [cookie(), cookie(domain=".unrelated.example")], "Fixture/1")
    vault.save(snapshot, True)
    file = tmp_path / "douyin.dat"
    assert b"synthetic-login-token" not in file.read_bytes()
    fresh = CredentialVault(tmp_path, protect=protect, unprotect=lambda value: encoded[value])
    restored = fresh.load("抖音")
    assert len(restored.cookies) == 1 and restored.cookies[0]["httpOnly"]
    assert restored.user_agent == "Fixture/1"
    fresh.save(restored, False)
    assert not file.exists() and fresh.load("抖音") is not None
    fresh.forget("抖音")
    assert fresh.load("抖音") is None


def test_corrupt_vault_is_actionable_and_does_not_expose_values(tmp_path):
    (tmp_path / "douyin.dat").write_bytes(b"corrupt-private-data")
    with pytest.raises(UserError, match="重新") as error:
        CredentialVault(tmp_path).load("抖音")
    assert "corrupt-private-data" not in str(error.value)


@pytest.mark.skipif(os.name != "nt", reason="DPAPI belongs to the Windows user account")
def test_windows_dpapi_roundtrip_and_saved_cookie_reuse(tmp_path):
    payload = b"fixture-secret-never-published"
    sealed = _dpapi(payload, True)
    assert payload not in sealed and _dpapi(sealed, False) == payload
    vault = CredentialVault(tmp_path)
    vault.save(LoginSnapshot("抖音", [cookie()]), True)
    assert b"synthetic-login-token" not in (tmp_path / "douyin.dat").read_bytes()
    assert CredentialVault(tmp_path).load("抖音").cookies[0]["name"] == "sessionid"


def test_login_snapshot_repr_never_prints_cookie():
    snapshot = LoginSnapshot("抖音", [cookie()])
    options = LoginOptions(cookie_data=snapshot.cookies)
    assert "synthetic-login-token" not in repr(snapshot) + repr(options)


def test_sync_recovers_a_closed_app_owned_login_window(monkeypatch, tmp_path):
    from streamgrab.browser_login import BrowserLogin
    profile = tmp_path / "browser/douyin"
    profile.mkdir(parents=True)
    (profile / "Local State").write_text("{}")
    vault = CredentialVault(tmp_path / "vault")
    service = BrowserLogin(tmp_path / "browser", vault=vault)
    snapshot = LoginSnapshot("抖音", [cookie()])
    attempts = []

    def capture(_):
        attempts.append("capture")
        if len(attempts) == 1:
            raise UserError("window closed")
        return snapshot

    launches = []
    monkeypatch.setattr(service, "capture", capture)
    monkeypatch.setattr(service, "start", lambda platform, **kwargs: launches.append((platform, kwargs)))
    assert service.sync("抖音", False) is snapshot
    assert launches == [("抖音", {"background": True})]
    assert vault.load("抖音") is snapshot
