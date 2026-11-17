import base64
import json
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from streamgrab import recording
from streamgrab.browser_login import LoginSite
from streamgrab.download import NO_WINDOW, probe, tool_path
from streamgrab.models import Cancelled, UserError
from streamgrab.recording import RecordingSpec, capture_playback, recording_url, run_recording

URL = 'http://127.0.0.1:1234/video/123'


@pytest.mark.parametrize('url,platform', [
    ('https://v.douyin.com/fixture/', '抖音'),
    ('https://b23.tv/fixture', 'B站'),
    ('https://xhslink.com/fixture', '小红书'),
])
def test_recording_short_links_do_not_depend_on_http_extraction(monkeypatch, url, platform):
    def forbidden(*args, **kwargs):
        raise AssertionError('the browser, not the failed HTTP parser, must resolve this link')
    monkeypatch.setattr(recording.requests, 'get', forbidden)
    assert recording_url(url) == (url, platform)


@pytest.fixture
def browser(monkeypatch):
    state = {'metadata': {'found': True, 'title': 'Fixture', 'duration': 6,
                          'width': 320, 'height': 180, 'url': URL},
             'browser_calls': [], 'page_calls': [], 'starts': [], 'closed': [], 'payloads': None}

    class Service:
        sites = {'fixture': LoginSite('fixture', URL, ('127.0.0.1',))}
        def start(self, platform, **kwargs):
            state['starts'].append((platform, kwargs))
        def _version(self, _):
            return 9222, {'webSocketDebuggerUrl': 'ws://127.0.0.1:9222/devtools/browser/fixture'}
        def close_all(self):
            state['closed'].append('service')

    class Browser:
        def call(self, method, params=None):
            state['browser_calls'].append((method, params))
            return {'targetId': 'fixture'} if method == 'Target.createTarget' else {}
        def close(self):
            state['closed'].append('browser')

    class Page:
        def __init__(self):
            self.events = None
        def call(self, method, params=None):
            state['page_calls'].append((method, params))
            return {}
        def next_event(self, timeout=.5):
            if self.events is None:
                payloads = state['payloads'] or [
                    {'kind': 'data', 'sequence': 0, 'data': base64.b64encode(b'fixture-recording').decode()},
                    {'kind': 'done', 'bytes': len(b'fixture-recording'), 'chunks': 1}]
                self.events = iter(payloads)
            message = next(self.events)
            if callable(message):
                message = message()
            payload = message if isinstance(message, str) else json.dumps({'token': state['options']['token'], **message})
            return {'method': 'Runtime.bindingCalled', 'params': {'name': state['options']['binding'], 'payload': payload}}
        def close(self):
            state['closed'].append('page')

    page = Page()
    monkeypatch.setattr(recording, 'CDP', lambda endpoint: Browser() if '/browser/' in endpoint else page)
    def local_request(self, url, **kwargs):
        assert url == 'http://127.0.0.1:9222/json/list'
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: [{'id': 'fixture', 'webSocketDebuggerUrl': 'ws://127.0.0.1:9222/devtools/page/fixture'}])
    monkeypatch.setattr(recording.requests.Session, 'get', local_request)
    def evaluate(client, script, argument):
        if script == recording.SELECT_PLAYER:
            return state['metadata']
        if script == recording.START_RECORDING:
            state['options'] = argument
        return True
    monkeypatch.setattr(recording, '_evaluate', evaluate)
    state['service'] = Service()
    return state


def capture(browser, tmp_path, *, cancel=None):
    target = tmp_path / 'capture.webm'
    meta = capture_playback(RecordingSpec(URL, tmp_path, start=1, end=4), target,
                            cancel or threading.Event(), lambda *_: None,
                            service=browser['service'], platform='fixture', ready_timeout=.01)
    return target, meta


def test_capture_transfers_only_complete_ordered_chunks_and_cleans_owned_tab(browser, tmp_path):
    path, metadata = capture(browser, tmp_path)
    assert path.read_bytes() == b'fixture-recording'
    assert metadata['selected_duration'] == 3
    assert browser['starts'][0][1]['background'] is True
    assert browser['starts'][0][1]['extra_args'] == ('--mute-audio',)
    assert browser['browser_calls'][0] == ('Target.createTarget', {'url': 'about:blank', 'background': True})
    assert not any(method == 'Target.activateTarget' for method, _ in browser['browser_calls'])
    assert ('Emulation.setFocusEmulationEnabled', {'enabled': True}) in browser['page_calls']
    assert browser['closed'] == ['page', 'browser', 'service']


@pytest.mark.parametrize('payloads', [
    [{'kind': 'data', 'sequence': 1, 'data': 'YQ=='}],
    [{'kind': 'data', 'sequence': 0, 'data': 'YQ=='}, {'kind': 'done', 'bytes': 99, 'chunks': 1}],
    [{'kind': 'done', 'bytes': 0, 'chunks': 0}],
    [{'kind': 'data', 'sequence': 0, 'data': '!invalid!'}],
    ['x' * (2 * 1024 * 1024 + 1)],
])
def test_corrupt_or_incomplete_capture_is_rejected(browser, tmp_path, payloads):
    browser['payloads'] = payloads
    with pytest.raises(UserError):
        capture(browser, tmp_path)
    assert browser['closed'][-1] == 'service'


def test_wrong_capture_nonce_is_ignored(browser, tmp_path):
    browser['payloads'] = [
        {'token': 'unrelated', 'kind': 'error', 'code': 'recording_failed'},
        {'kind': 'data', 'sequence': 0, 'data': 'YQ=='}, {'kind': 'done', 'bytes': 1, 'chunks': 1}]
    path, _ = capture(browser, tmp_path)
    assert path.read_bytes() == b'a'


def test_split_capture_blocks_are_reassembled_without_changing_bytes(browser, tmp_path):
    browser['payloads'] = [
        {'kind': 'data', 'sequence': 0, 'data': base64.b64encode(b'fixture-').decode()},
        {'kind': 'data', 'sequence': 1, 'data': base64.b64encode(b'recording').decode()},
        {'kind': 'done', 'bytes': 17, 'chunks': 2}]
    path, metadata = capture(browser, tmp_path)
    assert path.read_bytes() == b'fixture-recording' and metadata['bytes'] == 17


@pytest.mark.parametrize('change', [
    {'protected': True}, {'duration': None}, {'duration': float('inf')},
    {'url': 'https://unrelated.example/video/123'}, {'url': 'http://127.0.0.1:1234/video/999'},
    {'found': False},
])
def test_unplayable_protected_or_different_work_never_starts_recording(browser, tmp_path, change):
    browser['metadata'].update(change)
    with pytest.raises(UserError):
        capture(browser, tmp_path)
    assert 'options' not in browser
    assert not (tmp_path / 'capture.webm').exists()


def test_browser_capture_permission_error_is_actionable(browser, tmp_path):
    browser['payloads'] = [{'kind': 'error', 'code': 'capture_denied'}]
    with pytest.raises(UserError, match='不允许捕获'):
        capture(browser, tmp_path)


def test_pre_cancelled_capture_never_starts_a_browser(browser, tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        capture(browser, tmp_path, cancel=cancel)
    assert not browser['starts']


def test_cancellation_during_capture_closes_only_owned_resources(browser, tmp_path):
    cancel = threading.Event()
    def interrupt():
        cancel.set()
        return {'kind': 'progress', 'elapsed': 1}
    browser['payloads'] = [interrupt]
    with pytest.raises(Cancelled):
        capture(browser, tmp_path, cancel=cancel)
    assert browser['closed'][-1] == 'service'


@pytest.fixture(scope='module')
def live_webm(tmp_path_factory):
    source = tmp_path_factory.mktemp('recording-source') / 'live.webm'
    subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25',
                    '-f', 'lavfi', '-i', 'sine=frequency=660:sample_rate=48000',
                    '-t', '3', '-c:v', 'libvpx', '-deadline', 'realtime', '-c:a', 'libopus',
                    '-f', 'webm', '-live', '1', str(source)], check=True, creationflags=NO_WINDOW)
    return source


def test_finalize_browser_webm_with_missing_duration_and_preserve_existing_output(monkeypatch, tmp_path, live_webm):
    assert not probe(live_webm)['format'].get('duration')
    original = tmp_path / 'Fixture_网页录制.mp4'
    original.write_bytes(b'keep-existing-result')
    def captured(spec, target, *args, **kwargs):
        shutil.copyfile(live_webm, target)
        return {'title': 'Fixture', 'selected_duration': 3}
    monkeypatch.setattr(recording, 'capture_playback', captured)
    paths = run_recording(RecordingSpec(URL, tmp_path, start=1, end=4), threading.Event(), lambda *_: None)
    assert original.read_bytes() == b'keep-existing-result' and Path(paths[0]) != original
    result = probe(Path(paths[0]))
    assert abs(float(result['format']['duration']) - 3) < .15
    assert {s['codec_name'] for s in result['streams']} == {'h264', 'aac'}
    assert not (tmp_path / '.streamgrab-work').exists()


def test_failed_recording_removes_partial_work_without_removing_existing_files(monkeypatch, tmp_path):
    original = tmp_path / 'existing.mp4'
    original.write_bytes(b'keep')
    def interrupted(spec, target, *args, **kwargs):
        target.write_bytes(b'partial-capture')
        raise Cancelled('fixture interruption')
    monkeypatch.setattr(recording, 'capture_playback', interrupted)
    with pytest.raises(Cancelled):
        run_recording(RecordingSpec(URL, tmp_path, start=1, end=4), threading.Event(), lambda *_: None)
    assert list(tmp_path.iterdir()) == [original]


def test_invalid_recording_range_is_rejected_before_browser_start(browser, tmp_path):
    with pytest.raises(UserError):
        capture_playback(RecordingSpec(URL, tmp_path, start=4, end=1), tmp_path / 'raw.webm',
                         threading.Event(), lambda *_: None, service=browser['service'], platform='fixture')
    assert not browser['starts']
