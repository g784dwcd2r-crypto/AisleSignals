#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\AisleSignalsPilot"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
AppId={{DD026687-3AF5-459A-AE9D-187B0482E933}
AppName=AisleSignals Pilot
AppVersion={#MyAppVersion}
AppPublisher=AisleSignals
DefaultDirName={localappdata}\Programs\AisleSignals Pilot
DefaultGroupName=AisleSignals Pilot
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=AisleSignalsPilot-Windows-x86_64-v{#MyAppVersion}-unsigned-pilot-setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
RestartApplications=no
SetupLogging=yes
SignedUninstaller=no
UninstallDisplayName=AisleSignals Pilot (unsigned pilot)
WizardStyle=modern
MinVersion=10.0.17763

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AisleSignals Pilot"; Filename: "{app}\AisleSignalsPilot.exe"
Name: "{group}\Uninstall AisleSignals Pilot"; Filename: "{uninstallexe}"

[Code]
function InitializeSetup(): Boolean;
begin
  SuppressibleMsgBox('This is an unsigned AisleSignals pilot build. Continue only under your pharmacy organisation''s approved installation policy. CCTV selection and monitoring must still be started and tested by staff.', mbInformation, MB_OK, IDOK);
  Result := True;
end;
