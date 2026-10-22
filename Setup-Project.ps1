param([switch]$SkipData)
$ErrorActionPreference = 'Stop'
$travelRoot = $PSScriptRoot
$travelPython = Join-Path $travelRoot 'ChinaTravel\.venv\Scripts\python.exe'

function Invoke-TravelPython {
    param([string[]]$Arguments)
    & $travelPython @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Python command failed; see the error above.' }
}

Push-Location -LiteralPath $travelRoot
try {
    if (-not (Test-Path -LiteralPath $travelPython)) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3.12 -m venv (Join-Path $travelRoot 'ChinaTravel\.venv')
        }
        else {
            & python -c 'import sys; assert sys.version_info[:2] == (3, 12), "Please install Python 3.12"'
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
            & python -m venv (Join-Path $travelRoot 'ChinaTravel\.venv')
        }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $travelPython)) {
            throw 'Could not create the environment. Install Python 3.12 and retry.'
        }
    }
    Invoke-TravelPython -Arguments @('-m', 'pip', 'install', '-r', (Join-Path $travelRoot 'reproduction\requirements.lock.txt'))
    if (-not $SkipData) {
        Invoke-TravelPython -Arguments @('-X', 'utf8', (Join-Path $travelRoot 'download_chinatravel.py'))
        Invoke-TravelPython -Arguments @('-X', 'utf8', (Join-Path $travelRoot 'prepare_local_queries.py'))
        Invoke-TravelPython -Arguments @('-X', 'utf8', (Join-Path $travelRoot 'reproduction\check_environment.py'))
    }
    Write-Host 'Setup complete. No paid model requests were made.'
}
finally { Pop-Location }
