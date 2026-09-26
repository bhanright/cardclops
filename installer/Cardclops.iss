; Inno Setup script for Cardclops. Build with scripts\build_installer.ps1, which runs
; PyInstaller first (dist\Cardclops) and then this script.
;
; Installs for the current user by default (no administrator prompt, into
; %LOCALAPPDATA%\Programs), or for everyone into Program Files if the user picks that.
; The program's data never lives here: see ABOUT.txt and gallery/paths.py.

#define AppName "Cardclops"
#define AppVersion GetEnv("CARDCLOPS_VERSION")
#if AppVersion == ""
  #define AppVersion "0.1.0"
#endif
#define AppExe "Cardclops.exe"

[Setup]
AppId={{6E1D4C2A-7A3B-4F0E-9C51-2B8E5D9A1F37}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Cardclops
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=Cardclops-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
InfoBeforeFile=ABOUT.txt
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\Cardclops\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "ABOUT.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Quit {#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--quit"
Name: "{group}\About {#AppName}"; Filename: "{app}\ABOUT.txt"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Stop a running gallery and remove the daily-refresh task if the user created one.
Filename: "{app}\{#AppExe}"; Parameters: "--quit"; Flags: runhidden waituntilterminated; RunOnceId: "QuitGallery"
Filename: "{app}\{#AppExe}"; Parameters: "--remove-task"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveTask"

[UninstallDelete]
; Only the cache (card data, images, logs) is removed. Your collection, decks and price
; history in Documents\Cardclops stay.
Type: filesandordirs; Name: "{localappdata}\Cardclops"
