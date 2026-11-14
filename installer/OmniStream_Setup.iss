; Inno Setup Script for OmniStream
; Builds a complete Windows Installer with Desktop & Start Menu shortcuts

#define SourcePath ".."

[Setup]
AppId={{9F82A4B1-2E19-4C51-903D-9A74E36BB0A1}
AppName=OmniStream
AppVersion=2.1.0
AppPublisher=OmniStream Team
AppPublisherURL=https://github.com/imrankhadeer/OmniStream
AppSupportURL=https://github.com/imrankhadeer/OmniStream
AppUpdatesURL=https://github.com/imrankhadeer/OmniStream
DefaultDirName={autopf}\OmniStream
DisableProgramGroupPage=yes
LicenseFile={#SourcePath}\README.md
PrivilegesRequired=lowest
OutputDir={#SourcePath}\dist
OutputBaseFilename=OmniStream_Setup_v2.1.0
SetupIconFile={#SourcePath}\static\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourcePath}\OmniStream.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\tray_app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\downloader.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\resolver.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\crawler.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\db.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\media_tools.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\cookies_mgr.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\updater.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\run.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\bin\*"; DestDir: "{app}\bin"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourcePath}\static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourcePath}\.venv\*"; DestDir: "{app}\.venv"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: VenvExists

[Icons]
Name: "{autoprograms}\OmniStream"; Filename: "{app}\OmniStream.exe"; IconFilename: "{app}\static\icon.ico"
Name: "{autodesktop}\OmniStream"; Filename: "{app}\OmniStream.exe"; IconFilename: "{app}\static\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\OmniStream.exe"; Description: "{cm:LaunchProgram,OmniStream}"; Flags: nowait postinstall skipifsilent

[Code]
function VenvExists: Boolean;
begin
  Result := DirExists(ExpandConstant('{#SourcePath}\.venv'));
end;
