from __future__ import annotations

import html
import http.cookiejar
import json
import re
import threading
from collections import deque
from contextlib import contextmanager
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, unquote, urlparse

import requests
import yt_dlp
from yt_dlp.utils import DownloadError, js_to_json

from .models import (ImageAsset, LoginOptions, Media, UserError, check_cancel,
                     extract_url, platform_for, qualities_for, video_formats)
from .storage import resource_path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


class QuietLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


def ydl_options():
    return {
        "quiet": True, "no_warnings": True, "logger": QuietLogger(),
        "noplaylist": True, "playlistend": 1, "socket_timeout": 20,
        "retries": 2, "extractor_retries": 1, "cachedir": False,
        "windowsfilenames": True, "http_headers": {"User-Agent": UA},
        "ffmpeg_location": str(resource_path("assets/ffmpeg")) if resource_path("assets/ffmpeg/ffmpeg.exe").exists() else None,
    }


@contextmanager
def downloader(login: LoginOptions, url: str, **extra):
    opts = ydl_options()
    if login.browser:
        opts["cookiesfrombrowser"] = (login.browser,)
    opts.update(extra)
    with yt_dlp.YoutubeDL(opts) as ydl:
        # Loading into the jar avoids rewriting the user's original cookie file.
        jar = ydl.cookiejar
        if login.cookie_file:
            try:
                imported = http.cookiejar.MozillaCookieJar(login.cookie_file)
                imported.load(ignore_discard=True, ignore_expires=True)
                for cookie in imported:
                    jar.set_cookie(cookie)
            except (OSError, http.cookiejar.LoadError) as exc:
                raise UserError("Cookie 文件无法读取，请导入 Netscape 格式的 cookies.txt。") from exc
        if login.raw_cookie.strip():
            domain = urlparse(url).hostname or ""
            short_platform = platform_for(url)
            domain = {"小红书": ".xiaohongshu.com", "抖音": ".douyin.com", "B站": ".bilibili.com"}.get(short_platform, domain)
            for suffix in ("douyin.com", "xiaohongshu.com", "bilibili.com", "tiktok.com", "youtube.com"):
                if domain == suffix or domain.endswith("." + suffix):
                    domain = "." + suffix
                    break
            raw = login.raw_cookie.strip()
            if raw.lower().startswith("cookie:"):
                raw = raw[7:].strip()
            try:
                parsed = SimpleCookie()
                parsed.load(raw)
            except Exception as exc:
                raise UserError("Cookie 格式不正确，请粘贴 name=value; name2=value2 形式的内容。") from exc
            if not parsed:
                raise UserError("没有识别到有效 Cookie。")
            for name, value in parsed.items():
                jar.set_cookie(http.cookiejar.Cookie(
                    0, name, value.value, None, False, domain, True, domain.startswith("."),
                    "/", True, False, None, True, None, None, {}, False))
        yield ydl


def session_for(ydl, url: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": url})
    # CookieJar enforces domain/path matching, including after redirects.
    session.cookies = ydl.cookiejar
    return session


def public_error(exc: Exception) -> str:
    if isinstance(exc, UserError):
        return str(exc)
    text = str(exc)
    lower = text.lower()
    if any(k in lower for k in ("decrypt", "dpapi", "cookie database", "could not copy", "permission denied")):
        return "无法读取浏览器登录信息。请关闭该浏览器后重试，或在「登录设置」导入 cookies.txt / 粘贴 Cookie。"
    if any(k in lower for k in ("fresh cookies", "login required", "sign in", "log in", "not logged", "403", "412", "验证码", "captcha", "security", "初始数据")):
        return "平台要求登录或安全验证。请先在浏览器正常打开链接，再在「登录设置」为该平台导入最新 Cookie 后重新解析。"
    if any(k in lower for k in ("timeout", "timed out", "connection", "name resolution", "proxyerror")):
        return "连接平台失败或超时，请检查网络后重试。海外平台需本机网络可访问。"
    if "unsupported url" in lower:
        return "暂不支持这个链接。请使用具体的视频或图文详情页链接。"
    if any(k in lower for k in ("private", "unavailable", "not available", "404", "deleted", "not found")):
        return "内容不存在、已删除，或当前登录账号没有访问权限。"
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = re.sub(r"https?://\S+", "[平台地址]", text)
    return "解析 / 下载失败：" + text.removeprefix("ERROR: ")[:350]


def assigned_json(page: str, marker: str) -> dict:
    match = re.search(marker, page)
    if not match:
        return {}
    tail = page[match.end():].lstrip()
    if not tail.startswith(("{", "[")):
        return {}
    # Capture only the balanced value, excluding arbitrary trailing JavaScript.
    stack, quote, escape = [], "", False
    for index, char in enumerate(tail):
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack:
                return {}
            stack.pop()
            if not stack:
                try:
                    return json.loads(js_to_json(tail[:index + 1]))
                except (ValueError, TypeError):
                    return {}
    return {}


def walk_dicts(root):
    pending = deque([root])
    seen = 0
    while pending and seen < 200_000:
        value = pending.popleft()
        seen += 1
        if isinstance(value, dict):
            yield value
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def valid_url(value) -> str:
    if not isinstance(value, str):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    return value if urlparse(value).scheme in ("http", "https") else ""


def image_asset(item: dict) -> ImageAsset | None:
    urls = []
    for key in ("urlOriginal", "originalUrl", "original_url", "urlDefault", "url", "src"):
        if url := valid_url(item.get(key)):
            urls.append(url)
    for entry in item.get("infoList") or []:
        if url := valid_url(entry.get("url")):
            urls.append(url)
    for key in ("url_list", "urlList", "download_url_list"):
        urls.extend(u for u in item.get(key, []) if valid_url(u))
    urls = list(dict.fromkeys(urls))
    if not urls:
        return None
    return ImageAsset(urls[0], item.get("width") or 0, item.get("height") or 0, urls[1:])


def parse_xhs_state(page: str, url: str) -> Media:
    state = assigned_json(page, r"window\.__INITIAL_STATE__\s*=\s*")
    note_id = re.search(r"/(?:explore|discovery/item)/([\da-f]+)", url)
    note_id = note_id.group(1) if note_id else ""
    notes = state.get("note", {}).get("noteDetailMap", {})
    note = notes.get(note_id, {}).get("note", {})
    if not note:
        # Never silently take a recommended/related note.
        note = next((v.get("note", {}) for v in notes.values()
                     if v.get("note", {}).get("noteId") == note_id), {})
    if not note:
        raise UserError("未能读取小红书正文初始数据。请使用带 xsec_token 的完整分享链接，并在登录设置导入最新 Cookie。")
    images = [a for item in note.get("imageList", []) if (a := image_asset(item))]
    user = note.get("user") or {}
    formats = []
    for streams in (note.get("video", {}).get("media", {}).get("stream") or {}).values():
        if not isinstance(streams, list):
            continue
        for index, stream in enumerate(streams):
            if not (source := valid_url(stream.get("masterUrl"))):
                continue
            codec = str(stream.get("videoCodec") or "h264")
            formats.append({
                "url": source, "format_id": f"{codec}-{len(formats)}", "ext": "mp4",
                "width": stream.get("width"), "height": stream.get("height"),
                "vcodec": codec, "acodec": stream.get("audioCodec") or "aac",
                "filesize": stream.get("size"), "tbr": (stream.get("avgBitrate") or 0) / 1000,
                "format_note": stream.get("qualityType") or "播放源",
                "http_headers": {"Referer": url, "User-Agent": UA},
            })
    duration = next((s.get("duration") for values in (note.get("video", {}).get("media", {}).get("stream") or {}).values()
                     if isinstance(values, list) for s in values if s.get("duration")), None)
    title = note.get("title") or (note.get("desc") or "小红书笔记").split("\n")[0][:100]
    info = {"id": note_id, "title": title, "formats": formats, "webpage_url": url, "extractor": "XiaoHongShu",
            "http_headers": {"Referer": url, "User-Agent": UA}}
    return Media(url, "小红书", title, note_id, user.get("nickname") or "", duration / 1000 if duration else None,
                 images[0].url if images else "", info, images if note.get("type") != "video" else [],
                 qualities_for(info), bool(formats), bool(formats))


def parse_douyin_detail(detail: dict, url: str) -> Media:
    media_id = str(detail.get("aweme_id") or detail.get("awemeId") or "douyin")
    title = detail.get("desc") or detail.get("title") or "抖音作品"
    images = [asset for item in detail.get("images", []) if (asset := image_asset(item))]
    video = detail.get("video") or {}
    formats = []
    addresses = [("play_addr", video.get("play_addr") or video.get("playAddr"), False)]
    addresses += [(f"bitrate-{i}", b.get("play_addr") or b.get("playAddr"), False)
                  for i, b in enumerate(video.get("bit_rate") or video.get("bitRate") or [])]
    addresses.append(("download", video.get("download_addr") or video.get("downloadAddr"), bool(video.get("has_watermark", True))))
    for key, addr, marked in addresses:
        if not isinstance(addr, dict):
            continue
        for i, source in enumerate(addr.get("url_list") or addr.get("urlList") or []):
            if not valid_url(source):
                continue
            formats.append({"url": source, "format_id": f"{key}-{i}", "ext": "mp4", "vcodec": "h264", "acodec": "aac",
                            "width": addr.get("width") or video.get("width"), "height": addr.get("height") or video.get("height"),
                            "has_watermark": marked, "format_note": "watermarked" if marked else "播放源",
                            "http_headers": {"Referer": "https://www.douyin.com/", "User-Agent": UA}})
    music = detail.get("music") or {}
    play = music.get("play_url") or music.get("playUrl") or {}
    music_urls = play.get("url_list") or play.get("urlList") or []
    if not formats and music_urls:
        formats.append({"format_id": "music", "url": music_urls[0], "ext": "mp3", "vcodec": "none", "acodec": "mp3"})
    duration = video.get("duration")
    title = title[:160]
    info = {"id": media_id, "title": title, "webpage_url": url, "extractor": "Douyin",
            "formats": formats, "http_headers": {"Referer": "https://www.douyin.com/", "User-Agent": UA}}
    author = detail.get("author") or {}
    cover = video.get("cover") or {}
    thumb = (cover.get("url_list") or cover.get("urlList") or [""])[0]
    return Media(url, "抖音", title, media_id, author.get("nickname") or "", duration / 1000 if duration else None,
                 images[0].url if images else thumb, info, images, qualities_for(info), bool(video_formats(info)), bool(formats))


def extract_douyin(session: requests.Session, url: str, cancel: threading.Event) -> Media | None:
    match = re.search(r"/(?:video|note)/(\d+)", url)
    if not match:
        query = parse_qs(urlparse(url).query)
        media_id = (query.get("modal_id") or query.get("vid") or [""])[0]
    else:
        media_id = match.group(1)
    if not media_id:
        return None
    check_cancel(cancel)
    try:
        response = session.get("https://www.douyin.com/aweme/v1/web/aweme/detail/",
                               params={"aweme_id": media_id}, timeout=(8, 15))
        detail = response.json().get("aweme_detail")
        if detail:
            return parse_douyin_detail(detail, url)
    except (requests.RequestException, ValueError):
        pass
    check_cancel(cancel)
    share_url = f"https://www.iesdouyin.com/share/{'note' if '/note/' in url else 'video'}/{media_id}/"
    try:
        response = session.get(share_url, timeout=(8, 18), headers={"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36"})
        page = response.text
        state = assigned_json(page, r"(?:window\.)?_ROUTER_DATA\s*=\s*")
        if not state:
            match = re.search(r'<script[^>]+id=[\"\']RENDER_DATA[\"\'][^>]*>(.*?)</script>', page, re.S)
            if match:
                state = json.loads(unquote(html.unescape(match.group(1))))
        detail = next((d for d in walk_dicts(state) if str(d.get("aweme_id") or d.get("awemeId")) == media_id
                       and (d.get("video") or d.get("images"))), None)
        if detail:
            return parse_douyin_detail(detail, url)
    except (requests.RequestException, ValueError):
        pass
    return None


def extract_bili_images(session: requests.Session, url: str) -> Media | None:
    match = re.search(r"(?:t\.bilibili\.com/|bilibili\.com/opus/)(\d+)", url)
    if not match:
        return None
    media_id = match.group(1)
    response = session.get("https://api.bilibili.com/x/polymer/web-dynamic/v1/detail", params={"id": media_id}, timeout=(8, 20))
    data = response.json()
    if data.get("code") != 0:
        raise UserError("B站图文未能访问，请检查登录信息和链接。")
    item = data.get("data", {}).get("item", {})
    modules = item.get("modules") or {}
    dynamic = modules.get("module_dynamic") or {}
    major = dynamic.get("major") or {}
    entries = (major.get("draw") or {}).get("items") or (major.get("opus") or {}).get("pics") or []
    images = [a for i in entries if (a := image_asset(i))]
    if not images:
        raise UserError("此动态没有解析到正文图片，请使用图文详情页。")
    title = (major.get("opus") or {}).get("title") or (dynamic.get("desc") or {}).get("text") or "B站图文"
    return Media(url, "B站", title[:120], media_id, (modules.get("module_author") or {}).get("name") or "",
                 thumbnail=images[0].url, images=images, has_video=False, has_audio=False)


def media_from_info(info: dict, url: str) -> Media:
    if info.get("is_live"):
        raise UserError("当前版本支持已发布的视频，暂不支持直播录制。")
    formats = info.get("formats") or ([info] if info.get("url") else [])
    return Media(url, platform_for(url), info.get("title") or "未命名视频", str(info.get("id") or "media"),
                 info.get("uploader") or info.get("channel") or "", info.get("duration"), info.get("thumbnail") or "",
                 info=info, qualities=qualities_for(info), has_video=bool(video_formats(info, False)),
                 has_audio=any(f.get("acodec") != "none" for f in formats))


def parse_media(text: str, login: LoginOptions, cancel: threading.Event | None = None) -> Media:
    cancel = cancel or threading.Event()
    url = extract_url(text)
    check_cancel(cancel)
    try:
        with downloader(login, url) as ydl, session_for(ydl, url) as session:
            host = urlparse(url).hostname or ""
            if any(host == domain or host.endswith("." + domain) for domain in ("xhslink.com", "xhslink.cn", "v.douyin.com", "b23.tv")):
                with session.get(url, stream=True, timeout=(8, 20)) as response:
                    response.raise_for_status()
                    url = response.url
            check_cancel(cancel)
            platform = platform_for(url)
            if platform == "小红书":
                response = session.get(url, timeout=(8, 20))
                response.raise_for_status()
                media = parse_xhs_state(response.text, url)
                if media.has_video or media.images:
                    return media
                # The upstream extractor additionally checks the origin video source.
            if platform == "抖音":
                media = extract_douyin(session, url, cancel)
                if media and (media.has_video or media.images or media.has_audio):
                    return media
                match = re.search(r"/(?:video|note)/(\d+)", url)
                if match:
                    url = f"https://www.douyin.com/video/{match.group(1)}"
            if platform == "B站":
                if media := extract_bili_images(session, url):
                    return media
            info = ydl.extract_info(url, download=False)
            check_cancel(cancel)
            if info and info.get("entries") is not None:
                # A link represents one selected part, never an entire creator feed.
                if (info.get("extractor_key") or "").lower() not in ("bilibili", "bilibili:video"):
                    raise UserError("请粘贴单条视频或图文链接，暂不解析用户主页 / 合集。")
                info = next((entry for entry in info["entries"] if entry), None)
            if not info:
                raise UserError("没有解析到可下载内容。")
            result = media_from_info(info, url)
            if not result.has_video and not result.has_audio:
                raise UserError("链接中没有可用视频或音频。")
            return result
    except DownloadError as exc:
        raise UserError(public_error(exc)) from exc
    except requests.RequestException as exc:
        raise UserError(public_error(exc)) from exc


def thumbnail_bytes(media: Media, login: LoginOptions) -> bytes:
    if not media.thumbnail:
        return b""
    with downloader(login, media.url) as ydl, session_for(ydl, media.url) as session:
        with session.get(media.thumbnail, stream=True, timeout=(5, 10)) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > 10 * 1024 * 1024:
                    return b""
            return bytes(data)
