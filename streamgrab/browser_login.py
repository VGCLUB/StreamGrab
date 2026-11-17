"""User-driven sign-in in an app-owned browser profile; no personal profile access."""
from __future__ import annotations

import ctypes
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import requests
from websockets.sync.client import connect

from .models import UserError
from .storage import state_dir


@dataclass(frozen=True)
class LoginSite:
    key: str
    url: str
    domains: tuple[str, ...]
    urls: tuple[str, ...] = ()


SITES = {
    "B站": LoginSite("bilibili", "https://www.bilibili.com/", ("bilibili.com",),
                    ("https://passport.bilibili.com/", "https://api.bilibili.com/")),
    "小红书": LoginSite("xiaohongshu", "https://www.xiaohongshu.com/explore", ("xiaohongshu.com",),
                      ("https://edith.xiaohongshu.com/",)),
    "抖音": LoginSite("douyin", "https://www.douyin.com/", ("douyin.com", "iesdouyin.com"),
                    ("https://www.iesdouyin.com/",)),
    "TikTok": LoginSite("tiktok", "https://www.tiktok.com/", ("tiktok.com",)),
    "YouTube": LoginSite("youtube", "https://www.youtube.com/", ("youtube.com",)),
}


def domain_matches(domain: str, allowed: tuple[str, ...]) -> bool:
    domain = domain.lower().lstrip(".").rstrip(".")
    return any(domain == base or domain.endswith("." + base) for base in allowed)


def filter_cookies(cookies: list[dict], domains: tuple[str, ...]) -> list[dict]:
    result = {}
    for cookie in cookies:
        name, value, domain = cookie.get("name"), cookie.get("value"), cookie.get("domain")
        if not all(isinstance(v, str) for v in (name, value, domain)) or not name or not domain_matches(domain, domains):
            continue
        expires = cookie.get("expires", -1)
        if not isinstance(expires, (int, float)):
            continue
        if not cookie.get("session", expires <= 0) and 0 < expires <= time.time():
            continue
        item = {
            "name": name, "value": value, "domain": domain, "path": cookie.get("path") or "/",
            "secure": bool(cookie.get("secure")), "httpOnly": bool(cookie.get("httpOnly")),
            "expires": expires, "session": bool(cookie.get("session", expires <= 0)),
        }
        result[(domain, item["path"], name)] = item
    return list(result.values())


def add_cookies_to_jar(jar: http.cookiejar.CookieJar, cookies: list[dict]):
    """Keep domain/path/secure/expiry attributes rather than a global Cookie header."""
    for item in cookies:
        domain = item["domain"]
        session = item.get("session", item.get("expires", -1) <= 0)
        expires = None if session else int(item["expires"])
        if expires is not None and expires <= time.time():
            continue
        jar.set_cookie(http.cookiejar.Cookie(
            0, item["name"], item["value"], None, False, domain, domain.startswith("."),
            domain.startswith("."), item.get("path") or "/", True,
            bool(item.get("secure")), expires, bool(session), None, None,
            {"HttpOnly": None} if item.get("httpOnly") else {}, False,
        ))


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    if os.name != "nt":
        raise UserError("网页登录状态的加密保存目前支持 Windows。")
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_Blob)]
    function.restype = ctypes.c_int
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise UserError("无法保存 / 读取本机登录状态，请在当前 Windows 账号下重新网页登录并同步。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


@dataclass(repr=False)
class LoginSnapshot:
    platform: str
    cookies: list[dict] = field(repr=False)
    user_agent: str = ""
    saved_at: float = field(default_factory=time.time)


class CredentialVault:
    MAGIC = b"STREAMGRAB-AUTH-1\0"

    def __init__(self, root: Path | None = None, *, sites=None, protect=None, unprotect=None):
        self.root = Path(root) if root is not None else state_dir() / "auth"
        self.sites = sites if sites is not None else SITES
        self.protect = protect or (lambda data: _dpapi(data, True))
        self.unprotect = unprotect or (lambda data: _dpapi(data, False))
        self.memory: dict[str, LoginSnapshot] = {}
        self.lock = threading.RLock()

    def _path(self, platform):
        return self.root / (self.sites[platform].key + ".dat")

    def save(self, snapshot: LoginSnapshot, remember: bool):
        with self.lock:
            site = self.sites[snapshot.platform]
            snapshot.cookies = filter_cookies(snapshot.cookies, site.domains)
            if not snapshot.cookies:
                raise UserError("没有同步到该平台的网站状态，请在登录窗口打开平台并完成登录后重试。")
            if remember:
                payload = json.dumps({"platform": snapshot.platform, "cookies": snapshot.cookies,
                                      "user_agent": snapshot.user_agent, "saved_at": snapshot.saved_at},
                                     ensure_ascii=False).encode("utf-8")
                sealed = self.MAGIC + self.protect(payload)
                self.root.mkdir(parents=True, exist_ok=True)
                target = self._path(snapshot.platform)
                temporary = target.with_suffix(".tmp")
                temporary.write_bytes(sealed)
                temporary.replace(target)
            else:
                self._path(snapshot.platform).unlink(missing_ok=True)
            self.memory[snapshot.platform] = snapshot

    def load(self, platform: str) -> LoginSnapshot | None:
        with self.lock:
            if platform not in self.sites:
                return None
            if platform in self.memory:
                snapshot = self.memory[platform]
            else:
                target = self._path(platform)
                if not target.exists():
                    return None
                try:
                    sealed = target.read_bytes()
                    if not sealed.startswith(self.MAGIC):
                        raise ValueError()
                    payload = json.loads(self.unprotect(sealed[len(self.MAGIC):]))
                    if payload["platform"] != platform:
                        raise ValueError()
                    snapshot = LoginSnapshot(platform, payload["cookies"], payload.get("user_agent", ""), payload["saved_at"])
                    self.memory[platform] = snapshot
                except (OSError, ValueError, KeyError, TypeError, UserError) as exc:
                    raise UserError("已保存的登录状态无法读取，请重新点击网页登录并同步。") from exc
            snapshot.cookies = filter_cookies(snapshot.cookies, self.sites[platform].domains)
            return snapshot if snapshot.cookies else None

    def forget(self, platform: str):
        with self.lock:
            self.memory.pop(platform, None)
            if platform in self.sites:
                self._path(platform).unlink(missing_ok=True)


def find_browser() -> Path:
    candidates = []
    for name, suffix in [("PROGRAMFILES(X86)", "Microsoft/Edge/Application/msedge.exe"),
                         ("PROGRAMFILES", "Microsoft/Edge/Application/msedge.exe"),
                         ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
                         ("LOCALAPPDATA", "Microsoft/Edge/Application/msedge.exe"),
                         ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe")]:
        if os.environ.get(name):
            candidates.append(Path(os.environ[name]) / suffix)
    for name in ("msedge", "google-chrome", "chromium", "chromium-browser"):
        if path := shutil.which(name):
            candidates.append(Path(path))
    found = next((path for path in candidates if path.is_file()), None)
    if found is None:
        raise UserError("未找到 Edge 或 Chrome。请先安装其中一个浏览器，或改用高级导入。")
    return found


def _validated_ws(url: str, port: int) -> str:
    parsed = urlparse(url)
    if (parsed.scheme != "ws" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.port != port or parsed.username or parsed.password
            or not re.fullmatch(r"/devtools/(?:browser|page)/[\w-]+", parsed.path)):
        raise UserError("登录窗口的本机连接信息不正确，请关闭该登录窗口后重新打开。")
    return url


class CDP:
    def __init__(self, endpoint: str):
        self.connection = connect(endpoint, proxy=None, open_timeout=5, close_timeout=1, max_size=2 * 1024 * 1024)
        self.serial = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.serial += 1
        self.connection.send(json.dumps({"id": self.serial, "method": method, "params": params or {}}))
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            message = json.loads(self.connection.recv(timeout=max(.1, deadline - time.monotonic())))
            if message.get("id") != self.serial:
                continue
            if message.get("error"):
                raise UserError("浏览器未能同步网站状态，请在登录窗口完成登录后重试。")
            return message.get("result", {})
        raise UserError("登录窗口响应超时，请重新打开登录窗口。")

    def close(self):
        self.connection.close()


class BrowserLogin:
    def __init__(self, root: Path | None = None, *, sites=None, vault=None, executable=None):
        self.root = Path(root) if root is not None else state_dir() / "browser-login"
        self.sites = sites if sites is not None else SITES
        self.vault = vault or CredentialVault(sites=self.sites)
        self.executable = Path(executable) if executable else None
        self.started: set[str] = set()
        self.stopping = False
        self.lock = threading.RLock()

    def _site(self, platform):
        if platform not in self.sites:
            raise UserError("此平台暂不支持快捷网页登录，请使用高级导入。")
        return self.sites[platform]

    def _version(self, platform) -> tuple[int, dict]:
        site = self._site(platform)
        path = self.root / site.key / "DevToolsActivePort"
        parts = path.read_text(encoding="utf-8").strip().splitlines()
        port = int(parts[0])
        if not 0 < port < 65536 or len(parts) < 2:
            raise ValueError()
        expected = _validated_ws(f"ws://127.0.0.1:{port}{parts[1]}", port)
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(f"http://127.0.0.1:{port}/json/version", timeout=1)
            response.raise_for_status()
            version = response.json()
        endpoint = _validated_ws(version["webSocketDebuggerUrl"], port)
        if urlparse(endpoint).path != urlparse(expected).path:
            raise UserError("登录窗口已改变，请重新打开后同步。")
        return port, version

    def start(self, platform: str, *, extra_args: tuple[str, ...] = ()) -> str:
        site = self._site(platform)
        if self.stopping:
            raise UserError("软件正在退出，请重新启动后再登录。")
        with self.lock:
            try:
                _, version = self._version(platform)
                self.started.add(platform)
                if self.stopping:
                    self.close(platform)
                    return ""
                # Bring a platform tab to the dedicated browser, never to the personal profile.
                subprocess.Popen([str(self.executable or find_browser()), f"--user-data-dir={self.root / site.key}",
                                  "--new-window", site.url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return version.get("User-Agent", "")
            except (OSError, ValueError, KeyError, requests.RequestException, UserError):
                pass
            profile = self.root / site.key
            profile.mkdir(parents=True, exist_ok=True)
            # Only this app-owned endpoint marker is replaced; personal browser data is untouched.
            (profile / "DevToolsActivePort").unlink(missing_ok=True)
            subprocess.Popen([str(self.executable or find_browser()), f"--user-data-dir={profile}",
                              "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                              "--no-first-run", "--no-default-browser-check", *extra_args, "--new-window", site.url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    _, version = self._version(platform)
                    self.started.add(platform)
                    if self.stopping:
                        self.close(platform)
                        return ""
                    return version.get("User-Agent", "")
                except (OSError, ValueError, KeyError, requests.RequestException, UserError):
                    time.sleep(.2)
            raise UserError("登录窗口未能连接。请关闭本软件打开的登录窗口后重试；受管理的浏览器也可改用高级导入。")

    def capture(self, platform: str) -> LoginSnapshot:
        site = self._site(platform)
        try:
            port, version = self._version(platform)
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(f"http://127.0.0.1:{port}/json/list", timeout=3)
                response.raise_for_status()
                pages = response.json()
            page = next((page for page in pages if page.get("type") == "page"
                         and domain_matches(urlparse(page.get("url", "")).hostname or "", site.domains)), None)
            if not page:
                raise UserError("请在登录窗口打开所选平台，完成登录后再点击同步。")
            endpoint = _validated_ws(page["webSocketDebuggerUrl"], port)
            client = CDP(endpoint)
            try:
                urls = [site.url, *site.urls, page["url"]]
                values = client.call("Network.getCookies", {"urls": list(dict.fromkeys(urls))})
            finally:
                client.close()
            cookies = filter_cookies(values.get("cookies", []), site.domains)
            if not cookies:
                raise UserError("还没有读取到网站状态。请等待网页加载，并在窗口内登录后再同步。")
            return LoginSnapshot(platform, cookies, version.get("User-Agent", ""))
        except UserError:
            raise
        except Exception as exc:
            raise UserError("登录窗口已关闭或无法连接，请先点击「打开网页登录」，完成登录后再同步。") from exc

    def sync(self, platform: str, remember: bool = True) -> LoginSnapshot:
        snapshot = self.capture(platform)
        self.vault.save(snapshot, remember)
        self.close(platform)
        return snapshot

    def close(self, platform: str):
        if platform not in self.started:
            return
        try:
            port, version = self._version(platform)
            client = CDP(_validated_ws(version["webSocketDebuggerUrl"], port))
            try:
                client.call("Browser.close")
            finally:
                client.close()
        except Exception:
            pass
        self.started.discard(platform)

    def close_all(self):
        self.stopping = True
        for platform in list(self.started):
            self.close(platform)
