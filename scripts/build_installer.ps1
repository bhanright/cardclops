# Builds installer\Output\CollectionGallery-Setup-<version>.exe
#   1. PyInstaller bundles the app with its own Python into dist\CollectionGallery
#   2. Inno Setup packs that folder into a single installer
# Needs: python -m pip install pyinstaller certifi   and Inno Setup 6 (winget install JRSoftware.InnoSetup)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = (python -c "import gallery; print(gallery.__version__)").Trim()
$env:COLLECTION_GALLERY_VERSION = $version
Write-Host "Building Collection Gallery $version"

python -m PyInstaller --noconfirm --clean --distpath dist --workpath build installer\CollectionGallery.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc /Q installer\CollectionGallery.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Get-Item "installer\Output\CollectionGallery-Setup-$version.exe" | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
