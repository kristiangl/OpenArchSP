#define AppName "OpenArch Swept Paths"
#ifndef AppVersion
  #define AppVersion "0.1.0"
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

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\dist\OpenArchSweptPaths\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\OpenArch Swept Paths"; Filename: "{app}\OpenArchSweptPaths.exe"
Name: "{autodesktop}\OpenArch Swept Paths"; Filename: "{app}\OpenArchSweptPaths.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

