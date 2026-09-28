# Builds installer\Output\Cardclops-Setup-<version>.exe
#   1. PyInstaller bundles the app with its own Python into dist\Cardclops
#   2. Inno Setup packs that folder into a single installer
# Needs: python -m pip install pyinstaller certifi pywebview   and Inno Setup 6 (winget install JRSoftware.InnoSetup)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = (python -c "import gallery; print(gallery.__version__)").Trim()
$env:CARDCLOPS_VERSION = $version
Write-Host "Building Cardclops $version"

python -m PyInstaller --noconfirm --clean --distpath dist --workpath build installer\Cardclops.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc /Q installer\Cardclops.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Get-Item "installer\Output\Cardclops-Setup-$version.exe" | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
