#define AppName "OpenArch Swept Paths"
#ifndef AppVersion
  #define AppVersion "0.2.1"
#endif

[Setup]
AppId={{9A91B011-39D6-4CE5-81A6-48241B2C07F2}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=OpenArchSP
DefaultDirName={autopf}\OpenArch Swept Paths
DefaultGroupName=OpenArch Swept Paths
UninstallDisplayName={#AppName}
OutputDir=..\..\release-output
OutputBaseFilename=OpenArch-Swept-Paths-{#AppVersion}-Windows-x64-Setup
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\icons\openarchsp.ico
UninstallDisplayIcon={app}\OpenArchSweptPaths.exe
PrivilegesRequired=admin
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\dist\OpenArchSweptPaths\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
Root: HKA; Subkey: "Software\Classes\.oasp\OpenWithProgids"; ValueType: string; ValueName: "OpenArchSP.Project"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\OpenArchSP.Project"; ValueType: string; ValueData: "OpenArch swept path project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\OpenArchSP.Project\DefaultIcon"; ValueType: string; ValueData: "{app}\OpenArchSweptPaths.exe,0"
Root: HKA; Subkey: "Software\Classes\OpenArchSP.Project\shell\open\command"; ValueType: string; ValueData: """{app}\OpenArchSweptPaths.exe"" ""%1"""

[Icons]
Name: "{autoprograms}\OpenArch Swept Paths"; Filename: "{app}\OpenArchSweptPaths.exe"
Name: "{autodesktop}\OpenArch Swept Paths"; Filename: "{app}\OpenArchSweptPaths.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

