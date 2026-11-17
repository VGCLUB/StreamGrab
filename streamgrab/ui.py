from __future__ import annotations

import copy
import os
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy, QTabWidget, QVBoxLayout, QWidget)

from . import __version__
from .browser_login import BrowserLogin, LoginSnapshot, SITES
from .download import run_download, tool_path
from .engine import parse_media, public_error, thumbnail_bytes
from .models import (Cancelled, DownloadSpec, LoginOptions, Media, UserError,
                     extract_url, platform_for, seconds, timestamp)
from .recording import RecordingSpec, run_recording
from .storage import default_downloads, load_json, resource_path, save_json

STYLE = """
* { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #24324a; }
QMainWindow { background: #f4f6fb; }
QWidget#sidebar { background: #ffffff; border-right: 1px solid #e6ebf3; }
QLabel#brand { font-size: 25px; font-weight: 700; color: #223663; }
QLabel#headline { font-size: 26px; font-weight: 700; color: #182b4d; }
QLabel#section { font-size: 16px; font-weight: 650; }
QLabel#subtle { color: #7d899d; font-size: 12px; }
QLabel#small { color: #7d899d; font-size: 11px; }
QLabel#number { font-size: 11px; color: #476bdf; background: #edf2ff; border-radius: 6px; padding: 3px 6px; font-weight: 700; }
QLabel#chip { color: #52729d; background: #f0f4fa; border-radius: 5px; padding: 4px 8px; font-size: 11px; }
QLabel#notice { color: #597399; background: #eef4ff; border: 1px solid #dfE9fa; border-radius: 8px; padding: 11px 14px; }
QLabel#error { color: #b34046; background: #fff1f1; border: 1px solid #f7d9db; border-radius: 7px; padding: 10px; }
QFrame#card { background: white; border: 1px solid #e5eaf3; border-radius: 12px; }
QFrame#task { background: white; border: 1px solid #e5eaf3; border-radius: 10px; }
QPlainTextEdit, QLineEdit, QComboBox { background: #f9fbfe; border: 1px solid #dfe5ef; border-radius: 7px; padding: 9px 11px; selection-background-color: #cad9ff; }
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus { border: 1px solid #6a8af4; background: white; }
QLineEdit:disabled, QComboBox:disabled { color: #a8b0be; background: #f3f5f8; }
QComboBox { min-height: 19px; padding-right: 28px; }
QComboBox::drop-down { border: none; width: 25px; }
QComboBox QAbstractItemView { background: white; border: 1px solid #dfe5ef; selection-background-color: #edf2ff; selection-color: #375dd4; padding: 4px; }
QPushButton { background: white; border: 1px solid #dfe5ef; border-radius: 7px; padding: 9px 15px; font-weight: 500; }
QPushButton:hover { border-color: #9bb2ef; background: #f5f8ff; }
QPushButton:pressed { background: #e8efff; }
QPushButton:disabled { background: #eef1f7; color: #a9b2c3; border-color: #e4e9f1; }
QPushButton#primary, QPushButton#openWebLogin { background: #4166e8; border: 1px solid #4166e8; color: white; font-weight: 650; padding: 11px 23px; }
QPushButton#primary:hover, QPushButton#openWebLogin:hover { background: #3154cf; }
QPushButton#primary:disabled, QPushButton#openWebLogin:disabled { background: #b5c4f5; border-color: #b5c4f5; color: white; }
QPushButton#mode { text-align: center; padding: 13px 12px; background: #f8faff; border-color: #e4e9f2; }
QPushButton#mode:checked { background: #edf2ff; color: #365bd6; border: 1px solid #7996f1; font-weight: 650; }
QPushButton#nav { text-align: left; border: none; color: #78869d; padding: 13px 17px; }
QPushButton#nav:checked { background: #edf2ff; color: #3e63dc; font-weight: 650; }
QPushButton#link { border: none; color: #5473c8; padding: 5px 8px; font-size: 12px; background: transparent; }
QPushButton#danger { color: #bc5760; padding: 5px 10px; font-size: 12px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QProgressBar { border: none; border-radius: 3px; background: #edf1f7; max-height: 5px; min-height: 5px; }
QProgressBar::chunk { background: #6b8bf3; border-radius: 3px; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 0; }
QScrollBar::handle:vertical { background: #d6deed; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QDialog { background: #f7f9fc; }
"""


def label(text, name="", wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def button(text, name="", action=None):
    widget = QPushButton(text)
    widget.setCursor(Qt.PointingHandCursor)
    if name:
        widget.setObjectName(name)
    if action:
        widget.clicked.connect(action)
    return widget


def hbox(*widgets, spacing=10):
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    for widget in widgets:
        if isinstance(widget, int):
            layout.addStretch(widget)
        elif isinstance(widget, QWidget):
            layout.addWidget(widget)
        else:
            layout.addLayout(widget)
    return layout


def card():
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(22, 19, 22, 19)
    layout.setSpacing(13)
    return frame, layout


class WorkerSignals(QObject):
    done = Signal(object)
    error = Signal(object)
    progress = Signal(float, str)


class Worker(QRunnable):
    def __init__(self, func):
        super().__init__()
        self.func = func
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.func(self.signals.progress.emit)
            self.signals.done.emit(result)
        except Exception as exc:
            self.signals.error.emit(exc)


def migrate_legacy_login(profiles, login_service, cookies=None):
    """Prefer an existing app login over an unmigrated daily-browser setting."""
    migrated = copy.deepcopy(profiles)
    douyin = migrated.get("抖音", {})
    if ("managed_login" not in douyin and douyin.get("browser")
            and not douyin.get("cookie_file") and not (cookies or {}).get("抖音")
            and getattr(login_service, "has_profile", lambda _: False)("抖音")):
        douyin["managed_login"] = True
        douyin.setdefault("remember_login", True)
    return migrated


class SettingsDialog(QDialog):
    def __init__(self, profiles, cookies, parent, login_service=None, initial_platform="B站"):
        super().__init__(parent)
        self.setWindowTitle("登录与解析设置")
        self.setMinimumWidth(700)
        self.login_service = login_service or BrowserLogin()
        self.profiles = migrate_legacy_login(profiles, self.login_service, cookies)
        self.cookies = cookies.copy()
        self.current = initial_platform if initial_platform in {*SITES, "其他平台"} else "B站"
        self.pool = getattr(parent, "pool", None) or QThreadPool.globalInstance()
        self.workers = {}
        self.busy = False
        self.synced_platform = ""
        self.opened_platforms = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(14)
        layout.addWidget(label("登录与解析设置", "section"))
        layout.addWidget(label("选择平台，在网页里登录后点「完成同步」即可。无需打开开发者工具或复制 Cookie。", "subtle", True))
        self.platform = QComboBox()
        self.platform.setObjectName("loginPlatform")
        self.platform.addItems(["B站", "小红书", "抖音", "TikTok", "YouTube", "其他平台"])
        self.platform.setCurrentText(self.current)
        layout.addWidget(self.platform)
        self.method = QTabWidget()
        self.method.setObjectName("loginMethod")
        easy = QWidget()
        easy_layout = QVBoxLayout(easy)
        easy_layout.setContentsMargins(16, 20, 16, 18)
        easy_layout.setSpacing(16)
        easy_layout.addWidget(label("① 打开网页登录   →   ② 扫码或登录   →   ③ 完成同步", "section", True))
        easy_layout.addWidget(label("将打开 Edge / Chrome 的独立登录窗口。你在网页中自行完成登录或验证，完成同步后窗口会自动关闭。", "subtle", True))
        self.open_login_btn = button("打开网页登录", "primary", self.open_web_login)
        self.open_login_btn.setObjectName("openWebLogin")
        self.sync_btn = button("我已登录，完成同步", action=self.sync_web_login)
        self.sync_btn.setObjectName("syncWebLogin")
        easy_layout.addLayout(hbox(self.open_login_btn, self.sync_btn))
        self.remember = QCheckBox("在本机加密保存同步状态，下次直接使用")
        self.remember.setChecked(True)
        easy_layout.addWidget(self.remember)
        self.login_status = label("", "notice", True)
        easy_layout.addWidget(self.login_status)
        self.forget_btn = button("清除已同步状态", "link", self.forget_login)
        easy_layout.addLayout(hbox(self.forget_btn, 1))
        self.method.addTab(easy, "网页登录（推荐）")
        advanced = QWidget()
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(16, 17, 16, 18)
        advanced_layout.setSpacing(12)
        advanced_layout.addWidget(label("从日常浏览器读取（可能受浏览器加密限制）", "subtle"))
        self.browser = QComboBox()
        for title, value in [("不读取日常浏览器", ""), ("Microsoft Edge", "edge"), ("Google Chrome", "chrome"), ("Firefox", "firefox"), ("Brave", "brave")]:
            self.browser.addItem(title, value)
        advanced_layout.addWidget(self.browser)
        advanced_layout.addWidget(label("导入 Netscape 格式的 cookies.txt", "subtle"))
        self.file = QLineEdit()
        self.file.setPlaceholderText("可选：选择该平台的 Cookie 文件")
        advanced_layout.addLayout(hbox(self.file, button("选择文件", action=self.choose_file)))
        advanced_layout.addWidget(label("或粘贴 Cookie（仅本次运行使用）", "subtle"))
        self.raw = QLineEdit()
        self.raw.setEchoMode(QLineEdit.Password)
        self.raw.setPlaceholderText("name=value; name2=value2")
        self.show_cookie = QCheckBox("显示")
        self.show_cookie.toggled.connect(lambda show: self.raw.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password))
        advanced_layout.addLayout(hbox(self.raw, self.show_cookie))
        advanced_layout.addWidget(label("此页保留手动导入。日常浏览器无法读取时，切换到「网页登录」即可。", "subtle", True))
        self.method.addTab(advanced, "高级导入")
        layout.addWidget(self.method)
        layout.addWidget(label("网页登录使用本软件的独立浏览器资料，不读取日常浏览器的 Cookie。同步状态只供本机下载使用。", "small", True))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存设置")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.platform.currentTextChanged.connect(self.switch_platform)
        self.load_current()

    def choose_file(self):
        name, _ = QFileDialog.getOpenFileName(self, "导入 cookies.txt", "", "Cookie 文件 (*.txt);;所有文件 (*)")
        if name:
            self.file.setText(name)

    def store_current(self):
        self.profiles[self.current] = {"browser": self.browser.currentData(), "cookie_file": self.file.text().strip(),
                                       "managed_login": self.method.currentIndex() == 0,
                                       "remember_login": self.remember.isChecked()}
        self.cookies[self.current] = self.raw.text().strip()

    def load_current(self):
        data = self.profiles.get(self.current, {})
        self.browser.setCurrentIndex(max(0, self.browser.findData(data.get("browser", ""))))
        self.file.setText(data.get("cookie_file", ""))
        self.raw.setText(self.cookies.get(self.current, ""))
        self.show_cookie.setChecked(False)
        legacy = bool(data.get("browser") or data.get("cookie_file") or self.cookies.get(self.current))
        self.method.setCurrentIndex(0 if data.get("managed_login", not legacy) else 1)
        self.remember.setChecked(data.get("remember_login", True))
        available = self.current in SITES
        self.open_login_btn.setEnabled(available)
        self.sync_btn.setEnabled(available)
        self.forget_btn.setEnabled(available)
        if not available:
            self.login_status.setText("此平台请使用高级导入。")
            self.method.setCurrentIndex(1)
            return
        try:
            snapshot = self.login_service.vault.load(self.current)
            if snapshot:
                saved = datetime.fromtimestamp(snapshot.saved_at).strftime("%m-%d %H:%M")
                self.login_status.setText(f"已同步 · {saved}。可直接解析；失效时重新网页登录并同步。")
            else:
                has_profile = getattr(self.login_service, "has_profile", lambda _: False)(self.current)
                if has_profile:
                    self.login_status.setText("发现独立窗口的浏览器资料。可点击「保存设置」恢复同步；抖音解析也会自动在后台复用。")
                elif self.current == "抖音":
                    self.login_status.setText("抖音会自动在后台读取播放源。若作品要求登录或验证，再点击「打开网页登录」。")
                else:
                    self.login_status.setText("先打开网页登录。在窗口内看到登录成功后，回到这里点击「完成同步」。")
        except UserError as exc:
            self.login_status.setText(str(exc))

    def switch_platform(self, name):
        self.store_current()
        self.current = name
        self.load_current()

    def accept(self):
        if self.busy:
            return
        recoverable = (self.current in self.opened_platforms or
                       (self.current in SITES and getattr(self.login_service, "has_profile", lambda _: False)(self.current)))
        if self.method.currentIndex() == 0 and recoverable and not self.synced_platform:
            self.sync_web_login()
            return
        self.store_current()
        super().accept()

    def _run_login(self, operation, completed):
        if self.busy:
            return
        self.busy = True
        self.open_login_btn.setEnabled(False)
        self.sync_btn.setEnabled(False)
        self.forget_btn.setEnabled(False)
        self.platform.setEnabled(False)
        self.method.setEnabled(False)
        key = uuid.uuid4().hex
        worker = Worker(lambda _: operation())
        self.workers[key] = worker

        def finish():
            self.busy = False
            self.workers.pop(key, None)
            self.platform.setEnabled(True)
            self.method.setEnabled(True)
            self.open_login_btn.setEnabled(True)
            self.sync_btn.setEnabled(True)
            self.forget_btn.setEnabled(True)

        def success(result):
            finish()
            completed(result)

        def failure(exc):
            finish()
            self.login_status.setText(public_error(exc))

        worker.signals.done.connect(success)
        worker.signals.error.connect(failure)
        self.pool.start(worker)

    def open_web_login(self):
        platform = self.current
        self.login_status.setText("正在打开登录窗口…")

        def opened(_):
            self.opened_platforms.add(platform)
            self.login_status.setText("登录窗口已打开。完成登录后点击「完成同步」或「保存设置」都可同步；关闭登录窗口后也能恢复。")

        self._run_login(lambda: self.login_service.start(platform), opened)

    def sync_web_login(self):
        platform, remember = self.current, self.remember.isChecked()
        self.login_status.setText("正在同步登录状态…")

        def synced(_):
            self.method.setCurrentIndex(0)
            self.synced_platform = platform
            self.accept()

        self._run_login(lambda: self.login_service.sync(platform, remember), synced)

    def forget_login(self):
        try:
            self.login_service.vault.forget(self.current)
            self.login_status.setText("已清除软件保存的同步状态。若要退出网站账号，请在登录窗口中操作退出登录。")
        except (UserError, OSError):
            self.login_status.setText("无法清除同步状态，请检查本机目录权限。")


class TaskCard(QFrame):
    cancel_requested = Signal(str)
    retry_requested = Signal(str)

    def __init__(self, data):
        super().__init__()
        self.data = data
        self.setObjectName("task")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(17, 14, 17, 14)
        layout.setSpacing(8)
        title = label(data["title"])
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.status = label("排队中", "chip")
        layout.addLayout(hbox(title, self.status))
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.message = label("", "subtle", True)
        layout.addWidget(self.message)
        self.detail = label(data.get("detail", ""), "small", True)
        self.cancel_btn = button("取消", "danger", lambda: self.cancel_requested.emit(self.data["id"]))
        self.open_btn = button("打开结果", "link", self.open_result)
        self.folder_btn = button("所在文件夹", "link", self.open_folder)
        self.retry_btn = button("重新解析", "link", lambda: self.retry_requested.emit(self.data["id"]))
        layout.addLayout(hbox(self.detail, 1, self.retry_btn, self.open_btn, self.folder_btn, self.cancel_btn))
        self.update_state(data.get("state", "queued"), data.get("progress", 0), data.get("message", "等待开始…"))

    def update_state(self, state, value, message):
        self.data.update(state=state, progress=value, message=message)
        titles = {"queued": "排队中", "running": "下载中", "done": "已完成", "failed": "失败", "cancelled": "已取消", "interrupted": "已中断"}
        self.status.setText(titles.get(state, state))
        self.progress.setValue(int(value))
        self.message.setText(message)
        self.cancel_btn.setVisible(state in ("queued", "running"))
        self.open_btn.setVisible(state == "done")
        self.folder_btn.setVisible(state == "done")
        self.retry_btn.setVisible(state in ("failed", "cancelled", "interrupted"))
        if state == "done":
            self.progress.setStyleSheet("QProgressBar::chunk { background: #48b68b; }")
        elif state in ("failed", "interrupted"):
            self.progress.setStyleSheet("QProgressBar::chunk { background: #d99b70; }")

    def open_result(self):
        paths = self.data.get("paths", [])
        if paths:
            self.open_path(paths[0])

    def open_folder(self):
        paths = self.data.get("paths", [])
        if paths:
            path = Path(paths[0])
            self.open_path(str(path if path.is_dir() else path.parent))

    def open_path(self, path):
        if not Path(path).exists():
            QMessageBox.information(self, "文件不存在", "文件已移动或删除。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class RecordingDialog(QDialog):
    def __init__(self, url, output_dir, *, settings=None, parent=None):
        super().__init__(parent)
        settings = settings or {}
        self.setWindowTitle('网页播放录制 · 实验性备用方式')
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(label('网页播放录制（实验性）', 'section'))
        layout.addWidget(label('解析或下载失败，但网页能正常播放时，可尝试录制播放器的音画。耗时约等于片段时长，使用网页当前播放画质。', 'subtle', True))
        self.link = QLineEdit(url)
        self.link.setReadOnly(True)
        layout.addWidget(self.link)
        self.output = QLineEdit(output_dir)
        self.output.setObjectName('recordOutput')
        layout.addWidget(label('保存位置', 'subtle'))
        layout.addLayout(hbox(self.output, button('选择文件夹', action=self.choose_output)))
        self.kind = QComboBox()
        self.kind.setObjectName('recordMode')
        self.kind.addItem('MP4 视频', 'video')
        self.kind.addItem('仅音频', 'audio')
        self.kind.setCurrentIndex(max(0, self.kind.findData(settings.get('mode', 'video'))))
        self.audio = QComboBox()
        for value in ('mp3', 'm4a', 'wav', 'flac'):
            self.audio.addItem(value.upper(), value)
        self.audio.setCurrentIndex(max(0, self.audio.findData(settings.get('audio_format', 'mp3'))))
        self.audio.setEnabled(self.kind.currentData() == 'audio')
        self.kind.currentIndexChanged.connect(lambda _: self.audio.setEnabled(self.kind.currentData() == 'audio'))
        layout.addLayout(hbox(label('保存内容', 'subtle'), self.kind, self.audio))
        self.start = QLineEdit('' if settings.get('start') is None else str(settings['start']))
        self.end = QLineEdit('' if settings.get('end') is None else str(settings['end']))
        self.start.setObjectName('recordStart')
        self.end.setObjectName('recordEnd')
        self.start.setPlaceholderText('开始时间，默认 0')
        self.end.setPlaceholderText('结束时间，留空到视频结尾')
        layout.addWidget(label('起止时间（秒或 分:秒，结束留空到结尾）', 'subtle'))
        layout.addLayout(hbox(self.start, label('→'), self.end))
        layout.addWidget(label('只录制目标播放器的音画。网页需正常可播放且允许捕获；原片水印会保留，录制时间点受播放器和编码精度影响。', 'notice', True))
        layout.addWidget(label('若网页要求登录或验证，先取消本框，在「登录设置」打开独立窗口完成，再回来录制。录制期间可继续使用其他桌面窗口。', 'small', True))
        self.error = label('', 'error', True)
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('开始后台录制')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, '选择录制保存文件夹', self.output.text())
        if path:
            self.output.setText(path)

    def specification(self):
        if not self.output.text().strip():
            raise UserError('请先选择保存文件夹。')
        spec = RecordingSpec(self.link.text(), Path(self.output.text().strip()), self.kind.currentData(),
                             self.audio.currentData(), seconds(self.start.text()), seconds(self.end.text()))
        spec.validate()
        return spec

    def accept(self):
        try:
            self.specification()
        except UserError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"拾流 · 视频与图文下载器 {__version__}")
        self.resize(1260, 930)
        self.setMinimumSize(1080, 760)
        self.setWindowIcon(QIcon(str(resource_path("assets/app.svg"))))
        self.settings = load_json("settings.json", {})
        self.login_service = BrowserLogin()
        self.profiles = migrate_legacy_login(self.settings.get("profiles", {}), self.login_service)
        self.cookies = {}
        self.media: Media | None = None
        self.media_login: LoginOptions | None = None
        self.parse_cancel = None
        self.parse_generation = 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.workers = {}
        self.queue = deque()
        self.specs = {}
        self.events = {}
        self.tasks = {}
        self.active = None
        self.history_mode = False
        self.build_ui()
        self.restore_history()
        self.range_changed()
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.parse_clicked)

    def build_ui(self):
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(root)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(204)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 30, 20, 23)
        side.setSpacing(10)
        mark = QLabel()
        mark.setPixmap(QIcon(str(resource_path("assets/app.svg"))).pixmap(38, 38))
        side.addLayout(hbox(mark, label("拾流", "brand"), 1, spacing=10))
        side.addWidget(label("STREAMGRAB  /  本地下载", "small"))
        side.addSpacing(36)
        self.nav_work = button("  ↓    下载工作台", "nav", lambda: self.show_history(False))
        self.nav_history = button("  ◷    下载记录", "nav", lambda: self.show_history(True))
        nav = QButtonGroup(self)
        for btn in (self.nav_work, self.nav_history):
            btn.setCheckable(True)
            nav.addButton(btn)
            side.addWidget(btn)
        self.nav_work.setChecked(True)
        side.addStretch()
        side.addWidget(label("在这里，留住喜欢的片段。", "small", True))
        side.addSpacing(14)
        side.addWidget(button("  ⚙    登录设置", "nav", self.open_settings))
        side.addWidget(button("  ?    使用说明", "nav", self.help))
        side.addSpacing(8)
        side.addWidget(label(f"v{__version__}  ·  文件保存在本机", "small"))
        outer.addWidget(sidebar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, 1)
        body = QWidget()
        self.scroll.setWidget(body)
        self.content = QVBoxLayout(body)
        self.content.setContentsMargins(30, 27, 30, 25)
        self.content.setSpacing(17)
        header = QVBoxLayout()
        header.setSpacing(6)
        self.headline = label("下载工作台", "headline")
        self.subtitle = label("一个链接，保存视频、音频与图文。", "subtle")
        header.addWidget(self.headline)
        header.addWidget(self.subtitle)
        self.content.addLayout(hbox(header, 1, button("打开保存位置  ↗", action=self.open_output)))

        self.input_card, inputs = card()
        chips = hbox(label("01", "number"), label("粘贴分享链接", "section"), 1)
        for name, color in [("小红书", "#db4860"), ("B站", "#419fc7"), ("抖音", "#56607c")]:
            chip = label(name, "chip")
            chip.setStyleSheet(f"color: {color}; background: #f5f7fb; padding: 3px 8px;")
            chips.addWidget(chip)
        inputs.addLayout(chips)
        self.url = QPlainTextEdit()
        self.url.setObjectName("urlInput")
        self.url.setFixedHeight(72)
        self.url.setPlaceholderText("粘贴视频 / 图文链接，或包含链接的整段分享文案…")
        inputs.addWidget(self.url)
        self.parse_note = label("支持长链接、短链接与 B站 BV 号", "subtle")
        self.parse_btn = button("解析链接  →", "primary", self.parse_clicked)
        self.parse_btn.setObjectName("primary")
        self.record_btn = button('录制备用', action=self.record_clicked)
        self.record_btn.setObjectName('recordFallback')
        inputs.addLayout(hbox(self.parse_note, 1, button("从剪贴板粘贴", "link", self.paste), self.record_btn, self.parse_btn))
        self.error = label("", "error", True)
        self.error.hide()
        inputs.addWidget(self.error)
        self.content.addWidget(self.input_card)

        self.preview_card, preview = card()
        self.thumb = label("↙")
        self.thumb.setFixedSize(100, 65)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("background: #edf2fb; color: #a5b6d5; border-radius: 7px; font-size: 26px;")
        self.media_title = label("等待解析链接")
        self.media_title.setStyleSheet("font-size: 14px; font-weight: 650;")
        self.media_title.setWordWrap(True)
        self.media_meta = label("解析后将显示真实可用画质、时长与正文图片数量", "subtle", True)
        media_text = QVBoxLayout()
        media_text.setSpacing(7)
        media_text.addWidget(self.media_title)
        media_text.addWidget(self.media_meta)
        preview.addLayout(hbox(self.thumb, media_text, spacing=16))
        self.content.addWidget(self.preview_card)

        self.options_card, options = card()
        options.addLayout(hbox(label("02", "number"), label("选择下载内容", "section"), 1))
        self.modes = QButtonGroup(self)
        modes_row = QHBoxLayout()
        self.mode_buttons = {}
        for key, title in [("video", "▷  视频 · MP4"), ("audio", "♫  仅音频"), ("images", "▧  图文全部图片")]:
            btn = button(title, "mode", lambda checked=False, k=key: self.set_mode(k))
            btn.setCheckable(True)
            btn.setProperty("mode", key)
            self.modes.addButton(btn)
            self.mode_buttons[key] = btn
            modes_row.addWidget(btn, 1)
        self.mode_buttons["video"].setChecked(True)
        options.addLayout(modes_row)
        self.quality = QComboBox()
        self.quality.addItem("解析后选择画质", "best")
        self.quality.setEnabled(False)
        self.audio_format = QComboBox()
        for name, value in [("MP3 · 通用", "mp3"), ("M4A · AAC", "m4a"), ("WAV · 无压缩", "wav"), ("FLAC · 无损编码", "flac")]:
            self.audio_format.addItem(name, value)
        self.audio_format.hide()
        self.quality_label = label("视频画质", "subtle")
        self.watermark = QCheckBox("优先无平台水印的播放源")
        self.watermark.setChecked(True)
        self.zip_images = QCheckBox("全部图片同时打包为 ZIP")
        self.zip_images.setChecked(True)
        self.zip_images.hide()
        options.addLayout(hbox(self.quality_label, self.quality, self.audio_format, self.zip_images, 1, self.watermark))
        self.trim = QCheckBox("只下载指定时间段")
        self.trim.toggled.connect(self.range_changed)
        self.start = QLineEdit()
        self.start.setObjectName("startTime")
        self.start.setPlaceholderText("0 或 00:00")
        self.start.setFixedWidth(142)
        self.end = QLineEdit()
        self.end.setObjectName("endTime")
        self.end.setPlaceholderText("留空到结尾")
        self.end.setFixedWidth(142)
        self.start.textChanged.connect(self.range_changed)
        self.end.textChanged.connect(self.range_changed)
        self.range_row = QWidget()
        range_layout = hbox(self.trim, 1, label("从", "subtle"), self.start, label("到", "subtle"), self.end)
        range_layout.setContentsMargins(0, 0, 0, 0)
        self.range_row.setLayout(range_layout)
        options.addWidget(self.range_row)
        self.range_note = label("支持秒数、分:秒、时:分:秒；片段将精确裁剪后导出。", "small", True)
        options.addWidget(self.range_note)
        self.path = QLineEdit(self.settings.get("output_dir", default_downloads()))
        self.path.setObjectName("outputDir")
        self.path.setPlaceholderText("选择保存文件夹")
        options.addLayout(hbox(label("保存到", "subtle"), self.path, button("选择文件夹", action=self.choose_output)))
        self.export_note = label("自动避开明确标记了平台水印的版本；原片内水印会保留。", "small", True)
        self.export_note.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.download_btn = button("↓  开始下载", "primary", self.enqueue)
        self.download_btn.setEnabled(False)
        options.addLayout(hbox(self.export_note, self.download_btn))
        self.content.addWidget(self.options_card)

        self.queue_title = label("下载队列", "section")
        self.queue_count = label("0 个任务", "subtle")
        self.clear_btn = button("清除已结束记录", "link", self.clear_history)
        self.content.addLayout(hbox(self.queue_title, self.queue_count, 1, self.clear_btn))
        self.task_container = QWidget()
        self.task_layout = QVBoxLayout(self.task_container)
        self.task_layout.setContentsMargins(0, 0, 0, 0)
        self.task_layout.setSpacing(10)
        self.empty = label("还没有下载任务。解析链接后，选择需要的内容即可开始。", "subtle", True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(62)
        self.task_layout.addWidget(self.empty)
        self.content.addWidget(self.task_container)
        self.content.addStretch()

    def mode(self):
        return self.modes.checkedButton().property("mode")

    def set_mode(self, mode):
        self.mode_buttons[mode].setChecked(True)
        self.quality.setVisible(mode == "video")
        self.audio_format.setVisible(mode == "audio")
        self.zip_images.setVisible(mode == "images")
        self.quality_label.setText({"video": "视频画质", "audio": "音频格式", "images": "图文导出"}[mode])
        self.watermark.setVisible(mode != "images")
        self.range_row.setVisible(mode != "images")
        self.range_note.setVisible(mode != "images")
        self.export_note.setText("按正文顺序保存全部图片；ZIP 内保留独立图片。" if mode == "images" else
                                 "自动避开明确标记了平台水印的版本；原片内水印会保留。")
        self.download_btn.setText("↓  下载全部图片" if mode == "images" else "↓  开始下载")
        self.range_changed()

    def range_changed(self):
        enabled = self.trim.isChecked()
        self.start.setEnabled(enabled)
        self.end.setEnabled(enabled)
        if not enabled:
            self.range_note.setText("支持秒数、分:秒、时:分:秒；片段将精确裁剪后导出。")
            return
        try:
            start = seconds(self.start.text()) or 0
            end = seconds(self.end.text())
            if end is not None and end <= start:
                raise UserError("结束时间应大于开始时间。")
            self.range_note.setText(f"选取 {start:g} 秒 → {f'{end:g} 秒' if end is not None else '视频结尾'}。先下载所选画质源，再精确裁剪。")
        except UserError as exc:
            self.range_note.setText(str(exc))

    def paste(self):
        self.url.setPlainText(QApplication.clipboard().text())
        self.url.setFocus()

    def login_for(self, url):
        platform = platform_for(url)
        values = self.profiles.get(platform, {})
        manual = bool(values.get("browser") or values.get("cookie_file") or self.cookies.get(platform))
        if values.get("managed_login", platform == "抖音" and not manual):
            try:
                snapshot = self.login_service.vault.load(platform)
            except (UserError, OSError):
                # Public posts should still be parsed when a saved snapshot is
                # stale or unreadable. The app-owned profile can refresh it.
                if platform not in {"抖音", "小红书"}:
                    raise
                snapshot = None
            if snapshot:
                return LoginOptions(cookie_data=snapshot.cookies, user_agent=snapshot.user_agent, browser_login=True,
                                    remember_login=values.get("remember_login", True))
            return LoginOptions(browser_login=True, remember_login=values.get("remember_login", True))
        return LoginOptions(values.get("browser", ""), values.get("cookie_file", ""), self.cookies.get(platform, ""))

    def show_error(self, message):
        self.error.setText(message)
        self.error.show()

    def parse_clicked(self):
        if self.active and isinstance(self.specs.get(self.active), RecordingSpec):
            self.show_error('正在录制网页，请完成或取消当前录制后再解析。')
            return
        if self.parse_cancel and not self.parse_cancel.is_set():
            self.parse_cancel.set()
            self.parse_btn.setEnabled(False)
            self.parse_note.setText("正在停止解析…")
            return
        try:
            url = extract_url(self.url.toPlainText())
        except UserError as exc:
            self.show_error(str(exc))
            return
        self.error.hide()
        self.media = None
        self.download_btn.setEnabled(False)
        self.media_title.setText("正在解析链接…")
        self.media_meta.setText("正在读取媒体信息与实际可用画质")
        self.thumb.setPixmap(QPixmap())
        self.thumb.setText("···")
        self.parse_generation += 1
        generation = self.parse_generation
        event = self.parse_cancel = threading.Event()
        try:
            login = self.login_for(url)
        except UserError as exc:
            self.parse_cancel = None
            self.parse_btn.setText("解析链接  →")
            self.parse_note.setText("请更新网页登录状态")
            self.show_error(str(exc))
            return
        self.parse_btn.setText("取消解析")
        self.parse_note.setText("连接平台中，请稍候…")
        worker = Worker(lambda _: parse_media(url, login, event))
        key = "parse-" + str(generation)
        self.workers[key] = worker

        def finish():
            self.workers.pop(key, None)
            if generation != self.parse_generation:
                return False
            self.parse_cancel = None
            self.parse_btn.setEnabled(True)
            self.parse_btn.setText("解析链接  →")
            self.parse_note.setText("支持长链接、短链接与 B站 BV 号")
            return True

        def success(media):
            if not finish():
                return
            if event.is_set():
                self.media_title.setText("解析已取消")
                self.media_meta.setText("可以粘贴新链接重新解析")
                return
            self.apply_media(media, login)

        def failure(exc):
            if not finish():
                return
            self.media_title.setText("解析已取消" if event.is_set() else "未能解析此链接")
            self.media_meta.setText("可重新解析；若网页能正常播放，也可尝试「录制备用」")
            if not event.is_set() and not isinstance(exc, Cancelled):
                self.show_error(public_error(exc))

        worker.signals.done.connect(success)
        worker.signals.error.connect(failure)
        self.pool.start(worker)

    def apply_media(self, media, login=None):
        self.media = media
        self.media_login = login
        if login and login.browser_login and login.cookie_data:
            try:
                self.login_service.vault.save(LoginSnapshot(media.platform, login.cookie_data, login.user_agent), login.remember_login)
            except (UserError, OSError):
                self.parse_note.setText("已解析；登录状态本次有效，本机保存未成功")
        self.media_title.setText(media.title)
        parts = [media.platform]
        if media.uploader:
            parts.append(media.uploader)
        if media.duration is not None:
            parts.append(timestamp(media.duration))
        if media.images:
            parts.append(f"{len(media.images)} 张正文图片")
        if media.has_video:
            parts.append(f"{max(0, len(media.qualities) - 1)} 档已知分辨率")
        self.media_meta.setText("  ·  ".join(parts))
        self.thumb.setText("▧" if media.images else "▷")
        self.quality.clear()
        for quality in media.qualities:
            self.quality.addItem(quality.label, quality.key)
        self.quality.setEnabled(bool(media.qualities))
        for key, enabled in [("video", media.has_video), ("audio", media.has_audio), ("images", bool(media.images))]:
            self.mode_buttons[key].setEnabled(enabled)
        self.set_mode("video" if media.has_video else "images" if media.images else "audio")
        self.download_btn.setEnabled(True)
        if media.thumbnail:
            worker = Worker(lambda _: thumbnail_bytes(media, login or self.login_for(media.url)))
            key = "thumb-" + uuid.uuid4().hex
            self.workers[key] = worker

            def loaded(data):
                self.workers.pop(key, None)
                if self.media is not media:
                    return
                pix = QPixmap()
                if data and pix.loadFromData(data):
                    self.thumb.setPixmap(pix.scaled(self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

            worker.signals.done.connect(loaded)
            worker.signals.error.connect(lambda _: self.workers.pop(key, None))
            self.pool.start(worker)

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择保存文件夹", self.path.text())
        if path:
            self.path.setText(path)
            self.save_settings()

    def open_output(self):
        try:
            path = Path(self.path.text().strip() or default_downloads()).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
        except OSError as exc:
            QMessageBox.information(self, "无法打开文件夹", str(exc))

    def open_settings(self):
        if self.active and isinstance(self.specs.get(self.active), RecordingSpec):
            self.show_error('请完成或取消当前录制后再修改网页登录设置。')
            return
        platform = "B站"
        try:
            platform = platform_for(extract_url(self.url.toPlainText()))
        except UserError:
            pass
        dialog = SettingsDialog(self.profiles, self.cookies, self, self.login_service, platform)
        if dialog.exec() == QDialog.Accepted:
            self.profiles, self.cookies = dialog.profiles, dialog.cookies
            self.save_settings()
            if dialog.synced_platform:
                self.error.hide()
                self.parse_note.setText(f"{dialog.synced_platform}状态已同步")
                if self.url.toPlainText().strip() and self.parse_cancel is None:
                    QTimer.singleShot(0, self.parse_clicked)

    def save_settings(self):
        save_json("settings.json", {"output_dir": self.path.text().strip(), "profiles": self.profiles})

    def record_clicked(self, _checked=False, *, settings=None):
        if self.parse_cancel is not None:
            self.show_error('请先等待或取消当前解析，再开始网页录制。')
            return
        try:
            url = extract_url(self.url.toPlainText())
            if platform_for(url) not in SITES:
                raise UserError('网页录制暂支持 B站、抖音、小红书、TikTok 和 YouTube。')
        except UserError as exc:
            self.show_error(str(exc))
            return
        if settings is None:
            settings = {'mode': 'audio' if self.mode() == 'audio' else 'video',
                        'audio_format': self.audio_format.currentData(),
                        'start': self.start.text() if self.trim.isChecked() else None,
                        'end': self.end.text() if self.trim.isChecked() else None}
        dialog = RecordingDialog(url, self.path.text().strip() or default_downloads(), settings=settings, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            spec = dialog.specification()
            tool_path('ffmpeg')
            tool_path('ffprobe')
        except (UserError, OSError) as exc:
            self.show_error(str(exc))
            return
        self.error.hide()
        self.path.setText(str(spec.output_dir))
        key = uuid.uuid4().hex
        mode_name = 'MP4 视频' if spec.mode == 'video' else spec.audio_format.upper() + ' 音频'
        data = {'id': key, 'title': platform_for(url) + '网页录制', 'url': url,
                'detail': f'网页录制 · {mode_name} · {spec.start or 0:g} 秒至 ' + (f'{spec.end:g} 秒' if spec.end is not None else '结尾'),
                'state': 'queued', 'progress': 0, 'created': datetime.now().isoformat(timespec='seconds'), 'paths': [],
                'kind': 'recording', 'recording': {'mode': spec.mode, 'audio_format': spec.audio_format, 'start': spec.start, 'end': spec.end}}
        self.add_task(data)
        self.specs[key] = spec
        self.events[key] = threading.Event()
        self.queue.append(key)
        self.save_settings()
        self.save_history()
        self.start_next()
        self.scroll.ensureWidgetVisible(self.tasks[key])

    def enqueue(self):
        if not self.media:
            return
        self.error.hide()
        try:
            if not self.path.text().strip():
                raise UserError("请先选择保存文件夹。")
            mode = self.mode()
            use_range = self.trim.isChecked() and mode != "images"
            spec = DownloadSpec(self.media, Path(self.path.text().strip()), mode,
                                self.quality.currentData() or "best", self.audio_format.currentData(),
                                seconds(self.start.text()) if use_range else None,
                                seconds(self.end.text()) if use_range else None,
                                self.watermark.isChecked(), self.zip_images.isChecked(), self.media_login or self.login_for(self.media.url))
            spec.validate()
            if mode != "images":
                tool_path("ffmpeg")
                tool_path("ffprobe")
        except (UserError, OSError) as exc:
            self.show_error(str(exc))
            return
        task_id = uuid.uuid4().hex
        mode_name = {"video": "MP4 视频", "audio": spec.audio_format.upper() + " 音频", "images": f"{len(self.media.images)} 张图片"}[mode]
        detail = f"{self.media.platform} · {mode_name}"
        if mode == "video":
            detail += " · " + self.quality.currentText()
        if use_range:
            detail += f" · {spec.start or 0:g} 秒至 {f'{spec.end:g} 秒' if spec.end is not None else '结尾'}"
        data = {"id": task_id, "title": self.media.title, "url": self.media.url, "detail": detail,
                "state": "queued", "progress": 0, "created": datetime.now().isoformat(timespec="seconds"), "paths": []}
        self.add_task(data)
        self.specs[task_id] = spec
        self.events[task_id] = threading.Event()
        self.queue.append(task_id)
        self.save_settings()
        self.save_history()
        self.start_next()
        self.scroll.ensureWidgetVisible(self.tasks[task_id])

    def add_task(self, data):
        task = TaskCard(data)
        task.cancel_requested.connect(self.cancel_task)
        task.retry_requested.connect(self.retry_task)
        self.tasks[data["id"]] = task
        self.task_layout.insertWidget(0, task)
        self.empty.hide()
        self.update_count()

    def start_next(self):
        if self.active:
            return
        while self.queue:
            key = self.queue.popleft()
            if not self.events[key].is_set():
                break
        else:
            return
        self.active = key
        task = self.tasks[key]
        task.update_state("running", 0, "准备下载…")
        event = self.events[key]
        last = [0.0, ""]

        def run(emit):
            def progress(pct, message):
                now = time.monotonic()
                if now - last[0] > .15 or last[1] != message or pct >= 100:
                    last[:] = [now, message]
                    emit(pct, message)
            spec = self.specs[key]
            return run_recording(spec, event, progress) if isinstance(spec, RecordingSpec) else run_download(spec, event, progress)

        worker = Worker(run)
        self.workers[key] = worker
        worker.signals.progress.connect(lambda pct, msg: task.update_state("running", pct, msg))

        def finish(state, message, paths=None):
            if paths:
                task.data["paths"] = paths
            task.update_state(state, 100 if state == "done" else task.progress.value(), message)
            self.workers.pop(key, None)
            self.specs.pop(key, None)
            self.events.pop(key, None)
            self.active = None
            self.save_history()
            self.update_count()
            QTimer.singleShot(0, self.start_next)

        worker.signals.done.connect(lambda paths: finish("done", "已保存至 " + str(Path(paths[0]).parent), paths))
        worker.signals.error.connect(lambda exc: finish("cancelled", "任务已取消，临时文件已清理。") if isinstance(exc, Cancelled) or event.is_set()
                                     else finish("failed", public_error(exc)))
        self.pool.start(worker)

    def cancel_task(self, key):
        event = self.events.get(key)
        if event:
            event.set()
        if key == self.active:
            task = self.tasks[key]
            task.message.setText("正在取消并清理临时文件…")
            task.cancel_btn.setEnabled(False)
        else:
            try:
                self.queue.remove(key)
            except ValueError:
                pass
            self.specs.pop(key, None)
            self.events.pop(key, None)
            self.tasks[key].update_state("cancelled", 0, "任务已取消。")
            self.save_history()
            self.update_count()

    def retry_task(self, key):
        self.show_history(False)
        self.nav_work.setChecked(True)
        self.url.setPlainText(self.tasks[key].data.get("url", ""))
        self.scroll.verticalScrollBar().setValue(0)
        if self.tasks[key].data.get('kind') == 'recording':
            self.record_clicked(settings=self.tasks[key].data.get('recording'))
        else:
            self.parse_clicked()

    def save_history(self):
        save_json("history.json", [task.data for task in self.tasks.values()][-100:])

    def restore_history(self):
        for data in load_json("history.json", [])[-100:]:
            if not isinstance(data, dict) or not all(k in data for k in ("id", "title")):
                continue
            if data.get("state") in ("running", "queued"):
                data.update(state="interrupted", message="上次退出时任务未完成，可以重新解析后下载。")
            self.add_task(data)

    def update_count(self):
        running = sum(t.data["state"] in ("running", "queued") for t in self.tasks.values())
        done = sum(t.data["state"] == "done" for t in self.tasks.values())
        self.queue_count.setText(f"{len(self.tasks)} 个任务 · {running} 个进行中 · {done} 个完成")

    def clear_history(self):
        for key, task in list(self.tasks.items()):
            if task.data["state"] not in ("running", "queued"):
                self.task_layout.removeWidget(task)
                task.deleteLater()
                del self.tasks[key]
        self.empty.setVisible(not self.tasks)
        self.save_history()
        self.update_count()

    def show_history(self, enabled):
        self.history_mode = enabled
        for widget in (self.input_card, self.preview_card, self.options_card):
            widget.setVisible(not enabled)
        self.headline.setText("下载记录" if enabled else "下载工作台")
        self.subtitle.setText("查看任务状态，快速打开已保存的文件。" if enabled else "一个链接，保存视频、音频与图文。")
        self.queue_title.setText("全部任务" if enabled else "下载队列")
        self.scroll.verticalScrollBar().setValue(0)

    def help(self):
        QMessageBox.information(self, "拾流 · 使用说明",
            "1. 粘贴平台分享链接或整段分享文案，点击解析。\n\n"
            "2. 选择视频、仅音频或图文全部图片。画质列表来自平台实际返回的源，登录后可能获得更多画质。\n\n"
            "3. 开启时间段后，输入秒数（如 12.5）或 分:秒（如 01:30）；结束留空代表到结尾。当前采用先下载源文件、再精确裁剪的方式，长视频需预留完整源文件空间。\n\n"
            "录制备用：网页能正常播放且播放器允许捕获时，可跳过解析，在链接旁点击「录制备用」。支持后台 MP4 / 仅音频及起止时间；耗时约等于片段时长，使用当前播放画质。此模式不录整个桌面或麦克风，受保护或禁止捕获的播放器不支持。\n\n"
            "4. 选择保存位置后开始下载。支持排队、取消和查看历史；重新解析可刷新过期地址。\n\n"
            "无平台水印取决于平台提供的源；软件不重绘原片中已有的标识。图文按正文列表保存，不把头像或推荐封面算入。\n\n"
            "登录更方便：左下角「登录设置 → 网页登录」，打开登录窗口，在网页中扫码或登录，再点击「我已登录，完成同步」。状态在本机加密保存，回到工作台会自动重新解析已有链接。小红书请保留分享链接中的 xsec_token。\n\n"
            "当前不支持直播、批量主页下载、断点任务恢复或受 DRM 保护内容。图片与音视频只保存到你选择的本机目录。")

    def closeEvent(self, event):
        active = self.active is not None or bool(self.queue) or self.parse_cancel is not None
        if active:
            answer = QMessageBox.question(self, "退出拾流", "仍有任务在进行中。退出会取消未完成任务，确定退出？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        if self.parse_cancel:
            self.parse_cancel.set()
        for cancel in self.events.values():
            cancel.set()
        self.queue.clear()
        self.save_settings()
        self.save_history()
        event.accept()


def launch():
    app = QApplication(sys.argv)
    app.setApplicationName("拾流下载器")
    app.setOrganizationName("StreamGrab")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow()
    app.aboutToQuit.connect(window.login_service.close_all)
    window.show()
    return app.exec()
