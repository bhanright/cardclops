# Builds the signed Android app, installer\Output\Cardclops-<version>.apk
#   A clean Gradle build of android\ (the Kotlin shell, the Python engine from gallery\ through
#   Chaquopy, and the web page from static\), signed with the release key.
# Needs: JDK 17+ (JAVA_HOME), the Android SDK (android\local.properties or ANDROID_HOME), Python 3.13
# for Chaquopy, and the signing properties in %USERPROFILE%\.gradle\gradle.properties.
# See android\README.md.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$android = Join-Path $root "android"

$version = ((Get-Content (Join-Path $root "gallery\__init__.py") -Raw) |
    Select-String '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "Building Cardclops $version for Android"

$userProperties = Join-Path $env:USERPROFILE ".gradle\gradle.properties"
if (-not ((Test-Path $userProperties) -and (Select-String -Path $userProperties -Pattern '^cardclopsKeystore=' -Quiet))) {
    throw "No release signing settings in $userProperties (see android\README.md)"
}

Push-Location $android
try {
    # clean and assembleRelease run as two Gradle invocations: Chaquopy writes its ProGuard rules
    # into build\ while the build is configured, and a clean in the same run would delete them
    # before R8 reads them. Gradle on Windows sometimes can't delete build folders a daemon still
    # holds open; stopping the daemons and retrying once gets past it.
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        .\gradlew.bat clean --console=plain
        if ($LASTEXITCODE -eq 0) { .\gradlew.bat assembleRelease --console=plain }
        if ($LASTEXITCODE -eq 0) { break }
        if ($attempt -eq 2) { throw "Gradle build failed" }
        Write-Host "Build failed; stopping Gradle and trying once more"
        .\gradlew.bat --stop | Out-Null
        Start-Sleep -Seconds 3
    }
} finally {
    Pop-Location
}

$apk = Join-Path $android "app\build\outputs\cardclops\Cardclops-$version.apk"
if (-not (Test-Path $apk)) { throw "Expected $apk" }
$output = Join-Path $root "installer\Output"
New-Item -ItemType Directory -Force $output | Out-Null
Copy-Item $apk $output -Force

Get-Item (Join-Path $output "Cardclops-$version.apk") | Select-Object FullName, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
