[CmdletBinding()]
param(
    [string]$Out = 'artifacts/query_checks/EXP014_v1',
    [string]$AfterStatus,
    [int]$Port = 18080,
    [double]$Seconds = 120,
    [string[]]$Only,
    [switch]$Compact
)

$ErrorActionPreference = 'Stop'
$tripRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Set-Location -LiteralPath $tripRoot
$tripOut = (New-Item -ItemType Directory -Path $Out -Force).FullName
if ($AfterStatus) {
    $tripDeadline = [datetime]::UtcNow.AddHours(1)
    while ((Get-Content -LiteralPath $AfterStatus -Raw -Encoding utf8 | ConvertFrom-Json).state -eq 'running') {
        if ([datetime]::UtcNow -ge $tripDeadline) { throw 'The preceding GPU experiment is still running.' }
        Start-Sleep -Seconds 15
    }
}

$tripEndpoint = "http://127.0.0.1:$Port"
$tripOther = Get-Process -Name llama-server -ErrorAction SilentlyContinue
if ($Compact -and $tripOther) { throw 'Another llama-server is running. Wait for it or use its endpoint.' }
$tripExisting = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($tripExisting) { throw "Port $Port is already in use. Choose an unused loopback port." }
$tripRuntime = (Resolve-Path artifacts/local).Path
$tripExecutable = Join-Path $tripRuntime 'llama-b12211-cuda12.4/llama-server.exe'
$tripModel = Join-Path $tripRuntime 'Qwen3.5-4B-Q8_0.gguf'
$tripContext = 16384
if ($Compact) { $tripContext = 12288 }
$tripArguments = @(
    '--model', ('"' + $tripModel + '"'), '--alias', 'qwen3.5-4b-q8',
    '--host', '127.0.0.1', '--port', $Port, '--ctx-size', $tripContext, '--parallel', '1',
    '--device', 'CUDA0', '--n-gpu-layers', '99', '--flash-attn', 'on', '--jinja', '--reasoning-budget', '0'
)
if ($Compact) {
    $tripArguments += @('--batch-size', '512', '--ubatch-size', '128', '--cache-ram', '0',
        '--threads', '4', '--threads-batch', '4', '--prio', '-1')
}
$env:LLAMA_ARG_CHAT_TEMPLATE_KWARGS = '{"enable_thinking":false}'
$tripTimestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$tripErrorLog = Join-Path $tripOut "model-$tripTimestamp.stderr.log"
$tripServer = Start-Process -FilePath $tripExecutable -ArgumentList $tripArguments -WindowStyle Hidden -PassThru `
    -RedirectStandardError $tripErrorLog `
    -RedirectStandardOutput (Join-Path $tripOut "model-$tripTimestamp.stdout.log")
try {
    $tripDeadline = [datetime]::UtcNow.AddSeconds(100)
    do {
        $tripServer.Refresh()
        if ($tripServer.HasExited -or [datetime]::UtcNow -ge $tripDeadline) {
            throw "Qwen startup failed. Read $tripErrorLog."
        }
        $tripHealth = curl.exe --noproxy '*' --silent --max-time 2 "$tripEndpoint/health"
        if ($tripHealth -match '"status":"ok"') { break }
        Start-Sleep -Seconds 1
    } while ($true)
    $tripRunnerArgs = @('--out', $tripOut, '--endpoint', "$tripEndpoint/v1", '--seconds', $Seconds)
    if ($Only) { $tripRunnerArgs += @('--only') + $Only }
    & ./.venv/Scripts/python.exe -B -X utf8 -m chinatravel.environment.sandbox run -- -m experiments.query_sets.run @tripRunnerArgs
    $tripExit = $LASTEXITCODE
} finally {
    $tripServer.Refresh()
    if (!$tripServer.HasExited) { Stop-Process -Id $tripServer.Id }
}
exit $tripExit
