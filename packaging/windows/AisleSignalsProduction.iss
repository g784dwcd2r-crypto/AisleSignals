#ifndef MyAppVersion
  #error MyAppVersion is required
#endif
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef SignedBuild
  #define SignedBuild 0
#endif

[Setup]
AppId={{DD026687-3AF5-459A-AE9D-187B0482E933}
AppName=AisleSignals
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
AppPublisher=AisleSignals
AppPublisherURL=https://aislesignals.ie/
AppSupportURL=https://aislesignals.ie/support
DefaultDirName={localappdata}\Programs\AisleSignals
DefaultGroupName=AisleSignals
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
#if SignedBuild
OutputBaseFilename=AisleSignals-Windows-x86_64-v{#MyAppVersion}
#else
OutputBaseFilename=AisleSignals-Windows-x86_64-v{#MyAppVersion}-unsigned
#endif
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
RestartApplications=no
SetupLogging=yes
#if SignedBuild
SignedUninstaller=yes
SignTool=aislesignals
#else
SignedUninstaller=no
#endif
UninstallDisplayName=AisleSignals
WizardStyle=modern
MinVersion=10.0.17763

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "AisleSignalsPilot.exe"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\AisleSignalsPilot.exe"; DestDir: "{app}"; DestName: "AisleSignals.exe"; Flags: ignoreversion

[Icons]
Name: "{group}\AisleSignals"; Filename: "{app}\AisleSignals.exe"
Name: "{group}\Uninstall AisleSignals"; Filename: "{uninstallexe}"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
