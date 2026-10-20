; Build with Inno Setup 6 after build_desktop_windows.py.
#ifndef AppSource
  #error AppSource must point to the complete portable application folder
#endif
#ifndef InstallerOutput
  #define InstallerOutput "."
#endif
#define ClientVersion "0.1.10"

[Setup]
AppId={{8BB803B9-A7FA-47E8-8C3C-E5A6FF197702}
AppName=写作工作台
AppVersion={#ClientVersion}
AppPublisher=Story Skill
DefaultDirName={localappdata}\Programs\StoryWorkbench
DefaultGroupName=写作工作台
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#InstallerOutput}
OutputBaseFilename=story-workbench-{#ClientVersion}-windows-x86_64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\StoryWorkbench.exe
CloseApplications=no
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\写作工作台"; Filename: "{app}\StoryWorkbench.exe"
Name: "{autodesktop}\写作工作台"; Filename: "{app}\StoryWorkbench.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StoryWorkbench.exe"; Description: "启动写作工作台"; Flags: nowait postinstall skipifsilent
