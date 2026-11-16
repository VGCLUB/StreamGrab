# Third-party notices

StreamGrab original source is GPL-3.0-or-later. No third-party component is relicensed by this project.

- PySide6 Essentials / Shiboken6 / Qt 6: LGPLv3 / GPLv3 / commercial options as provided by The Qt Company. Dynamic libraries remain separate. [Qt licensing](https://www.qt.io/licensing/open-source-lgpl-obligations), [Qt source](https://code.qt.io/), [PySide source](https://code.qt.io/cgit/pyside/pyside-setup.git/). Wheel notices and metadata are under `licenses/`.
- yt-dlp: Unlicense, with bundled components retaining their own terms. [Source and licenses](https://github.com/yt-dlp/yt-dlp). The installed version and metadata are retained.
- yt-dlp-ejs: Unlicense. [Source](https://github.com/yt-dlp/ejs). Default yt-dlp dependencies also include PyCryptodomex (BSD / public-domain components), Brotli (MIT), Mutagen (GPL-2.0-or-later) and websockets (BSD-3-Clause); their original notices and metadata are preserved in `licenses/`.
- Requests: Apache-2.0. [Source](https://github.com/psf/requests).
- urllib3: MIT. [Source](https://github.com/urllib3/urllib3).
- idna: BSD-3-Clause. [Source](https://github.com/kjd/idna).
- charset-normalizer: MIT. [Source](https://github.com/jawah/charset_normalizer).
- certifi: MPL-2.0. [Source](https://github.com/certifi/python-certifi).
- PyInstaller: GPL-2.0-or-later with bootloader distribution exception. [License](https://pyinstaller.org/en/stable/license.html).
- FFmpeg / FFprobe: existing gyan.dev static build, version `2025-06-02-git-688f3944ce-full_build-www.gyan.dev`, GPLv3. The original LICENSE and build README are preserved at `licenses/ffmpeg/`. [Corresponding FFmpeg revision](https://github.com/FFmpeg/FFmpeg/commit/688f3944ce), [build provider](https://www.gyan.dev/ffmpeg/builds/). Build configuration lists the linked codecs and libraries in the retained README.
- Python runtime: PSF License Agreement. [Python source](https://www.python.org/downloads/source/). Runtime license files collected by PyInstaller remain in `_internal`.

The source ZIP accompanying the portable build contains the application source, tests, dependency versions and build scripts. It intentionally excludes third-party executables; prepare existing FFmpeg binaries as described in the README when rebuilding.
