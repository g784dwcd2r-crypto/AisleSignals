param(
  [Parameter(Mandatory = $true)][string]$Bundle,
  [Parameter(Mandatory = $true)][string]$OutputDir,
  [Parameter(Mandatory = $true)][string]$UpdatePrivateKey,
  [Parameter(Mandatory = $true)][string]$Version
)
$ErrorActionPreference = 'Stop'

# Nothing is copied, signed or compiled until the source, identity, update key,
# certificate selector and tools have all passed the fail-closed preflight.
python scripts/release_preflight.py --platform Windows --update-private-key-file $UpdatePrivateKey
if ($LASTEXITCODE -ne 0) { throw 'Desktop release signing preflight failed.' }
$bundlePath = (Resolve-Path -LiteralPath $Bundle).Path
$outputPath = (Resolve-Path -LiteralPath $OutputDir).Path
$sourceExe = Join-Path $bundlePath 'AisleSignalsPilot.exe'
if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf)) { throw 'The Windows application bundle is missing.' }
$identity = Get-Content -LiteralPath 'packaging/release-identity.json' -Raw | ConvertFrom-Json
if ($Version -ne $identity.version) { throw 'The requested version does not match the production release identity.' }
$expectedInstaller = Join-Path $outputPath "AisleSignals-Windows-x86_64-v$Version.exe"
if (Test-Path -LiteralPath $expectedInstaller) { throw 'Refusing to replace an existing signed installer.' }

$temporaryRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$temporary = Join-Path $temporaryRoot ("aislesignals-sign-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
  $signedBundle = Join-Path $temporary 'AisleSignalsPilot'
  Copy-Item -LiteralPath $bundlePath -Destination $signedBundle -Recurse
  $signedExe = Join-Path $signedBundle 'AisleSignalsPilot.exe'
  if ((Get-AuthenticodeSignature -LiteralPath $signedExe).Status -ne 'NotSigned') {
    throw 'Refusing an application executable which already has a signature.'
  }
  & signtool sign /sha1 $env:AISLESIGNALS_WINDOWS_CERT_SHA1 /fd SHA256 /td SHA256 `
    /tr 'http://timestamp.acs.microsoft.com' $signedExe
  if ($LASTEXITCODE -ne 0) { throw 'Windows application signing failed.' }
  & signtool verify /pa /all /v $signedExe
  if ($LASTEXITCODE -ne 0) { throw 'Windows application signature verification failed.' }

  # Inno substitutes its literal $f placeholder with each executable that it
  # generates. This signs the uninstaller as well as the final setup program.
  $innoSign = 'signtool sign /sha1 ' + $env:AISLESIGNALS_WINDOWS_CERT_SHA1 + `
    ' /fd SHA256 /td SHA256 /tr http://timestamp.acs.microsoft.com $f'
  & iscc "/DMyAppVersion=$Version" "/DSourceDir=$signedBundle" "/DOutputDir=$outputPath" `
    '/DSignedBuild=1' "/Saislesignals=$innoSign" packaging/windows/AisleSignalsProduction.iss
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $expectedInstaller -PathType Leaf)) {
    throw 'Signed Windows installer compilation failed.'
  }
  & signtool verify /pa /all /v $expectedInstaller
  if ($LASTEXITCODE -ne 0) { throw 'Windows installer signature verification failed.' }
  $verified = Get-AuthenticodeSignature -LiteralPath $expectedInstaller
  if ($verified.Status -ne 'Valid') { throw "Windows signature status is $($verified.Status)." }
  Write-Output $expectedInstaller
} finally {
  Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
