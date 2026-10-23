; Clean-room Inno Setup 6 Installer Script for Dewdrop
#define AppName "Dewdrop"
#define AppExe "dewdrop.exe"
#define WorkerExe "dewdrop-worker.exe"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{9F4E7C18-3D6B-4E2A-9A51-7B82D410E6F3}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
OutputDir=..\dist
OutputBaseFilename=Dewdrop-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\dewdrop\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\target\release\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\target\release\{#WorkerExe}"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\dewdrop_core.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dewdrop_runtime.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\liquid_optics.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\tests\run_cleanroom_verification.py"; DestDir: "{app}\tests"; Flags: ignoreversion
Source: "..\assets\icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "..\assets\icon-64.png"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "..\assets\logo-1024.png"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "..\assets\icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\ffmpeg.exe"; DestDir: "{app}"; Check: ShouldInstallFfmpeg; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{userprograms}\{#AppName}\Change Hotkey"; Filename: "{app}\{#AppExe}"; Parameters: "--configure-hotkey"; IconFilename: "{app}\{#AppExe}"

[Code]
var
  HotkeyPage: TWizardPage;
  ModifierCombo: TNewComboBox;
  KeyCombo: TNewComboBox;
  FfmpegPage: TWizardPage;
  FfmpegCheck: TNewCheckBox;
  FirewallCheck: TNewCheckBox;

function ShouldInstallFfmpeg: Boolean;
begin
  Result := Assigned(FfmpegCheck) and FfmpegCheck.Checked;
end;

function SelectedHotkeyString: String;
begin
  Result := ModifierCombo.Text + '+' + KeyCombo.Text;
end;

procedure InitializeWizard;
var
  LblMod, LblKey, LblInfo: TNewStaticText;
  I: Integer;
begin
  HotkeyPage := CreateCustomPage(wpSelectDir, 'Activation Shortcut Key',
    'Select the shortcut key combination that arms Dewdrop for 2 minutes.');

  LblInfo := TNewStaticText.Create(HotkeyPage);
  LblInfo.Parent := HotkeyPage.Surface;
  LblInfo.Caption := 'Windows monitors this shortcut via the Start Menu entry so zero background processes run until you press the key.';
  LblInfo.Top := 0;
  LblInfo.Left := 0;
  LblInfo.Width := HotkeyPage.SurfaceWidth;
  LblInfo.AutoSize := True;

  LblMod := TNewStaticText.Create(HotkeyPage);
  LblMod.Parent := HotkeyPage.Surface;
  LblMod.Caption := 'Modifier keys:';
  LblMod.Top := 44;
  LblMod.Left := 0;

  ModifierCombo := TNewComboBox.Create(HotkeyPage);
  ModifierCombo.Parent := HotkeyPage.Surface;
  ModifierCombo.Style := csDropDownList;
  ModifierCombo.Top := 64;
  ModifierCombo.Left := 0;
  ModifierCombo.Width := 220;
  ModifierCombo.Items.Add('CTRL+ALT');
  ModifierCombo.Items.Add('CTRL+SHIFT+ALT');
  ModifierCombo.Items.Add('CTRL+SHIFT');
  ModifierCombo.ItemIndex := 0;

  LblKey := TNewStaticText.Create(HotkeyPage);
  LblKey.Parent := HotkeyPage.Surface;
  LblKey.Caption := 'Trigger key:';
  LblKey.Top := 104;
  LblKey.Left := 0;

  KeyCombo := TNewComboBox.Create(HotkeyPage);
  KeyCombo.Parent := HotkeyPage.Surface;
  KeyCombo.Style := csDropDownList;
  KeyCombo.Top := 124;
  KeyCombo.Left := 0;
  KeyCombo.Width := 140;
  for I := Ord('A') to Ord('Z') do
    KeyCombo.Items.Add(Chr(I));
  for I := Ord('0') to Ord('9') do
    KeyCombo.Items.Add(Chr(I));
  for I := 1 to 12 do
    KeyCombo.Items.Add('F' + IntToStr(I));
  KeyCombo.ItemIndex := 25; // Default 'Z' -> CTRL+ALT+Z

  FfmpegPage := CreateCustomPage(HotkeyPage.ID, 'Media Tools & Network Hardening',
    'Choose whether to bundle offline FFmpeg media tools and outbound firewall rules.');

  FfmpegCheck := TNewCheckBox.Create(FfmpegPage);
  FfmpegCheck.Parent := FfmpegPage.Surface;
  FfmpegCheck.Top := 8;
  FfmpegCheck.Left := 0;
  FfmpegCheck.Width := FfmpegPage.SurfaceWidth;
  FfmpegCheck.Caption := 'Install bundled offline FFmpeg (enables the 12 audio and video tools)';
  FfmpegCheck.Checked := True;

  FirewallCheck := TNewCheckBox.Create(FfmpegPage);
  FirewallCheck.Parent := FfmpegPage.Surface;
  FirewallCheck.Top := 44;
  FirewallCheck.Left := 0;
  FirewallCheck.Width := FfmpegPage.SurfaceWidth;
  FirewallCheck.Caption := 'Add Windows Firewall outbound block rules for all executables (optional UAC prompt)';
  FirewallCheck.Checked := False;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    Exec(ExpandConstant('{app}\{#AppExe}'), '--make-shortcut "' + SelectedHotkeyString + '"',
         ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if FirewallCheck.Checked then
    begin
      ShellExec('runas', ExpandConstant('{sys}\netsh.exe'),
        'advfirewall firewall add rule name="Dewdrop Outbound Block" dir=out program="' + ExpandConstant('{app}\{#AppExe}') + '" action=block',
        '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      ShellExec('runas', ExpandConstant('{sys}\netsh.exe'),
        'advfirewall firewall add rule name="Dewdrop Worker Outbound Block" dir=out program="' + ExpandConstant('{app}\{#WorkerExe}') + '" action=block',
        '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#AppExe} /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#WorkerExe} /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    DeleteFile(ExpandConstant('{userprograms}\{#AppName}.lnk'));
    if MsgBox('Do you also want to remove your saved usage statistics and settings in %APPDATA%\Dewdrop?', mbConfirmation, MB_YESNO) = IDYES then
      DelTree(ExpandConstant('{userappdata}\{#AppName}'), True, True, True);
  end;
end;
