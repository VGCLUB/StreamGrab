"""Optional playback recording; captures only the selected browser media element."""
from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from .browser_login import BrowserLogin, CDP, SITES, _validated_ws, domain_matches
from .download import _ffmpeg, export_media, probe, tool_path, unique_path
from .models import Cancelled, DownloadSpec, Media, UserError, check_cancel, extract_url, platform_for, safe_name
from .recording_script import SELECT_PLAYER, START_RECORDING

ERRORS = {
    'no_player': '未找到可播放的视频。请先在独立网页登录窗口确认该作品能正常播放。',
    'protected_media': '此播放器使用受保护媒体，不能使用网页录制。',
    'unsupported': '当前浏览器或播放器不支持网页音画录制。',
    'capture_denied': '播放器不允许捕获音画，无法使用网页录制。',
    'missing_track': '没有取得所需的视频或音轨，播放器可能不允许录制。',
    'seek_failed': '播放器未能跳转到开始时间，请检查该片段能否正常播放。',
    'play_failed': '网页未能开始播放。请先在独立网页登录窗口确认播放或完成验证。',
    'stalled': '网页播放持续缓冲，录制已停止。请检查网络与网页播放。',
    'playback_changed': '网页切换了播放内容或提前停止，未保存不完整录制。',
    'media_error': '网页播放器发生错误，录制已停止。',
    'recording_failed': '网页录制失败，当前播放器可能不支持音画捕获。',
    'transfer_failed': '录制数据传输中断，请重新录制。',
}


@dataclass
class RecordingSpec:
    url: str
    output_dir: Path
    mode: str = 'video'
    audio_format: str = 'mp3'
    start: float | None = None
    end: float | None = None

    def validate(self, duration=None):
        extract_url(self.url)
        if self.mode not in ('video', 'audio'):
            raise UserError('网页录制支持视频或仅音频。')
        DownloadSpec(Media(self.url, platform_for(self.url), '网页录制', 'recording'), self.output_dir,
                     mode=self.mode, audio_format=self.audio_format, start=self.start, end=self.end).validate(duration)


def recording_url(text: str, cancel: threading.Event | None = None) -> tuple[str, str]:
    url = extract_url(text)
    platform = platform_for(url)
    if platform not in SITES:
        raise UserError('网页录制暂支持 B站、抖音、小红书、TikTok 和 YouTube 的视频详情页。')
    check_cancel(cancel or threading.Event())
    host = urlparse(url).hostname or ''
    short_hosts = {'v.douyin.com': '抖音', 'b23.tv': 'B站', 'xhslink.com': '小红书',
                   'xhslink.cn': '小红书', 'youtu.be': 'YouTube'}
    if not domain_matches(host, SITES[platform].domains) and short_hosts.get(host) != platform:
        raise UserError('分享链接不属于所选平台。')
    # The normal browser follows short-link redirects; recording must not depend
    # on the HTTP extraction path that may have failed in the first place.
    return url, platform


def _evaluate(client, function, argument):
    result = client.call('Runtime.evaluate', {'expression': f'({function})({json.dumps(argument, ensure_ascii=True)})',
                                             'returnByValue': True})
    if result.get('exceptionDetails'):
        raise UserError(ERRORS['recording_failed'])
    return result.get('result', {}).get('value')


def _work_id(url):
    match = re.search(r'/(?:video|note|explore|discovery/item)/([\w-]+)', urlparse(url).path)
    return match.group(1) if match else (parse_qs(urlparse(url).query).get('v') or [''])[0]


def capture_playback(spec: RecordingSpec, source: Path, cancel: threading.Event, progress, *,
                     service=None, platform=None, ready_timeout=35) -> dict:
    spec.validate()
    check_cancel(cancel)
    if service is None:
        url, platform = recording_url(spec.url, cancel)
        service = BrowserLogin()
    else:
        url = spec.url
        if platform not in service.sites or not domain_matches(urlparse(url).hostname or '', service.sites[platform].domains):
            raise UserError('录制页面不属于所选浏览器资料。')
    browser = page = None
    target = None
    nonce = uuid.uuid4().hex
    player_key, stop_key, binding = ('sgPlayer_' + nonce, 'sgStop_' + nonce, 'sgCapture_' + nonce)
    try:
        progress(0, '正在打开独立后台播放器…')
        service.start(platform, background=True, initial_url='about:blank', extra_args=('--mute-audio',))
        check_cancel(cancel)
        port, version = service._version(platform)
        browser = CDP(_validated_ws(version['webSocketDebuggerUrl'], port))
        target = browser.call('Target.createTarget', {'url': 'about:blank', 'background': True})['targetId']
        with requests.Session() as local:
            local.trust_env = False
            response = local.get(f'http://127.0.0.1:{port}/json/list', timeout=3)
            response.raise_for_status()
            selected = next(item for item in response.json() if item['id'] == target)
        page = CDP(_validated_ws(selected['webSocketDebuggerUrl'], port))
        page.call('Runtime.enable')
        # Keep this private tab rendering without raising a desktop window.
        page.call('Emulation.setFocusEmulationEnabled', {'enabled': True})
        page.call('Runtime.addBinding', {'name': binding})
        page.call('Page.navigate', {'url': url})
        deadline = time.monotonic() + ready_timeout
        metadata = None
        while time.monotonic() < deadline:
            check_cancel(cancel)
            metadata = _evaluate(page, SELECT_PLAYER, player_key)
            if metadata and metadata.get('found'):
                break
            cancel.wait(.2)
        else:
            raise UserError(ERRORS['no_player'])
        if metadata.get('protected'):
            raise UserError(ERRORS['protected_media'])
        if not domain_matches(urlparse(metadata.get('url', '')).hostname or '', service.sites[platform].domains):
            raise UserError('网页跳转到了其他网站，未开始录制。')
        expected_id = _work_id(url)
        if expected_id and _work_id(metadata.get('url', '')) != expected_id:
            raise UserError('网页跳转到了其他作品，未开始录制。')
        duration = metadata.get('duration')
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise UserError('当前录制支持有确定时长的视频，暂不支持直播或未知时长。')
        spec.validate(duration)
        start, end = spec.start or 0, spec.end if spec.end is not None else duration
        total = end - start
        options = {'playerKey': player_key, 'stopKey': stop_key, 'binding': binding, 'token': nonce,
                   'start': start, 'end': end, 'mode': spec.mode}
        _evaluate(page, START_RECORDING, options)
        deadline = time.monotonic() + total * 1.5 + 45
        written = chunks = 0
        completed = False
        with source.open('xb') as file:
            while time.monotonic() < deadline:
                check_cancel(cancel)
                try:
                    event = page.next_event(timeout=.5)
                except TimeoutError:
                    continue
                if event.get('method') != 'Runtime.bindingCalled':
                    continue
                params = event.get('params', {})
                if params.get('name') != binding:
                    continue
                payload = params.get('payload', '')
                if len(payload) > 2 * 1024 * 1024:
                    raise UserError('单次录制数据过大，录制已停止。')
                message = json.loads(payload)
                if message.get('token') != nonce:
                    continue
                kind = message.get('kind')
                if kind == 'error':
                    raise UserError(ERRORS.get(message.get('code'), ERRORS['recording_failed']))
                if kind == 'data':
                    if message.get('sequence') != chunks:
                        raise UserError(ERRORS['transfer_failed'])
                    block = base64.b64decode(message.get('data', ''), validate=True)
                    written += len(block)
                    if written > 8 * 1024**3:
                        raise UserError('本次录制超过 8 GB，请选择更短的片段。')
                    file.write(block)
                    chunks += 1
                elif kind in ('started', 'progress'):
                    elapsed = min(total, max(0, float(message.get('elapsed', 0))))
                    progress(min(84, elapsed / total * 84), f'正在后台录制 · {elapsed:.1f} / {total:.1f} 秒')
                elif kind == 'done':
                    if not written or message.get('bytes') != written or message.get('chunks') != chunks:
                        raise UserError(ERRORS['transfer_failed'])
                    completed = True
                    break
        if not completed:
            raise UserError('网页录制超时，未保存不完整文件。')
        return {**metadata, 'selected_duration': total, 'bytes': written}
    except (Cancelled, UserError):
        raise
    except Exception as exc:
        raise UserError('网页录制连接中断，请检查播放器后重试。') from exc
    finally:
        if page:
            try:
                _evaluate(page, '(key) => {if (typeof window[key] === "function") window[key](); return true;}', stop_key)
            except Exception:
                pass
            try:
                page.close()
            except Exception:
                pass
        if browser:
            if target:
                try:
                    browser.call('Target.closeTarget', {'targetId': target})
                except Exception:
                    pass
            try:
                browser.close()
            except Exception:
                pass
        service.close_all()


def run_recording(spec: RecordingSpec, cancel: threading.Event, progress, *, service=None, platform=None) -> list[str]:
    spec.validate()
    check_cancel(cancel)
    tool_path('ffmpeg')
    tool_path('ffprobe')
    output = spec.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    work_root = output / '.streamgrab-work'
    work_root.mkdir(exist_ok=True)
    if work_root.resolve().parent != output:
        raise UserError('临时目录不在保存文件夹内，请更换保存位置。')
    identifier = uuid.uuid4().hex
    work = work_root / identifier
    work.mkdir()
    try:
        source = work / 'capture.webm'
        metadata = capture_playback(spec, source, cancel, progress, service=service, platform=platform)
        check_cancel(cancel)
        media = Media(spec.url, platform_for(spec.url), (metadata.get('title') or '网页视频')[:160], 'recording',
                      duration=metadata['selected_duration'])
        # MediaRecorder WebM has no finalized Duration field. A lossless remux
        # writes an index/duration so startup silence can be measured reliably.
        indexed = work / 'indexed.mkv'
        _ffmpeg(['-i', str(source), '-map', '0', '-c', 'copy', str(indexed)],
                metadata['selected_duration'], cancel, lambda *_: None)
        raw = probe(indexed)
        captured_duration = float(raw.get('format', {}).get('duration') or 0)
        if captured_duration < metadata['selected_duration'] - .25:
            raise UserError('未录到完整片段，请确认网页播放流畅后重试。')
        # Chromium can prepend a frozen frame while its playback clock starts.
        # The capture runs from the selected source position through its end;
        # remove only that startup prelude before the final export.
        prelude = max(0, captured_duration - metadata['selected_duration'])
        if prelude > 20:
            raise UserError('网页播放启动延迟过长，请确认播放流畅后重试。')
        export = DownloadSpec(media, output, mode=spec.mode, audio_format=spec.audio_format,
                              start=prelude or None, end=captured_duration)
        extension = 'mp4' if spec.mode == 'video' else spec.audio_format
        staged = work / ('export.' + extension)
        export_media(indexed, staged, export, cancel, progress)
        details = probe(staged)
        actual = float(details.get('format', {}).get('duration') or 0)
        if not actual or abs(actual - metadata['selected_duration']) > max(.6, metadata['selected_duration'] * .08):
            raise UserError('录制时长与所选片段不符，未将不完整录制标记为完成。')
        check_cancel(cancel)
        target = unique_path(output / (safe_name(media.title) + '_网页录制.' + extension))
        os.rename(staged, target)
        progress(100, '录制已保存')
        return [str(target)]
    finally:
        if work.resolve().parent == work_root.resolve() and work.name == identifier:
            shutil.rmtree(work, ignore_errors=True)
        try:
            work_root.rmdir()
        except OSError:
            pass
