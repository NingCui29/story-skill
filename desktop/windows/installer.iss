; Build with Inno Setup 6 after build_desktop_windows.py.
#ifndef AppSource
  #error AppSource must point to the complete portable application folder
#endif
#ifndef InstallerOutput
  #define InstallerOutput "."
#endif
#define ClientVersion "0.2.0"
#define WebViewInstaller "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"

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
Source: "{#AppSource}\Prerequisites\{#WebViewInstaller}"; Flags: dontcopy

[InstallDelete]
; Only an authenticated legacy installation may lose its installer-owned payload.
Type: filesandordirs; Name: "{app}\_internal"; Check: RemoveLegacyPayload
Type: filesandordirs; Name: "{app}\ThirdPartyLicenses\Qt"; Check: RemoveLegacyPayload
Type: filesandordirs; Name: "{app}\ThirdPartyLicenses\PySide6"; Check: RemoveLegacyPayload
Type: filesandordirs; Name: "{app}\ThirdPartyLicenses\PySide6_Essentials"; Check: RemoveLegacyPayload
Type: filesandordirs; Name: "{app}\ThirdPartyLicenses\PySide6_Addons"; Check: RemoveLegacyPayload
Type: filesandordirs; Name: "{app}\ThirdPartyLicenses\shiboken6"; Check: RemoveLegacyPayload
Type: filesandordirs; Name: "{app}\ThirdPartyLicenses\pyinstaller"; Check: RemoveLegacyPayload

[Icons]
Name: "{group}\写作工作台"; Filename: "{app}\StoryWorkbench.exe"
Name: "{autodesktop}\写作工作台"; Filename: "{app}\StoryWorkbench.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StoryWorkbench.exe"; Description: "启动写作工作台"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebViewKey = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8BB803B9-A7FA-47E8-8C3C-E5A6FF197702}_is1';
var
  LegacyPayloadVerified: Boolean;

function ValidRuntimeVersion(Value: String): Boolean;
var
  I, Dots, Digits: Integer;
  NonZero: Boolean;
begin
  Dots := 0; Digits := 0; NonZero := False; Result := False;
  for I := 1 to Length(Value) do begin
    if Value[I] = '.' then begin
      if Digits = 0 then exit;
      Dots := Dots + 1; Digits := 0;
    end else if (Value[I] >= '0') and (Value[I] <= '9') then begin
      Digits := Digits + 1;
      if Value[I] <> '0' then NonZero := True;
    end else exit;
  end;
  Result := (Dots = 3) and (Digits > 0) and NonZero;
end;

function RuntimeAt(RootKey: Integer): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(RootKey, WebViewKey, 'pv', Version) and ValidRuntimeVersion(Version);
end;

function WebViewRuntimeInstalled: Boolean;
begin
  Result := RuntimeAt(HKCU32) or RuntimeAt(HKCU64) or RuntimeAt(HKLM32);
end;

function RemoveLegacyPayload: Boolean;
begin
  Result := LegacyPayloadVerified;
end;

function VerifyLegacyPayload: Boolean;
var
  RegisteredPath: String;
  Manifest: AnsiString;
begin
  Result := RegQueryStringValue(HKCU, UninstallKey, 'Inno Setup: App Path', RegisteredPath);
  if not Result then exit;
  Result := CompareText(AddBackslash(ExpandFileName(RegisteredPath)),
                        AddBackslash(ExpandConstant('{app}'))) = 0;
  if not Result then exit;
  Result := FileExists(ExpandConstant('{app}\unins000.exe')) and
            FileExists(ExpandConstant('{app}\backend_runner.py')) and
            FileExists(ExpandConstant('{app}\runtime\python.exe')) and
            FileExists(ExpandConstant('{app}\Scripts\story.py')) and
            LoadStringFromFile(ExpandConstant('{app}\build-manifest.json'), Manifest);
  if not Result then exit;
  Result := (Pos('"platform": "windows-x86_64"', String(Manifest)) > 0) and
            (Pos('"client_version": "0.1.', String(Manifest)) > 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  Installed: Boolean;
begin
  Result := '';
  LegacyPayloadVerified := False;
  if DirExists(ExpandConstant('{app}\_internal')) or
     DirExists(ExpandConstant('{app}\ThirdPartyLicenses\Qt')) then begin
    LegacyPayloadVerified := VerifyLegacyPayload;
    if not LegacyPayloadVerified then begin
      Result := '无法确认目标目录属于旧版写作工作台。请改用空目录安装，原目录未清理。';
      exit;
    end;
  end;
  if WebViewRuntimeInstalled then exit;
  ExtractTemporaryFile('{#WebViewInstaller}');
  Installed := ExecAsOriginalUser(ExpandConstant('{tmp}\{#WebViewInstaller}'),
    '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
  if not Installed then begin
    Result := '无法启动随安装包提供的 WebView2 离线安装器。安装已停止。';
    exit;
  end;
  if ((ExitCode <> 0) and (ExitCode <> 3010) and (ExitCode <> 1641)) or
     not WebViewRuntimeInstalled then begin
    Result := Format('WebView2 运行时安装未完成（代码 %d）。安装已停止，请重试。', [ExitCode]);
    exit;
  end;
  NeedsRestart := (ExitCode = 3010) or (ExitCode = 1641);
end;
