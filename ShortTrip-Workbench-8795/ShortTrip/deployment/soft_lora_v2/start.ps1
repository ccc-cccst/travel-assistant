$ErrorActionPreference = 'Stop'
python -u (Join-Path $PSScriptRoot 'server.py') start
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
