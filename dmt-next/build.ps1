$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $resourcePath = Join-Path (Split-Path $PSScriptRoot -Parent) 'resources'
    python -m PyInstaller --noconfirm --onedir --name dmt-service --distpath backend-dist --workpath backend-build --specpath backend-build --paths .. --add-data "$resourcePath;resources" legacy_bridge.py
    if ($LASTEXITCODE -ne 0) { throw 'Backend build failed' }
    npx.cmd electron-builder --win --publish never
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
} finally { Pop-Location }
