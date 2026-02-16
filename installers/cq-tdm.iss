; CQ TDM - Inno Setup installer script
;
; Build steps:
;   1. pyinstaller installers/cq_tdm_installer.spec
;   2. iscc installers/cq-tdm.iss
;
; The output installer will be in installers/Output/

; Read version from src/cq_tdm/__init__.py (single source of truth)
#define ParseVersion() \
    Local[0] = FileOpen(SourcePath + "\..\src\cq_tdm\__init__.py"), \
    Local[1] = FileRead(Local[0]), \
    Local[1] = FileRead(Local[0]), \
    Local[1] = FileRead(Local[0]), \
    FileClose(Local[0]), \
    Local[2] = Pos('"', Local[1]) + 1, \
    Copy(Local[1], Local[2], Pos('"', Copy(Local[1], Local[2])) - 1)

#define MyAppName "CQ TDM"
#define MyAppVersion ParseVersion()
#define MyAppPublisher "Luis"
#define MyAppURL "https://github.com/lammour/cq-tdm"
#define MyAppExeName "CQ_TDM.exe"

[Setup]
AppId={{B8F3A1E2-7C4D-4F5A-9E6B-1D2C3A4B5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
LicenseFile=..\LICENSE
OutputDir=Output
OutputBaseFilename=CQ_TDM_Setup_Windows
SetupIconFile=..\src\cq_tdm\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\CQ_TDM_installer\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\CQ_TDM_installer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
