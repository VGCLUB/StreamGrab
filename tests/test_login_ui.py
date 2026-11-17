import os
import time

import pytest

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


def test_save_settings_also_syncs_a_recent_web_login():
    app = QApplication.instance() or QApplication([])
    service = FakeLogin()
    dialog = SettingsDialog({}, {}, None, service, "抖音")
    dialog.open_web_login()
    wait_for(app, lambda: not dialog.busy)
    dialog.accept()
    wait_for(app, lambda: dialog.result() == QDialog.Accepted)
    assert service.calls == [("open", "抖音"), ("sync", "抖音", True)]
    assert dialog.synced_platform == "抖音"
    dialog.deleteLater()
    app.processEvents()


def test_save_settings_recovers_a_previous_closed_login_window():
    app = QApplication.instance() or QApplication([])
    service = FakeLogin()
    service.has_profile = lambda _: True
    dialog = SettingsDialog({}, {}, None, service, "抖音")
    dialog.accept()
    wait_for(app, lambda: dialog.result() == QDialog.Accepted)
    assert service.calls == [("sync", "抖音", True)]
    dialog.deleteLater()
    app.processEvents()


def test_legacy_douyin_browser_setting_reuses_existing_app_login():
    app = QApplication.instance() or QApplication([])
    service = FakeLogin()
    service.has_profile = lambda platform: platform == "抖音"
    profiles = {"抖音": {"browser": "edge", "cookie_file": ""}}
    dialog = SettingsDialog(profiles, {}, None, service, "抖音")
    assert dialog.method.currentIndex() == 0
    assert dialog.profiles["抖音"]["managed_login"] is True
    assert "managed_login" not in profiles["抖音"]
    assert not service.calls
    dialog.deleteLater()
    app.processEvents()


def test_main_window_migrates_legacy_douyin_before_reading_browser_cookies(monkeypatch, tmp_path):
    from streamgrab import ui
    app = QApplication.instance() or QApplication([])
    service = FakeLogin()
    service.has_profile = lambda platform: platform == "抖音"
    settings = {"profiles": {"抖音": {"browser": "edge", "cookie_file": ""}}, "output_dir": str(tmp_path)}
    saved = {}
    monkeypatch.setattr(ui, "BrowserLogin", lambda: service)
    monkeypatch.setattr(ui, "load_json", lambda name, default: settings if name == "settings.json" else default)
    monkeypatch.setattr(ui, "save_json", lambda name, data: saved.update({name: data}))
    window = ui.MainWindow()
    try:
        login = window.login_for("https://www.douyin.com/video/123456789")
        assert login.browser_login is True
        assert not login.browser and not login.cookie_file and not login.raw_cookie
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
    assert saved["settings.json"]["profiles"]["抖音"]["managed_login"] is True
    assert saved["settings.json"]["profiles"]["抖音"]["browser"] == "edge"


@pytest.mark.parametrize("profile,raw,owned", [
    ({"browser": "edge", "managed_login": False}, "", True),
    ({"browser": "edge", "cookie_file": "fixture-cookies.txt"}, "", True),
    ({"browser": "edge"}, "fixture=synthetic-only", True),
    ({"browser": "edge"}, "", False),
])
def test_explicit_advanced_import_or_missing_profile_is_preserved(profile, raw, owned):
    app = QApplication.instance() or QApplication([])
    service = FakeLogin()
    service.has_profile = lambda _: owned
    dialog = SettingsDialog({"抖音": profile}, {"抖音": raw}, None, service, "抖音")
    assert dialog.method.currentIndex() == 1
    assert dialog.profiles["抖音"] == profile
    dialog.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("platform,url", [
    ("抖音", "https://www.douyin.com/video/123456789"),
    ("小红书", "https://www.xiaohongshu.com/explore/fixture"),
])
def test_bad_saved_snapshot_does_not_block_public_post_parsing(platform, url):
    from types import SimpleNamespace
    from streamgrab.models import UserError
    from streamgrab.ui import MainWindow

    def corrupt_snapshot(_):
        raise UserError("fixture snapshot unavailable")

    state = SimpleNamespace(profiles={platform: {"managed_login": True}}, cookies={},
                            login_service=SimpleNamespace(vault=SimpleNamespace(load=corrupt_snapshot)))
    login = MainWindow.login_for(state, url)
    assert login.browser_login and not login.cookie_data and not login.browser


def test_bad_snapshot_on_other_platform_retains_login_error():
    from types import SimpleNamespace
    from streamgrab.models import UserError
    from streamgrab.ui import MainWindow

    def corrupt_snapshot(_):
        raise UserError("fixture snapshot unavailable")

    state = SimpleNamespace(profiles={"B站": {"managed_login": True}}, cookies={},
                            login_service=SimpleNamespace(vault=SimpleNamespace(load=corrupt_snapshot)))
    with pytest.raises(UserError, match="fixture snapshot unavailable"):
        MainWindow.login_for(state, "https://www.bilibili.com/video/BV1xx411c79H")
