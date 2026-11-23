$ErrorActionPreference = 'Stop'
python (Join-Path $PSScriptRoot 'server.py') stop
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
