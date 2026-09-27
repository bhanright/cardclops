# Cardclops for Android

A small native Kotlin app around the same Cardclops the desktop runs. Chaquopy bundles Python 3.13
with the engine (`gallery/`, plus `certifi` and `numpy`), and a WebView shows the same web page
(`static/`). Nothing is rewritten for the phone: `gallery/android.py` starts the engine on a free
port on 127.0.0.1, and the WebView loads it with a per-launch secret token (a cookie), so other apps
on the phone get 403.

- `app/src/main/java/com/cardclops/app/Engine.kt` starts Python and the engine once per process,
  and unpacks the web page from the APK's assets into `noBackupFilesDir/static` once per install.
- `MainActivity.kt` is the loading screen and the WebView: file picker for the CSV and deck-file
  inputs, outside links to the browser, "Missing list as text" to the share sheet, Back closes the
  card viewer and then walks the page's history, the screen stays on while a download or import runs.
- Data: `filesDir/cardclops` is the user's database and its backups (Android backup includes only
  `cardclops.sqlite`); `noBackupFilesDir/cache` is Scryfall's card data and images (rebuildable).

## Building

Once per machine:

- JDK 17 or newer as `JAVA_HOME`, and the Android SDK (platform 37). Point `android/local.properties`
  at the SDK: `sdk.dir=C\:/Users/<you>/AppData/Local/Android/Sdk` (the colon escaped).
- Python 3.13 (Chaquopy compiles the app's Python with the same version): `uv python install 3.13`.
- In `%USERPROFILE%\.gradle\gradle.properties` (never in the repository):

  ```
  cardclopsBuildPython=C:/Users/<you>/AppData/Roaming/uv/python/cpython-3.13.<n>-windows-x86_64-none/python.exe
  cardclopsKeystore=C:/Users/<you>/.cardclops/cardclops-release.jks
  cardclopsKeystorePassword=...
  cardclopsKeyAlias=cardclops
  cardclopsKeyPassword=...
  ```

Then `scripts\build_android.ps1` makes a clean signed build and copies it to
`installer\Output\Cardclops-<version>.apk`. From `android\`, `.\gradlew assembleDebug` gives
`app\build\outputs\cardclops\Cardclops-<version>-debug.apk` (debuggable, WebView inspectable from
`chrome://inspect`).

The version comes from `gallery/__init__.py`: 0.1.2 is versionName "0.1.2", versionCode 102.

## Installing

- With a cable: turn on USB debugging on the phone, then `adb install -r installer\Output\Cardclops-0.1.2.apk`.
- Without one: send the APK to the phone (email, Drive, USB copy), open it, and allow "Install
  unknown apps" for the app that opened it when Android asks.

The first launch shows the setup wizard. The download and import take a few minutes on a phone;
keep Cardclops open and in front until it finishes (the screen stays on by itself while it works).
Export the collection CSV from ManaBox (or another app) to the phone's Downloads first, then choose
it in the wizard.

## Updates

Build the new version (raise `__version__` in `gallery/__init__.py` so the versionCode goes up) and
install it over the old one the same way. Android keeps the app's data across updates **only if
the new APK is signed with the same key**, so `cardclops-release.jks` and its password must be backed
up: a lost key means the next version can only be installed after uninstalling, which deletes the
collection database. Debug and release builds are signed with different keys, so switching between
them also needs an uninstall.

## Versions

Chaquopy 17.0.0 (the newest, December 2025) supports Android Gradle Plugin 7.3 to 9.2, so the
project uses AGP 9.2.1 with Gradle 9.7.1, not a newer AGP. Python 3.13 is the newest version
Chaquopy has numpy wheels for (1.26.2). minSdk 26 (Android 8.0), targetSdk 36, compileSdk 37;
ABI arm64-v8a only: Python 3.12+ has no 32-bit builds, so that is every phone Cardclops can run on.
To run on an x86_64 emulator, add "x86_64" to `abiFilters` in `app/build.gradle.kts` (about 16 MB
more; Chaquopy reads only the defaultConfig list, not per-build-type lists).

`gradlew clean assembleRelease` in one command fails: Chaquopy writes its ProGuard rules into
`build/` while the build is configured, and the clean deletes them. Run `clean` and
`assembleRelease` separately, as the build script does.
