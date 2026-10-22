param(
    [Parameter(Mandatory = $true)][string]$ModelId,
    [Parameter(Mandatory = $true)][string]$BaseUrl,
    [Parameter(Mandatory = $true)][string]$ModelVersion,
    [ValidateSet('LLMNeSy','ReAct','ReAct0','Act','LLM-modulo')][string]$Agent = 'LLMNeSy',
    [ValidateSet('smoke_local','pilot10_local','preview5_local','patterns9_local','patterns14_local','easy_local','medium_local','human_local')][string]$Split = 'smoke_local',
    [switch]$Oracle,
    [switch]$Evaluate,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$travelRoot = Split-Path -Parent $PSScriptRoot
$travelPython = Join-Path $travelRoot 'ChinaTravel\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $travelPython)) { throw 'Project Python environment is missing.' }
$travelPreviousKey = $env:CHINATRAVEL_OPENAI_API_KEY
$travelPreviousEncoding = $env:PYTHONIOENCODING
try {
    if (-not $Evaluate -and -not $DryRun -and -not $env:CHINATRAVEL_OPENAI_API_KEY -and -not $env:OPENAI_API_KEY) {
        $travelSecureKey = Read-Host 'API key (hidden; used for this run only)' -AsSecureString
        $travelCredential = [System.Net.NetworkCredential]::new('', $travelSecureKey)
        $env:CHINATRAVEL_OPENAI_API_KEY = $travelCredential.Password
        $travelCredential = $null
        $travelSecureKey.Dispose()
    }
    $env:PYTHONIOENCODING = 'utf-8'
    $travelArguments = @((Join-Path $PSScriptRoot 'run.py'), '--model', $ModelId, '--base-url', $BaseUrl, '--model-version', $ModelVersion, '--agent', $Agent, '--split', $Split)
    if ($Oracle) { $travelArguments += '--oracle' }
    if ($Evaluate) { $travelArguments += '--evaluate' }
    if ($DryRun) { $travelArguments += '--dry-run' }
    & $travelPython @travelArguments
    if ($LASTEXITCODE -ne 0) { throw "ChinaTravel exited with code $LASTEXITCODE" }
}
finally {
    $env:CHINATRAVEL_OPENAI_API_KEY = $travelPreviousKey
    $env:PYTHONIOENCODING = $travelPreviousEncoding
}
