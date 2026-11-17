"""Real browser playback recording against synthetic localhost media only."""
import array
import functools
import http.server
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamgrab.browser_login import BrowserLogin, LoginSite
from streamgrab.download import NO_WINDOW, probe, tool_path
from streamgrab.models import Cancelled, UserError
from streamgrab.recording import RecordingSpec, run_recording


def _frequency(path, start, length=.2):
    result = subprocess.run([tool_path('ffmpeg'), '-v', 'error', '-ss', str(start), '-i', str(path),
                             '-t', str(length), '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'],
                            capture_output=True, check=True, creationflags=NO_WINDOW)
    samples = array.array('h', result.stdout)
    return sum(a <= 0 < b for a, b in zip(samples, samples[1:])) / (len(samples) / 16000)


def run_check():
    with tempfile.TemporaryDirectory(prefix='streamgrab-recording-fixture-', ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        origin, output = root / 'origin', root / 'output'
        origin.mkdir()
        output.mkdir()
        subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y',
                        '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25',
                        '-f', 'lavfi', '-i', 'aevalsrc=0.1*sin(2*PI*(440*t+50*t*t)):s=48000:d=6',
                        '-t', '6', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', str(origin / 'fixture.mp4')],
                       check=True, creationflags=NO_WINDOW)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(origin)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        html = '<!doctype html><title>StreamGrab recording fixture</title><video controls preload="auto" width="640" height="360" src="{}"></video>'
        (origin / 'index.html').write_text(html.format('/fixture.mp4'), encoding='utf-8')
        (origin / 'cross.html').write_text(html.format(f'http://localhost:{server.server_port}/fixture.mp4'), encoding='utf-8')
        (origin / 'protected.html').write_text(html.format('/fixture.mp4') +
            '<script>Object.defineProperty(document.querySelector("video"),"mediaKeys",{value:{fixture:true}})</script>', encoding='utf-8')

        def record(name, spec, cancel=None, progress=None):
            service = BrowserLogin(root / ('browser-' + name), sites={
                'fixture': LoginSite('fixture', base + '/', ('127.0.0.1',))})
            try:
                return run_recording(spec, cancel or threading.Event(), progress or (lambda *_: None),
                                     service=service, platform='fixture')
            finally:
                # Chromium's cache/profile flush can briefly outlive Browser.close.
                time.sleep(1)

        try:
            paths = record('video', RecordingSpec(base + '/index.html', output, start=1, end=4))
            video = probe(Path(paths[0]))
            video_duration = float(video['format']['duration'])
            assert abs(video_duration - 3) < .15
            assert {s['codec_type'] for s in video['streams']} == {'video', 'audio'}
            assert next(s for s in video['streams'] if s['codec_type'] == 'video')['width'] == 320
            frequencies = [_frequency(paths[0], start) for start in (.1, 2.7)]
            assert abs(frequencies[0] - 560) < 30 and abs(frequencies[1] - 820) < 30, frequencies
            volume = subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-i', paths[0], '-vn', '-af', 'volumedetect', '-f', 'null', '-'],
                                    capture_output=True, creationflags=NO_WINDOW, check=True)
            match = re.search(r'mean_volume:\s*(-?[\d.]+) dB', volume.stderr.decode('utf-8', 'replace'))
            assert match and float(match.group(1)) > -40

            audio_paths = record('audio', RecordingSpec(base + '/index.html', output, mode='audio', start=1, end=3))
            audio = probe(Path(audio_paths[0]))
            assert abs(float(audio['format']['duration']) - 2) < .15
            assert [s['codec_name'] for s in audio['streams']] == ['mp3']

            before = set(output.iterdir())
            for name in ('cross', 'protected'):
                try:
                    record(name, RecordingSpec(base + '/' + name + '.html', output, start=1, end=3))
                except UserError:
                    pass
                else:
                    raise AssertionError(name + ' fixture was not rejected')
                assert set(output.iterdir()) == before

            cancelled = threading.Event()
            def cancel_recording(percent, _):
                if percent > 10:
                    cancelled.set()
            try:
                record('cancel', RecordingSpec(base + '/index.html', output, start=1, end=5), cancelled, cancel_recording)
            except Cancelled:
                pass
            else:
                raise AssertionError('cancelled recording succeeded')
            assert set(output.iterdir()) == before
            assert not (output / '.streamgrab-work').exists()
            return {'status': 'passed', 'browser_mode': 'headless', 'source': 'synthetic localhost video and chirp audio',
                    'selection_seconds': [1, 4], 'video_duration': video_duration, 'video_resolution': [320, 180],
                    'mean_audio_db': float(match.group(1)), 'chirp_frequency_hz': frequencies,
                    'audio_selection_seconds': [1, 3], 'mp3_duration': float(audio['format']['duration']),
                    'cross_origin_fixture': 'rejected', 'protected_marker_fixture': 'rejected',
                    'cancellation_cleanup': 'passed', 'desktop_or_microphone_capture': 'not used',
                    'real_platform': 'not tested'}
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    print(json.dumps(run_check(), ensure_ascii=True, indent=2))
