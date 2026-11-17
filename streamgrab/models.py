from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


class UserError(Exception):
    pass


class Cancelled(Exception):
    pass


def check_cancel(event: threading.Event):
    if event.is_set():
        raise Cancelled("任务已取消")


def extract_url(text: str) -> str:
    text = text.strip()
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", text):
        return f"https://www.bilibili.com/video/{text}"
    match = re.search(r"https?://[^\s<>\"'，。！？；、【】（）《》]+", text, re.I)
    if not match:
        raise UserError("请粘贴完整的 http / https 链接，也可以直接粘贴平台分享文案。")
    url = match.group().rstrip(").,;!]}〉」』")
    parsed = urlparse(url)
    if not parsed.hostname or parsed.username or parsed.password:
        raise UserError("链接格式不正确。")
    if parsed.scheme not in ("http", "https"):
        raise UserError("只支持 http / https 链接。")
    return url


def platform_for(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for domains, name in [
        (("bilibili.com", "b23.tv"), "B站"),
        (("xiaohongshu.com", "xhslink.com", "xhslink.cn"), "小红书"),
        (("douyin.com", "iesdouyin.com"), "抖音"),
        (("tiktok.com",), "TikTok"),
        (("youtube.com", "youtu.be"), "YouTube"),
    ]:
        if any(host == d or host.endswith("." + d) for d in domains):
            return name
    return "其他平台"


def seconds(value: str | float | int | None) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = str(value).strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?|\d+:\d{1,2}(?:\.\d+)?|\d+:\d{1,2}:\d{1,2}(?:\.\d+)?", text):
        raise UserError("时间请输入秒数或 分:秒 / 时:分:秒，例如 12.5、01:30。")
    parts = [float(p) for p in text.split(":")]
    if len(parts) > 1 and any(p >= 60 for p in parts[1:]):
        raise UserError("分、秒必须小于 60。")
    result = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
    if not math.isfinite(result):
        raise UserError("时间必须为有效数值。")
    return result


def timestamp(value: float | None) -> str:
    if value is None:
        return "未知时长"
    value = max(0, int(value))
    hours, rest = divmod(value, 3600)
    mins, secs = divmod(rest, 60)
    return f"{hours:02}:{mins:02}:{secs:02}" if hours else f"{mins:02}:{secs:02}"


def safe_name(name: str, limit: int = 72) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:limit].rstrip(" .")
    if not name:
        name = "未命名媒体"
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name


@dataclass
class LoginOptions:
    browser: str = ""
    cookie_file: str = ""
    raw_cookie: str = field(default="", repr=False)
    cookie_data: list[dict] = field(default_factory=list, repr=False)
    user_agent: str = ""
    browser_login: bool = False
    remember_login: bool = True


@dataclass
class ImageAsset:
    url: str
    width: int = 0
    height: int = 0
    alternatives: list[str] = field(default_factory=list)


@dataclass
class Quality:
    key: str
    label: str


@dataclass
class Media:
    url: str
    platform: str
    title: str
    media_id: str
    uploader: str = ""
    duration: float | None = None
    thumbnail: str = ""
    info: dict = field(default_factory=dict, repr=False)
    images: list[ImageAsset] = field(default_factory=list)
    qualities: list[Quality] = field(default_factory=list)
    has_video: bool = True
    has_audio: bool = True


@dataclass
class DownloadSpec:
    media: Media
    output_dir: Path
    mode: str = "video"
    quality: str = "best"
    audio_format: str = "mp3"
    start: float | None = None
    end: float | None = None
    avoid_watermark: bool = True
    zip_images: bool = True
    login: LoginOptions = field(default_factory=LoginOptions, repr=False)

    def validate(self, duration: float | None = None):
        if self.mode not in ("video", "audio", "images"):
            raise UserError("下载类型不正确。")
        if self.mode == "images":
            if not self.media.images:
                raise UserError("此链接没有解析到正文图片。视频封面不会作为图文图片下载。")
            if self.start is not None or self.end is not None:
                raise UserError("图文图片不支持时间段。")
            return
        if self.mode == "video" and not self.media.has_video:
            raise UserError("此链接没有视频。请选择全部图片。")
        if self.mode == "audio" and not self.media.has_audio:
            raise UserError("此链接没有可用音轨。")
        if self.audio_format not in ("mp3", "m4a", "wav", "flac"):
            raise UserError("不支持的音频格式。")
        for val in (self.start, self.end):
            if val is not None and (not math.isfinite(val) or val < 0):
                raise UserError("时间必须是大于等于 0 的有效数值。")
        start = self.start or 0
        if self.end is not None and self.end <= start:
            raise UserError("结束时间必须大于开始时间。")
        duration = duration if duration is not None else self.media.duration
        if duration and (start >= duration or (self.end is not None and self.end > duration + .05)):
            raise UserError(f"时间段超出视频长度（{timestamp(duration)}）。")


def is_watermarked(fmt: dict) -> bool:
    # Only use explicit source metadata; do not promise removal of burned-in marks.
    note = str(fmt.get("format_note") or "").lower()
    url = str(fmt.get("url") or "").lower()
    return bool(fmt.get("has_watermark") or re.search(r"(?<!no )\bwatermarked\b", note) or "/playwm/" in url)


def video_formats(info: dict, avoid_watermark: bool = True) -> list[dict]:
    formats = info.get("formats") or ([info] if info.get("url") else [])
    return [f for f in formats if f.get("vcodec") != "none" and not f.get("has_drm")
            and (not avoid_watermark or not is_watermarked(f))]


def qualities_for(info: dict) -> list[Quality]:
    formats = video_formats(info)
    if not formats:
        return []
    # Use the short edge for portrait clips, consistent with 1080 x 1920 = 1080p.
    edges = {min(f.get("width") or f.get("height") or 0, f.get("height") or f.get("width") or 0) for f in formats}
    return [Quality("best", "最佳可用画质")] + [Quality(str(int(e)), f"{int(e)}p") for e in sorted(edges, reverse=True) if e]
