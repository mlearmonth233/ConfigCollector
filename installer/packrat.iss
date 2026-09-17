; Inno Setup script for the Packrat Windows installer.
; Compiled by installer\build-windows.ps1 after PyInstaller has produced
; dist\Packrat\. Installs per user (no admin prompt) into
; %LOCALAPPDATA%\Programs\Packrat, adds Start Menu shortcuts, optionally
; starts Packrat at sign-in, and offers to start it right away.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "Packrat"
#define MyAppPublisher "MIL Networks Limited"
#define MyAppURL "https://milnetworkslimited.co.uk"
#define MyAppExeName "Packrat.exe"
#define SourceDir "..\dist\Packrat"

[Setup]
AppId={{7D6C1F0E-3B9A-4C2D-9E51-2A8F4B6C0D13}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install: no UAC prompt, and {autopf} resolves to
; %LOCALAPPDATA%\Programs. The dialog lets an admin choose all-users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=Packrat-Setup-{#MyAppVersion}-windows
SetupIconFile=assets\packrat.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start {#MyAppName} when I sign in to Windows (recommended for scheduled backups)"; GroupDescription: "Startup:"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Network config backup"
Name: "{group}\{#MyAppName} data folder"; Filename: "{localappdata}\{#MyAppName}"; Comment: "Database, logs and settings"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"" --no-browser"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Start {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The program folder only. The data folder (%LOCALAPPDATA%\Packrat) is the
; customer's database and is deliberately left in place.
Type: filesandordirs; Name: "{app}\_internal"

[Code]
// Packrat runs in the tray; stop it before files are replaced or removed.
procedure StopPackrat();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#MyAppExeName} /F /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopPackrat();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopPackrat();
  Result := True;
end;
