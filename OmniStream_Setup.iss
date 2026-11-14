; Inno Setup Script for OmniStream
; Builds a complete Windows Installer with Desktop & Start Menu shortcuts

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
LicenseFile=README.md
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=OmniStream_Setup
SetupIconFile=icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "OmniStream.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "tray_app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "downloader.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "resolver.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "crawler.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "db.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "media_tools.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "cookies_mgr.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "updater.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "run.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "icon.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: ".venv\*"; DestDir: "{app}\.venv"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: VenvExists

[Icons]
Name: "{autoprograms}\OmniStream"; Filename: "{app}\OmniStream.exe"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\OmniStream"; Filename: "{app}\OmniStream.exe"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\OmniStream.exe"; Description: "{cm:LaunchProgram,OmniStream}"; Flags: nowait postinstall skipifsilent

[Code]
function VenvExists: Boolean;
begin
  Result := DirExists(ExpandConstant('{src}\.venv'));
end;
