from __future__ import annotations

import copy
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Callable

import requests
from yt_dlp.utils import DownloadError

from .engine import downloader, public_error, session_for
from .models import (Cancelled, DownloadSpec, UserError, check_cancel,
                     is_watermarked, safe_name, video_formats)
from .storage import resource_path

Progress = Callable[[float, str], None]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def tool_path(name: str) -> str:
    bundled = resource_path(f"assets/ffmpeg/{name}.exe")
    path = str(bundled) if bundled.exists() else shutil.which(name)
    if not path:
        raise UserError(f"未找到 {name}。请使用完整发行包，或将 FFmpeg / FFprobe 加入 PATH。")
    return path


def probe(path: Path) -> dict:
    result = subprocess.run([tool_path("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                            capture_output=True, creationflags=NO_WINDOW, timeout=45)
    if result.returncode:
        raise UserError("无法读取下载媒体，源文件可能已失效。请重新解析链接。")
    return json.loads(result.stdout)


def choose_format(spec: DownloadSpec) -> tuple[dict, str]:
    info = copy.deepcopy(spec.media.info)
    formats = info.get("formats") or ([info.copy()] if info.get("url") else [])
    formats = [f for f in formats if not f.get("has_drm") and (not spec.avoid_watermark or not is_watermarked(f))]
    if spec.mode == "video":
        if spec.quality != "best":
            try:
                edge = int(spec.quality)
            except ValueError as exc:
                raise UserError("画质选择不正确，请重新解析。") from exc
            formats = [f for f in formats if f.get("vcodec") == "none" or
                       min(f.get("width") or f.get("height") or 0, f.get("height") or f.get("width") or 0) == edge]
        if not any(f.get("vcodec") != "none" for f in formats):
            raise UserError("所选画质没有可用源，或只返回了明确带平台水印的版本。请重新解析或调整水印选项。")
        selector = "bv*+ba/b"
    else:
        if not any(f.get("acodec") != "none" for f in formats):
            raise UserError("没有可用音轨。")
        selector = "ba/b"
    # Some direct generic results have no formats list. Convert them to a normal one.
    info.pop("url", None)
    info.pop("format_id", None)
    info.pop("requested_downloads", None)
    info.pop("requested_formats", None)
    info["formats"] = formats
    return info, selector


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 100_000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise UserError("同名文件过多，请更换保存位置。")


def _ffmpeg(args: list[str], duration: float, cancel: threading.Event, progress: Progress):
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([tool_path("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                                    *args, "-progress", "pipe:1", "-nostats"],
                                   stdout=subprocess.PIPE, stderr=errors, creationflags=NO_WINDOW)
        lines = queue.Queue()

        def read():
            try:
                for line in process.stdout:
                    lines.put(line.decode("utf-8", "replace").strip())
            finally:
                process.stdout.close()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            while process.poll() is None or not lines.empty():
                check_cancel(cancel)
                try:
                    line = lines.get(timeout=.1)
                except queue.Empty:
                    continue
                if line.startswith("out_time_us="):
                    try:
                        done = int(line.split("=", 1)[1]) / 1_000_000
                        progress(min(99, 86 + (done / duration * 13 if duration else 0)), "正在精确裁剪 / 导出…")
                    except ValueError:
                        pass
            if process.wait() != 0:
                errors.seek(0)
                message = errors.read().decode("utf-8", "replace")
                raise UserError("媒体导出失败：" + message[-600:])
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            reader.join(timeout=2)


def export_media(source: Path, destination: Path, spec: DownloadSpec, cancel: threading.Event, progress: Progress):
    details = probe(source)
    duration = float(details.get("format", {}).get("duration") or spec.media.duration or 0)
    spec.validate(duration or None)
    trimmed = bool(spec.start or spec.end is not None)
    args = []
    if spec.start:
        args += ["-ss", str(spec.start)]
    args += ["-i", str(source)]
    target_duration = max(0, (spec.end if spec.end is not None else duration) - (spec.start or 0))
    if spec.end is not None:
        args += ["-t", str(target_duration)]
    if spec.mode == "audio":
        if not any(s.get("codec_type") == "audio" for s in details.get("streams", [])):
            raise UserError("此媒体没有音轨，无法提取音频。")
        args += ["-map", "0:a:0", "-vn"]
        encoders = {"mp3": ["-c:a", "libmp3lame", "-q:a", "2"], "m4a": ["-c:a", "aac", "-b:a", "192k"],
                    "wav": ["-c:a", "pcm_s16le"], "flac": ["-c:a", "flac"]}
        args += encoders[spec.audio_format]
    else:
        args += ["-map", "0:v:0", "-map", "0:a:0?"]
        codecs = {s.get("codec_type"): s.get("codec_name") for s in details.get("streams", [])}
        if trimmed or codecs.get("video") not in ("h264", "hevc", "av1", "mpeg4"):
            args += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p"]
        else:
            args += ["-c:v", "copy"]
        args += ["-c:a", "copy"] if not trimmed and codecs.get("audio") in (None, "aac", "mp3") else ["-c:a", "aac", "-b:a", "192k"]
        args += ["-movflags", "+faststart"]
    args += ["-map_metadata", "-1", str(destination)]
    _ffmpeg(args, target_duration or duration, cancel, progress)
    check_cancel(cancel)
    if not destination.exists() or not destination.stat().st_size:
        raise UserError("没有生成有效的输出文件。")


def sniff_image(data: bytes, content_type: str) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis", b"heic", b"heix", b"mif1"):
        return ".avif" if data[8:12] in (b"avif", b"avis") else ".heic"
    if data.startswith(b"BM"):
        return ".bmp"
    raise UserError("图片地址返回了无效内容，可能已过期。请重新解析图文链接。")


def download_images(spec: DownloadSpec, work: Path, cancel: threading.Event, progress: Progress) -> Path:
    images_dir = work / "images"
    images_dir.mkdir()
    with downloader(spec.login, spec.media.url) as ydl, session_for(ydl, spec.media.url) as session:
        for index, image in enumerate(spec.media.images):
            check_cancel(cancel)
            success = False
            for url in [image.url, *image.alternatives]:
                if success:
                    break
                for attempt in range(2):
                    check_cancel(cancel)
                    partial = images_dir / f"{index + 1:03}.part"
                    try:
                        with session.get(url, stream=True, timeout=(10, 20)) as response:
                            response.raise_for_status()
                            total = int(response.headers.get("Content-Length") or 0)
                            written, first = 0, b""
                            with partial.open("wb") as file:
                                for chunk in response.iter_content(128 * 1024):
                                    check_cancel(cancel)
                                    if not first:
                                        first = chunk[:32]
                                    file.write(chunk)
                                    written += len(chunk)
                                    fraction = min(.99, written / total) if total else .5
                                    progress((index + fraction) / len(spec.media.images) * 96, f"正在下载图片 {index + 1} / {len(spec.media.images)}")
                            ext = sniff_image(first, response.headers.get("Content-Type", ""))
                            partial.rename(images_dir / f"{index + 1:03}{ext}")
                            success = True
                            break
                    except (requests.RequestException, UserError):
                        partial.unlink(missing_ok=True)
                        if attempt == 0:
                            cancel.wait(.4)
            if not success:
                raise UserError(f"第 {index + 1} / {len(spec.media.images)} 张图片下载失败。未将不完整图集标记为完成，请更新登录信息并重新解析。")
    return images_dir


def run_download(spec: DownloadSpec, cancel: threading.Event, progress: Progress) -> list[str]:
    spec.validate()
    check_cancel(cancel)
    output = spec.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    work_root = output / ".streamgrab-work"
    work_root.mkdir(exist_ok=True)
    work = work_root / uuid.uuid4().hex
    work.mkdir()
    base = safe_name(spec.media.title) + "_" + safe_name(spec.media.media_id, 26)
    try:
        if spec.mode == "images":
            folder = download_images(spec, work, cancel, progress)
            target = unique_path(output / (base + "_图片"))
            archive = None
            if spec.zip_images:
                progress(97, "正在打包全部图片…")
                archive = work / "images.zip"
                with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
                    for file in sorted(folder.iterdir()):
                        check_cancel(cancel)
                        zf.write(file, f"{target.name}/{file.name}")
            check_cancel(cancel)
            shutil.move(str(folder), str(target))
            paths = [str(target)]
            if archive:
                zip_target = unique_path(output / (target.name + ".zip"))
                shutil.move(str(archive), str(zip_target))
                paths.append(str(zip_target))
        else:
            # Check tools before transferring a potentially large video.
            tool_path("ffmpeg")
            tool_path("ffprobe")
            info, selector = choose_format(spec)

            def hook(status):
                check_cancel(cancel)
                if status.get("status") == "downloading":
                    total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
                    done = status.get("downloaded_bytes") or 0
                    pct = min(84, done / total * 84) if total else 0
                    speed = status.get("speed") or 0
                    progress(pct, "正在下载源文件" + (f" · {speed / 1024 / 1024:.1f} MB/s" if speed else "…"))
                elif status.get("status") == "finished":
                    progress(85, "源轨道下载完成，正在合并 / 导出…")

            with downloader(spec.login, spec.media.url, format=selector, outtmpl=str(work / "source.%(ext)s"),
                            merge_output_format="mkv", progress_hooks=[hook], postprocessor_hooks=[lambda _: check_cancel(cancel)],
                            concurrent_fragment_downloads=1, continuedl=True) as ydl:
                # Reprocess to sort custom extractor formats and choose matching tracks.
                ydl.process_ie_result(info, download=True)
            check_cancel(cancel)
            candidates = [f for f in work.iterdir() if f.is_file() and f.name.startswith("source.")
                          and f.suffix.lower() not in (".part", ".ytdl", ".json", ".temp")]
            if len(candidates) != 1:
                raise UserError("媒体合并没有完成，请重新解析后重试。")
            ext = ".mp4" if spec.mode == "video" else "." + spec.audio_format
            if spec.start or spec.end is not None:
                base += f"_{spec.start or 0:g}-{spec.end if spec.end is not None else 'end'}s"
            exported = work / ("export" + ext)
            progress(86, "正在精确裁剪 / 导出…")
            export_media(candidates[0], exported, spec, cancel, progress)
            target = unique_path(output / (base + ext))
            shutil.move(str(exported), str(target))
            paths = [str(target)]
        progress(100, "下载完成")
        return paths
    except DownloadError as exc:
        check_cancel(cancel)
        raise UserError(public_error(exc)) from exc
    finally:
        # Only remove this task's UUID staging directory beneath the selected output.
        if work.resolve().parent == work_root.resolve() and work.name and len(work.name) == 32:
            shutil.rmtree(work, ignore_errors=True)
        try:
            work_root.rmdir()
        except OSError:
            pass
