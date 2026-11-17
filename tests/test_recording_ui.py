import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QDialog

from streamgrab import ui
from streamgrab.recording import RecordingSpec

URL = 'https://www.douyin.com/video/123456789'


def test_recording_dialog_supports_audio_and_time_formats(tmp_path):
    app = QApplication.instance() or QApplication([])
    dialog = ui.RecordingDialog(URL, str(tmp_path), settings={'mode': 'audio', 'audio_format': 'flac', 'start': '1:02', 'end': '1:06'})
    spec = dialog.specification()
    assert spec.mode == 'audio' and spec.audio_format == 'flac'
    assert spec.start == 62 and spec.end == 66 and spec.output_dir == tmp_path
    dialog.deleteLater()
    app.processEvents()


def test_recording_dialog_rejects_invalid_range(tmp_path):
    app = QApplication.instance() or QApplication([])
    dialog = ui.RecordingDialog(URL, str(tmp_path), settings={'start': 5, 'end': 2})
    dialog.accept()
    assert dialog.result() != QDialog.Accepted and dialog.error.text()
    dialog.deleteLater()
    app.processEvents()


def make_window(monkeypatch, tmp_path):
    monkeypatch.setattr(ui, 'load_json', lambda name, default: {'output_dir': str(tmp_path)} if name == 'settings.json' else default)
    monkeypatch.setattr(ui, 'save_json', lambda *_: None)
    return ui.MainWindow()


def test_recording_can_be_queued_and_completed_without_parsing(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    window = make_window(monkeypatch, tmp_path)
    calls = []
    class Accepted:
        def __init__(self, *args, **kwargs):
            pass
        def exec(self):
            return QDialog.Accepted
        def specification(self):
            return RecordingSpec(URL, tmp_path, start=1, end=4)
    def record(spec, cancel, progress):
        calls.append(spec)
        result = tmp_path / 'fixture.mp4'
        result.write_bytes(b'fixture-result')
        return [str(result)]
    def forbidden(*args, **kwargs):
        raise AssertionError('fallback recording must not require the parser')
    monkeypatch.setattr(ui, 'RecordingDialog', Accepted)
    monkeypatch.setattr(ui, 'run_recording', record)
    monkeypatch.setattr(ui, 'parse_media', forbidden)
    window.url.setPlainText(URL)
    window.record_clicked()
    deadline = time.monotonic() + 5
    while window.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert window.media is None and len(calls) == 1
    task = next(iter(window.tasks.values())).data
    assert task['state'] == 'done' and task['kind'] == 'recording'
    assert task['recording']['start'] == 1 and task['paths'] == [str(tmp_path / 'fixture.mp4')]
    window.close()
    window.deleteLater()
    app.processEvents()


def test_active_recording_blocks_concurrent_parse_and_login_changes(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    window = make_window(monkeypatch, tmp_path)
    window.active = 'fixture'
    window.specs['fixture'] = RecordingSpec(URL, tmp_path)
    window.parse_clicked()
    assert window.parse_cancel is None and window.error.text()
    window.error.clear()
    window.open_settings()
    assert window.error.text()
    window.active = None
    window.specs.clear()
    window.close()
    window.deleteLater()
    app.processEvents()
