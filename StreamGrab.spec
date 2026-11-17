# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata
from streamgrab import __version__

root = Path(SPECPATH)
datas = [(str(root / 'assets'), 'assets'), (str(root / 'licenses'), 'licenses')]
datas += copy_metadata('yt-dlp') + copy_metadata('requests')
a = Analysis([str(root / 'run.py')], pathex=[str(root)], binaries=[], datas=datas,
             hiddenimports=collect_submodules('yt_dlp'),
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets',
                       'PySide6.QtNetwork', 'PySide6.QtOpenGL', 'PySide6.QtPrintSupport',
                       'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtXml', 'tkinter', 'pytest'],
             noarchive=False)
# The pinned Qt 6.12 wheel uses Windows' system ICU with unversioned exports.
# The host PATH contains Poppler's incompatible ICU (version-suffixed exports).
# No component of this app needs that private ICU; let Windows load its own.
a.binaries = [b for b in a.binaries
              if not Path(b[0]).name.lower().startswith(('icuuc', 'icudt', 'icuin'))]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='拾流下载器',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon=str(root / 'assets' / 'app.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=f'StreamGrab-{__version__}')
