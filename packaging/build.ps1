#requires -Version 5.1
<#
.SYNOPSIS
    Build the onedir Windows distribution with PyInstaller.

.DESCRIPTION
    Output: dist\SplitChunkAssistant\SplitChunkAssistant.exe + _internal\
    Also creates dist\SplitChunkAssistant-win64.zip unless -NoZip.

.PARAMETER SkipInstall
    Do not try to pip install PyInstaller when it is missing.

.PARAMETER NoZip
    Skip creating the zip archive.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#>
param(
    [switch]$SkipInstall,
    [switch]$NoZip
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    python -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        if ($SkipInstall) { throw "PyInstaller is not installed; run: python -m pip install pyinstaller" }
        Write-Host "Installing PyInstaller..."
        python -m pip install --upgrade pyinstaller
    }

    Write-Host "Building onedir distribution..."
    python -m PyInstaller --clean --noconfirm packaging\SplitChunkAssistant.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    $dist = Join-Path $root "dist\SplitChunkAssistant"
    $exe = Join-Path $dist "SplitChunkAssistant.exe"
    if (-not (Test-Path $exe)) { throw "Missing exe: $exe" }
    Write-Host "exe: $exe"

    if (-not $NoZip) {
        $zip = Join-Path $root "dist\SplitChunkAssistant-win64.zip"
        if (Test-Path $zip) { Remove-Item $zip -Force }
        Compress-Archive -Path (Join-Path $dist "*") -DestinationPath $zip
        Write-Host "zip: $zip"
    }

    Write-Host "Done."
}
finally {
    Pop-Location
}
