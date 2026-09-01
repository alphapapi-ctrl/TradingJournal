param(
    [string]$Branch = "main"
)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Trading Journal Git sync (local machine only)"
Write-Host "------------------------------------------------"

git fetch --all
if ($LASTEXITCODE -ne 0) {
    Write-Host "git fetch failed." -ForegroundColor Red
    exit 1
}

git pull --ff-only origin $Branch
if ($LASTEXITCODE -ne 0) {
    Write-Host "git pull failed. Resolve branch divergence or remote issues first." -ForegroundColor Red
    exit 1
}

Write-Host "Repository synced to latest '$Branch'." -ForegroundColor Green
Write-Host "If dependencies changed, run: python -m pip install -r requirements.txt"
Write-Host "Then restart the app."

