param([string]$Python)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    if (-not $Python) { $Python = Join-Path (Get-Location) '.venv\Scripts\python.exe' }
    & $Python -m pip install --requirement requirements-build.lock
    if ($LASTEXITCODE -ne 0) { throw 'Build dependencies failed to install.' }
    & $Python -m PyInstaller --noconfirm MARStudio.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    Copy-Item LICENSE,README.md,docs/LICENSES.md -Destination dist/MARStudio -Force
    Write-Host 'Run dist\MARStudio\MARStudio.exe. Keep that whole directory together.'
} finally { Pop-Location }

