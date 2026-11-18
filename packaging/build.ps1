# Build the portable app folder and the Windows installer.
# writtenBy('Closegamer', 2026)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$staging = Join-Path $project 'build\staging'
$dist = Join-Path $project 'dist'
$pythonHome = Split-Path -Parent (Get-Command python).Source

$iscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
if (!(Test-Path $iscc)) { $iscc = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' }
if (!(Test-Path $iscc)) { throw 'Inno Setup 6 (ISCC.exe) was not found. Install it with: winget install JRSoftware.InnoSetup' }
if (!(Test-Path (Join-Path $pythonHome 'pythonw.exe'))) { throw "pythonw.exe not found in $pythonHome" }

if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
New-Item -ItemType Directory -Force $staging, (Join-Path $staging 'app'), (Join-Path $staging 'app\vendor'), (Join-Path $staging 'models') | Out-Null
New-Item -ItemType Directory -Force $dist | Out-Null

Write-Host "Copying the Python runtime from $pythonHome ..."
robocopy $pythonHome (Join-Path $staging 'python') /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1 /XD __pycache__ .pytest_cache .mypy_cache .ruff_cache | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }

Write-Host 'Copying the app ...'
Copy-Item (Join-Path $project 'server.py'), (Join-Path $project 'index.html') -Destination (Join-Path $staging 'app') -Force
Copy-Item (Join-Path $project 'vendor\three.min.js') -Destination (Join-Path $staging 'app\vendor') -Force
Copy-Item (Join-Path $PSScriptRoot 'launcher.py'), (Join-Path $PSScriptRoot 'stop.py'), (Join-Path $PSScriptRoot 'appicon.ico') -Destination $staging -Force

Write-Host 'Staging the separation model ...'
& python (Join-Path $PSScriptRoot 'stage_models.py') (Join-Path $staging 'models')
if ($LASTEXITCODE -ne 0) { throw "stage_models.py failed with exit code $LASTEXITCODE" }

Write-Host 'Checking the staged Python imports ...'
& (Join-Path $staging 'python\python.exe') -c "import torch, demucs, basic_pitch, librosa, soundfile; print('staged imports ok')"
if ($LASTEXITCODE -ne 0) { throw "staged import check failed with exit code $LASTEXITCODE" }

Write-Host 'Building the installer ...'
Push-Location $PSScriptRoot
try {
    & $iscc 'installer.iss'
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$setup = Join-Path $dist 'GuitarAndBassAISmartRemoverSetup.exe'
if (!(Test-Path $setup)) { throw "Installer was not created at $setup" }
Write-Host "Installer ready: $setup"
Get-ChildItem $dist | Select-Object Name, @{n='GB';e={[math]::Round($_.Length/1GB,2)}}
