; ExpBot v1.0 — Inno Setup 6 script
; Build after portable folder exists: dist\ExpBot-v1.0-Windows

#define MyAppName "ExpBot"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "ExpBot"
#define MyAppExeName "ExpBot.exe"
#define MyAppId "{{A3E7B0D1-4F62-4C8A-9B1E-7D2C5F8A9012}}"
#define SourceDir "..\dist\ExpBot-v1.0-Windows"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=ExpBot-v1.0-Setup
SetupIconFile=..\app\web\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
InfoBeforeFile=..\使用说明.md
LicenseFile=..\LICENSE
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "data\*;output\*;startup-error.txt;.qa*"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\README"; Filename: "{app}\使用说明.md"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch ExpBot"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\desktop"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\certs"
Type: filesandordirs; Name: "{app}\licenses"
Type: dirifempty; Name: "{app}"
