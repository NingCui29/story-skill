; Build with Inno Setup 6 after build_desktop_windows.py.
#ifndef AppSource
  #error AppSource must point to the complete portable application folder
#endif
#ifndef InstallerOutput
  #define InstallerOutput "."
#endif
#define ClientVersion "0.3.0"
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
#ifdef LifecycleFailureProbe
OutputBaseFilename=story-workbench-lifecycle-failure-probe
#else
#ifdef UpdateOnly
OutputBaseFilename=story-workbench-{#ClientVersion}-windows-x86_64-update-setup
#else
OutputBaseFilename=story-workbench-{#ClientVersion}-windows-x86_64-setup
#endif
#endif
Compression=lzma2
#ifdef UpdateOnly
SolidCompression=no
#else
SolidCompression=yes
#endif
WizardStyle=modern
UninstallDisplayIcon={app}\StoryWorkbench.exe
CloseApplications=no
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
; An independent interpreter permits renaming the old installation and removing
; its runtime without locking it with the lifecycle helper itself.
Source: "{#AppSource}\runtime\*"; DestDir: "{tmp}\lifecycle\runtime"; Flags: dontcopy recursesubdirs createallsubdirs
Source: "{#AppSource}\installation_lifecycle.py"; DestDir: "{tmp}\lifecycle"; Flags: dontcopy
#ifndef UpdateOnly
Source: "{#AppSource}\Prerequisites\{#WebViewInstaller}"; Flags: dontcopy
#endif
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\写作工作台"; Filename: "{app}\StoryWorkbench.exe"
Name: "{autodesktop}\写作工作台"; Filename: "{app}\StoryWorkbench.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StoryWorkbench.exe"; Description: "启动写作工作台"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebViewKey = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  LifecycleMutex = 'Local\StoryWorkbench.InstallLifecycle';
var
  LifecycleReady, UpgradePrepared, UpgradeFinished: Boolean;
  LifecycleHandle: THandle;
  PostInstallExitCode: Integer;

function CreateLifecycleMutex(Attributes: LongWord; Owner: Boolean; Name: String): THandle;
  external 'CreateMutexW@kernel32.dll stdcall';
function CloseLifecycleHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

procedure ReleaseLifecycleMutex;
begin
  if LifecycleHandle <> 0 then begin
    CloseLifecycleHandle(LifecycleHandle); LifecycleHandle := 0;
  end;
end;

function AcquireLifecycleMutex: Boolean;
var
  ErrorCode: LongWord;
begin
  LifecycleHandle := CreateLifecycleMutex(0, False, LifecycleMutex);
  ErrorCode := DLLGetLastError;
  Result := (LifecycleHandle <> 0) and (ErrorCode <> 183);
  if not Result then ReleaseLifecycleMutex;
end;

function InitializeSetup: Boolean;
begin
  Result := AcquireLifecycleMutex;
  if not Result then SuppressibleMsgBox('另一个安装或卸载正在进行，或无法取得安装锁。请等待完成后重试。', mbError, MB_OK, IDOK);
end;

function LifecycleCommand(Action, Extra: String; var ExitCode: Integer): Boolean;
begin
  Result := Exec(ExpandConstant('{tmp}\lifecycle\runtime\python.exe'),
    '-I -B "' + ExpandConstant('{tmp}\lifecycle\installation_lifecycle.py') + '" ' + Action +
    ' --app "' + ExpandConstant('{app}') + '" --report "' + ExpandConstant('{tmp}\lifecycle-result.json') +
    '" --journal "' + ExpandConstant('{tmp}\lifecycle-journal.json') + '" ' + Extra,
    ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ExitCode);
end;

function LifecycleMessage: String;
var
  Text: AnsiString;
begin
  Result := '无法完成安全交接。请保存并关闭所有工作台窗口、正常停止作品服务后重试。原安装文件将保留。';
  if LoadStringFromFile(ExpandConstant('{tmp}\lifecycle-result.txt'), Text) then
    Result := UTF8Decode(Text);
end;

function CheckLifecycle(IsUninstall: Boolean): String;
var
  ExitCode: Integer;
  Extra: String;
  SilentMode: Boolean;
begin
  Result := ''; Extra := '';
  if IsUninstall then begin Extra := '--uninstall'; SilentMode := UninstallSilent; end
  else SilentMode := WizardSilent;
  if not LifecycleCommand('check', Extra, ExitCode) then begin
    Result := '无法启动安装安全检查，操作已停止。'; exit;
  end;
  if ExitCode = 10 then begin
    if SilentMode then begin
      Result := '仍有作品服务运行。静默安装/卸载不会代替作者确认保存；请保存并正常停止服务后重试。'; exit;
    end;
    if MsgBox('书架或作品服务仍在运行。请先保存工作台及外部浏览器中所有需要保留的编辑。' + #13#10 +
      '确认已全部保存后，安装器将正常停止已认证的服务。是否继续？',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then begin
      Result := '尚未确认全部保存，操作已停止。'; exit;
    end;
    if not LifecycleCommand('check', Extra + ' --saved', ExitCode) then begin
      Result := '无法正常停止服务，操作已停止。'; exit;
    end;
  end;
  if ExitCode <> 0 then Result := LifecycleMessage;
end;

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
  Result := RuntimeAt(HKCU32) or RuntimeAt(HKCU64) or RuntimeAt(HKLM32) or RuntimeAt(HKLM64);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  Installed: Boolean;
begin
  Result := '';
#ifdef UpdateOnly
  if not WebViewRuntimeInstalled then begin
    Result := '本更新包需要已有的 WebView2 运行时。请下载完整离线安装包（文件名以 -setup.exe 结尾且不含 -update），安装后再打开工作台。'; exit;
  end;
#endif
  if not LifecycleReady then begin
    ExtractTemporaryFiles('{tmp}\lifecycle\*');
    LifecycleReady := True;
  end;
  Result := CheckLifecycle(False);
  if Result <> '' then exit;
  if not WebViewRuntimeInstalled then begin
#ifndef UpdateOnly
    ExtractTemporaryFile('{#WebViewInstaller}');
    Installed := ExecAsOriginalUser(ExpandConstant('{tmp}\{#WebViewInstaller}'),
      '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
    if not Installed then begin
      Result := '无法启动随安装包提供的 WebView2 离线安装器。安装已停止。'; exit;
    end;
    if ((ExitCode <> 0) and (ExitCode <> 3010) and (ExitCode <> 1641)) or
       not WebViewRuntimeInstalled then begin
      Result := Format('WebView2 运行时安装未完成（代码 %d）。安装已停止，请重试。', [ExitCode]); exit;
    end;
    NeedsRestart := (ExitCode = 3010) or (ExitCode = 1641);
#endif
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
begin
  if CurStep = ssInstall then begin
    UpgradePrepared := True;
    if not LifecycleCommand('prepare', '--shortcut "' + ExpandConstant('{group}\写作工作台.lnk') +
      '" --shortcut "' + ExpandConstant('{autodesktop}\写作工作台.lnk') + '"', ExitCode) or (ExitCode <> 0) then
      RaiseException(LifecycleMessage);
  end;
  if CurStep = ssPostInstall then begin
    { Inno handles exceptions in ssPostInstall and otherwise may still return 0.
      Only a completed commit clears this failure status. }
    PostInstallExitCode := 21;
#ifdef LifecycleFailureProbe
    { Compile-only acceptance fixture, absent from the distributed installer. }
    SaveStringToFile(ExpandConstant('{app}\lifecycle-failure-unowned.txt'),
      'isolated rollback acceptance; preserve this untracked file', False);
    RaiseException('isolated rollback acceptance');
#endif
    { Files, shortcuts and registry are complete. Commit before [Run] can start
      the new host, then release its startup gate. }
    UpgradeFinished := True;
    if not LifecycleCommand('commit', '', ExitCode) or (ExitCode <> 0) then
      RaiseException('新版安装完成，但未能写入迁移回执。旧版备份仍保留。' + #13#10 + LifecycleMessage);
    ReleaseLifecycleMutex;
    PostInstallExitCode := 0;
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  Result := PostInstallExitCode;
end;

procedure DeinitializeSetup;
var
  ExitCode: Integer;
begin
  if UpgradePrepared and not UpgradeFinished then begin
    if not LifecycleCommand('rollback', '', ExitCode) or (ExitCode <> 0) then
      SuppressibleMsgBox('未能自动恢复旧版。请保留安装目录旁的 .previous- 备份，不要清理。' + #13#10 +
        LifecycleMessage, mbError, MB_OK, IDOK);
  end;
  ReleaseLifecycleMutex;
end;

function InitializeUninstall: Boolean;
var
  ExitCode: Integer;
  ErrorText: String;
begin
  Result := False;
  if not AcquireLifecycleMutex then begin
    SuppressibleMsgBox('另一个安装或卸载正在进行，请等待完成。', mbError, MB_OK, IDOK); exit;
  end;
  { This first process only copies its interpreter/helper and exits. All checks
    and service handoff then run independently outside the installation. }
  if not Exec(ExpandConstant('{app}\runtime\python.exe'),
    '-I -B "' + ExpandConstant('{app}\installation_lifecycle.py') + '" stage --app "' +
    ExpandConstant('{app}') + '" --destination "' + ExpandConstant('{tmp}\lifecycle') +
    '" --report "' + ExpandConstant('{tmp}\lifecycle-result.json') + '"',
    ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then begin
    SuppressibleMsgBox('无法准备独立卸载检查，未删除安装文件。' + #13#10 + LifecycleMessage, mbError, MB_OK, IDOK); exit;
  end;
  ErrorText := CheckLifecycle(True);
  if ErrorText <> '' then begin SuppressibleMsgBox(ErrorText, mbError, MB_OK, IDOK); exit; end;
  Result := True;
end;

procedure DeinitializeUninstall;
begin
  ReleaseLifecycleMutex;
end;
