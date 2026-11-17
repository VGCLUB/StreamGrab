import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QDialog

from streamgrab.browser_login import LoginSnapshot
from streamgrab.ui import SettingsDialog


class FakeVault:
    def load(self, platform):
        return None

    def forget(self, platform):
        pass


class FakeLogin:
    def __init__(self):
        self.vault = FakeVault()
        self.calls = []

    def start(self, platform):
        self.calls.append(("open", platform))
        return "Fixture/1"

    def sync(self, platform, remember):
        self.calls.append(("sync", platform, remember))
        return LoginSnapshot(platform, [])


def wait_for(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


def test_easy_login_sync_finishes_without_another_save_click():
    app = QApplication.instance() or QApplication([])
    service = FakeLogin()
    dialog = SettingsDialog({}, {}, None, service, "抖音")
    assert dialog.method.currentIndex() == 0 and dialog.current == "抖音"
    dialog.open_web_login()
    wait_for(app, lambda: not dialog.busy)
    assert "已打开" in dialog.login_status.text()
    dialog.sync_web_login()
    wait_for(app, lambda: dialog.result() == QDialog.Accepted)
    assert service.calls == [("open", "抖音"), ("sync", "抖音", True)]
    assert dialog.profiles["抖音"]["managed_login"] is True
    assert dialog.synced_platform == "抖音"
    dialog.deleteLater()
    app.processEvents()


def test_legacy_import_remains_selected_and_platform_specific():
    app = QApplication.instance() or QApplication([])
    profiles = {"B站": {"browser": "edge", "cookie_file": "fixture-cookies.txt"}}
    dialog = SettingsDialog(profiles, {}, None, FakeLogin(), "B站")
    assert dialog.method.currentIndex() == 1
    dialog.platform.setCurrentText("小红书")
    assert dialog.method.currentIndex() == 0
    dialog.accept()
    assert dialog.profiles["B站"]["managed_login"] is False
    assert dialog.profiles["B站"]["cookie_file"] == "fixture-cookies.txt"
    assert dialog.profiles["小红书"]["managed_login"] is True
    dialog.deleteLater()
    app.processEvents()
