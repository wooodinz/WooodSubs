; Inno Setup Script for WooodSubs (Windows Installer)
#define MyAppName "WooodSubs"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "wooodestic"
#define MyAppExeName "WooodSubs.exe"

[Setup]
AppId={{8E93DFCF-9675-49E0-ABD5-03CEA110DFF1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\WooodSubs
DefaultGroupName={#MyAppName}
OutputDir=release
OutputBaseFilename=WooodSubs-Setup-{#MyAppVersion}
SetupIconFile=assets\wooodsubs.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
