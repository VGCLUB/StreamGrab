# 拾流 StreamGrab 1.0

Windows 10/11 x64 本地视频、音频与图文下载器。粘贴平台链接或整段分享文案，解析后选择内容、画质、保存位置和起止时间。

**[下载 Windows 免安装版](https://github.com/VGCLUB/StreamGrab/releases/latest)** · [提交问题](https://github.com/VGCLUB/StreamGrab/issues) · [验证记录](qa/验证报告.md)

## 使用

免安装包：完整解压 `StreamGrab-1.0.0-Windows-x64.zip`，双击其中的 `拾流下载器.exe`。请保留旁边的 `_internal` 文件夹。已内置 FFmpeg / FFprobe，无需安装 Python。

当前工作目录也可以双击 `启动拾流.cmd`。下载文件默认保存在用户的 `Downloads/拾流下载`，可以自行更换。

1. 粘贴具体视频 / 图文链接，点击「解析链接」。支持常见短链接、完整分享文案和 B站 BV 号。
2. 选择 MP4 视频、仅音频或图文全部图片。画质来自源站实际返回的媒体，只有原片提供的分辨率才会出现。
3. 如需片段，开启「只下载指定时间段」。支持 `12.5` 秒、`01:30`、`1:02:03`。结束留空代表到结尾。
4. 点击下载；支持队列、取消、历史和打开结果。图片按正文顺序保存，也可同时打包 ZIP。同名输出自动编号。

时间段目前采用**先下载完整所选画质的源文件，再使用 FFmpeg 精确裁剪**，因此长视频需要完整源文件的空间和流量。导出完成或正常取消后清理本次临时文件。精度受原始帧率、音频采样及编码帧长约束，不是任意时间点的零误差。

音频输出：MP3、M4A/AAC、WAV、FLAC。FLAC/WAV 只改变编码容器，不会恢复原始有损音频已经丢失的信息。

## 平台支持及真实验证范围

| 平台 | 视频 / 音频 | 图文正文图片 | 登录与访问条件 |
| --- | --- | --- | --- |
| B站 | yt-dlp 解析，支持实际返回的画质 | 动态 / opus 的图片列表 | 部分画质需要登录或账号权限 |
| 小红书 | 正文视频流；上游解析器补充可用源 | 正文 imageList 全部图片 | 常需保留分享链接 xsec_token，并导入 Cookie |
| 抖音 | 视频播放源与音频 | 图文 images 全部图片 | 常需最新 Cookie，安全验证需在浏览器完成 |
| 其他平台 | yt-dlp 已支持的详情页视频 / 音频 | 未提供通用图文解析 | 取决于解析器、网络和账号权限 |

2026-11-16 验证：B站公开测试视频成功解析，并通过桌面界面下载指定片段；本机 HTTP 测试源完成视频、4 种音频、图片全集 / ZIP、裁剪、取消与重名保护测试。抖音和小红书公开样例在**未导入 Cookie**时要求验证，已确认返回可操作的登录提示；尚未使用用户登录信息做这两个平台的真实下载验收。具体记录见 [平台探测结果](qa/live-platform-results.json) 和 [验证报告](qa/验证报告.md)。

「优先无平台水印」会排除源数据明确标记了水印的版本，优先使用播放源。软件不会伪称能清除已编码进原片的标识，也无法保证未标记的源绝对无水印。不添加新水印、不裁去原画面、不调用收费解析 API。

直播、DRM 保护内容、用户主页批量采集、任务断点恢复和付费权限绕过不在当前版本范围内。平台更新、失效链接、地区限制或安全验证均可能导致解析失败；重新解析会刷新媒体地址。

## 登录设置

在「登录设置」分别配置平台，可选择：

- 导入 Netscape 格式 `cookies.txt`。程序只读取原文件，不改写。
- 粘贴该平台请求头 Cookie，例如 `a=b; c=d`，仅本次运行保留在内存，退出即清除。
- 从 Edge / Chrome / Firefox / Brave 默认配置读取 Cookie。浏览器锁定、加密或系统权限可能使读取失败，此时改用文件导入。

先用浏览器正常访问并登录平台；若出现验证，先自行完成，再更新 Cookie。小红书不要手工删掉链接中的 `xsec_token`。Cookie 按域名匹配，只随对应站点的请求发送，不作为全局请求头转发给任意图片 CDN。

软件没有上传服务和遥测。下载请求直接发送到媒体平台 / CDN。设置与历史保存在 `%LOCALAPPDATA%/StreamGrab`，包含标题、链接、结果路径、Cookie 文件路径和浏览器来源，不写入粘贴的 Cookie。媒体链接可能含平台访问参数，因此历史也应留在本机保管。

## 开发、测试与构建

需要 Python 3.12、FFmpeg / FFprobe。可使用 uv：

```powershell
git clone https://github.com/VGCLUB/StreamGrab.git
cd StreamGrab
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
.venv\Scripts\python.exe run.py
.venv\Scripts\python.exe -m pytest -q --junitxml=qa\test-results.xml
powershell -ExecutionPolicy Bypass -File build.ps1
.venv\Scripts\python.exe qa\package_release.py
```

`qa/prepare_assets.py` 仅复制 PATH 中已有的 FFmpeg / FFprobe，不自行下载可执行文件。`build.ps1` 先测试再构建目录式免安装程序。平台规则变更时，可以在源码环境更新 `yt-dlp`，记录新版本并重新测试、打包；已冻结的 exe 不会自动更新解析器。

源码仓库与源码 ZIP 只包含代码、图标、许可证及经过筛选的验证结果，不包含 Cookie、下载媒体、会话历史或本机原始日志。GitHub Actions 使用本机合成素材运行测试，不依赖个人账号或平台 Cookie。

源码分工：`engine.py` 解析与登录、`download.py` 下载与导出、`models.py` 输入校验、`ui.py` 异步桌面界面。队列顺序执行，每个任务有独立临时目录；取消时停止下载 / FFmpeg，不覆盖已存在的文件。

## 许可证

本项目原创代码使用 GPL-3.0-or-later。第三方组件保持各自许可证，详见 `THIRD_PARTY_NOTICES.md` 和 `licenses/`。发行包附带本应用源码 ZIP。Qt/PySide6 动态库保持独立文件；FFmpeg 为本机已有的 gyan.dev GPLv3 构建，精确版本与对应源码链接保留在 `licenses/ffmpeg/README.txt`。
