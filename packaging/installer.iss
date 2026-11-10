; Inno Setup script for SplitChunkAssistant (onedir build).
; Prerequisites:
;   1) Run packaging\build.ps1 so that dist\SplitChunkAssistant\ exists.
;   2) Install Inno Setup 6 (https://jrsoftware.org/isinfo.php).
; Build:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
; Output: dist\SplitChunkAssistant-Setup.exe
;
; Notes:
; - Default is a per-user install (no admin): {autopf} resolves to
;   %LOCALAPPDATA%\Programs\SplitChunkAssistant. Users may still choose an
;   all-users install from the privileges dialog.
; - config.json and data\ are NOT bundled: on first run the app creates them next
;   to the exe, or falls back to %LOCALAPPDATA%\SplitChunkAssistant when the
;   install directory is not writable.
; - Bump MyAppVersion for each release.

#define MyAppName "SplitChunkAssistant"
#define MyAppVersion "0.3.45"
#define MyAppPublisher "SplitChunkAssistant"
#define MyAppExeName "SplitChunkAssistant.exe"

[Setup]
AppId={{8F3B2C1A-6D2E-4C59-9A87-2B4E7D5C1F60}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=SplitChunkAssistant-Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "..\dist\SplitChunkAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent


