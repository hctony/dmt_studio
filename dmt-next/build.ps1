$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    New-Item -ItemType Directory -Path collector-bin -Force | Out-Null
    Push-Location ../collector
    try { go build -trimpath -o ../dmt-next/collector-bin/dmt-collector.exe .; if ($LASTEXITCODE -ne 0) { throw 'Go collector build failed' } } finally { Pop-Location }
    $resourcePath = Join-Path (Split-Path $PSScriptRoot -Parent) 'resources'
    python -m PyInstaller --noconfirm --onedir --name dmt-service --distpath backend-dist --workpath backend-build --specpath backend-build --paths .. --add-data "$resourcePath;resources" legacy_bridge.py
    if ($LASTEXITCODE -ne 0) { throw 'Backend build failed' }
    npx.cmd electron-builder --win --publish never
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
} finally { Pop-Location }
