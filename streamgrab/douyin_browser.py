"""Read the requested video's normal browser response in an isolated background tab."""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

from .browser_login import BrowserLogin, CDP, LoginSnapshot, SITES, _validated_ws, domain_matches, filter_cookies
from .models import UserError, check_cancel


def find_detail(payload, media_id: str) -> dict | None:
    pending = [payload]
    scanned = 0
    while pending and scanned < 100_000:
        item = pending.pop()
        scanned += 1
        if isinstance(item, dict):
            if (str(item.get("aweme_id") or item.get("awemeId")) == media_id
                    and (item.get("video") or item.get("images"))):
                return item
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return None


def is_detail_response(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.scheme == "https" and domain_matches(parsed.hostname or "", SITES["抖音"].domains)
            and parsed.path.rstrip("/") == "/aweme/v1/web/aweme/detail")


def decode_detail(payload: dict, media_id: str) -> dict | None:
    text = payload.get("body") or ""
    if len(text) > 12 * 1024 * 1024:
        return None
    try:
        if payload.get("base64Encoded"):
            text = base64.b64decode(text).decode("utf-8")
        return find_detail(json.loads(text), media_id)
    except (ValueError, UnicodeError, TypeError):
        return None


@dataclass(repr=False)
class BrowserDetail:
    detail: dict = field(repr=False)
    snapshot: LoginSnapshot = field(repr=False)


def fetch_detail(url: str, cancel: threading.Event | None = None, *, service=None, timeout=35) -> BrowserDetail:
    match = re.search(r"/(?:video|note)/(\d+)", url)
    if not match or not domain_matches(urlparse(url).hostname or "", SITES["抖音"].domains):
        raise UserError("请使用具体的抖音视频或图文链接。")
    media_id = match.group(1)
    target_url = f"https://www.douyin.com/{'note' if '/note/' in url else 'video'}/{media_id}"
    cancel = cancel or threading.Event()
    check_cancel(cancel)
    service = service or BrowserLogin()
    browser = page_client = None
    target_id = None
    try:
        # Existing login windows are borrowed without being raised or navigated.
        # A newly needed browser runs headlessly with only this app's dedicated profile.
        service.start("抖音", background=True, initial_url="about:blank")
        check_cancel(cancel)
        port, version = service._version("抖音")
        browser = CDP(_validated_ws(version["webSocketDebuggerUrl"], port))
        target_id = browser.call("Target.createTarget", {"url": "about:blank", "background": True})["targetId"]
        with requests.Session() as local:
            local.trust_env = False
            response = local.get(f"http://127.0.0.1:{port}/json/list", timeout=3)
            response.raise_for_status()
            page = next(item for item in response.json() if item["id"] == target_id)
        page_client = CDP(_validated_ws(page["webSocketDebuggerUrl"], port))
        page_client.call("Network.enable", {"maxTotalBufferSize": 32000000, "maxResourceBufferSize": 8000000})
        page_client.call("Page.navigate", {"url": target_url})
        pending = set()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            check_cancel(cancel)
            try:
                event = page_client.next_event(timeout=min(1, max(.05, deadline - time.monotonic())))
            except TimeoutError:
                continue
            method, params = event.get("method"), event.get("params", {})
            if method == "Network.responseReceived" and is_detail_response(params.get("response", {}).get("url", "")):
                pending.add(params["requestId"])
            elif method == "Network.loadingFinished" and params.get("requestId") in pending:
                request_id = params["requestId"]
                pending.discard(request_id)
                try:
                    body = page_client.call("Network.getResponseBody", {"requestId": request_id})
                except UserError:
                    continue
                detail = decode_detail(body, media_id)
                if detail:
                    check_cancel(cancel)
                    cookies = page_client.call("Network.getCookies", {"urls": [target_url, *SITES["抖音"].urls]})
                    snapshot = LoginSnapshot("抖音", filter_cookies(cookies.get("cookies", []), SITES["抖音"].domains),
                                             version.get("User-Agent", ""))
                    return BrowserDetail(detail, snapshot)
        raise UserError("后台抖音网页没有返回这条作品的数据。请在网页登录窗口打开该作品并完成可能出现的验证，再重试；也请确认作品仍可播放。")
    finally:
        if page_client:
            page_client.close()
        if browser:
            if target_id:
                try:
                    browser.call("Target.closeTarget", {"targetId": target_id})
                except Exception:
                    pass
            browser.close()
        # start(background=True) never claims an already-running browser.
        service.close_all()
