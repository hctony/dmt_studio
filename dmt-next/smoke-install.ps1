# Run on the disposable Windows CI runner, never against a developer installation.
$ErrorActionPreference='Stop'
if ($env:CI -ne 'true') { throw 'Installer smoke test requires an isolated CI runner' }
$version=(Get-Content (Join-Path $PSScriptRoot 'package.json') | ConvertFrom-Json).version
$installer=Join-Path $PSScriptRoot "release/DMT Analysis Studio Setup $version.exe"
$destination=Join-Path $env:RUNNER_TEMP 'DMT Installer Verification'
$process=Start-Process -FilePath $installer -ArgumentList @('/S',"/D=$destination") -WindowStyle Hidden -PassThru
if(-not $process.WaitForExit(120000)){throw 'Installer timed out'}
if($process.ExitCode -ne 0){throw "Installer failed: $($process.ExitCode)"}
python (Join-Path $PSScriptRoot 'smoke-frozen.py') (Join-Path $destination 'resources/backend/dmt-service.exe')
if($LASTEXITCODE -ne 0){throw 'Installed backend failed validation'}
