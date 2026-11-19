#define MyAppName "Guitar and Bass AI Smart Remover"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Closegamer"
#define MyAppCopyright "writtenBy('Closegamer', 2026)"
#define MyAppExeName "pythonw.exe"

[Setup]
AppId={{C7A1F4E2-6B3D-4A8E-9F1B-2D6C8E0A4B71}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppCopyright={#MyAppCopyright}
AppComments={#MyAppCopyright}
DefaultDirName={localappdata}\Programs\GuitarAndBassAISmartRemover
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=GuitarAndBassAISmartRemoverSetup
SetupIconFile=appicon.ico
UninstallDisplayIcon={app}\appicon.ico
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupLogging=yes
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName}
VersionInfoCopyright={#MyAppCopyright}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoVersion=1.0.0.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "..\build\staging\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Guitar and Bass AI Smart Remover"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\appicon.ico"
Name: "{group}\Stop Guitar and Bass AI Smart Remover"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\stop.py"""; WorkingDir: "{app}"; IconFilename: "{app}\appicon.ico"
Name: "{group}\Uninstall Guitar and Bass AI Smart Remover"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Guitar and Bass AI Smart Remover"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\appicon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; Description: "Launch Guitar and Bass AI Smart Remover"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\stop.py"" --silent"; Flags: runhidden waituntilterminated; RunOnceId: "StopServer"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\GuitarAndBassAISmartRemover\tracks"
Type: filesandordirs; Name: "{localappdata}\GuitarAndBassAISmartRemover\models"
Type: files; Name: "{localappdata}\GuitarAndBassAISmartRemover\server.log"
Type: dirifempty; Name: "{localappdata}\GuitarAndBassAISmartRemover"
Type: filesandordirs; Name: "{app}"

[Code]
procedure InitializeWizard;
begin
  WizardForm.WelcomeLabel2.Caption :=
    WizardForm.WelcomeLabel2.Caption + #13#10#13#10 +
    'writtenBy(''Closegamer'', 2026)';
end;
