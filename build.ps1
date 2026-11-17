param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Please create .venv and install requirements-dev.txt first.' }
Push-Location -LiteralPath $taskRoot
try {
    if (-not $SkipTests) {
        & $taskPython -m pytest -q --junitxml=qa/test-results.xml
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    }
    & $taskPython qa/prepare_assets.py
    if ($LASTEXITCODE -ne 0) { throw 'Asset preparation failed.' }
    & $taskPython -m PyInstaller --noconfirm StreamGrab.spec
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
    $taskVersion = (& $taskPython -c 'from streamgrab import __version__; print(__version__)').Trim()
    $taskDist = Join-Path $taskRoot "dist\StreamGrab-$taskVersion"
    Copy-Item -LiteralPath (Join-Path $taskRoot '使用说明.txt'),(Join-Path $taskRoot 'README.md'),(Join-Path $taskRoot 'LICENSE'),(Join-Path $taskRoot 'THIRD_PARTY_NOTICES.md') -Destination $taskDist
    Copy-Item -LiteralPath (Join-Path $taskRoot 'licenses') -Destination $taskDist -Recurse -Force
} finally { Pop-Location }
