[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 8795,
    [ValidateRange(1, 65535)][int]$PlannerPort = 8781,
    [string]$AmapEnvFile,
    [switch]$Open
)

$ErrorActionPreference = 'Stop'
if ($Port -eq $PlannerPort) { throw '工作台和规划器需要两个不同的端口。' }
$workspace = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$planner = Join-Path $workspace 'combined version_personalized'
$runner = Join-Path $planner 'Run-Personalized.ps1'
$server = Join-Path $PSScriptRoot 'webui/backend/server.py'
$archive = Join-Path $planner 'result/experiments/P042_search_budget/paired_release/shanghai_hangzhou/new'
$snapshot = Join-Path $workspace 'ShortTrip/artifacts/data/6de12f4/install.json'
$plannerPython = @(
    (Join-Path $planner '.venv/Scripts/python.exe')
    (Join-Path $workspace 'ShortTrip/.venv/Scripts/python.exe')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
foreach ($file in @($runner, $server, $snapshot, (Join-Path $archive 'PLAN_VIEW.json'))) {
    if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "找不到所需文件 $file" }
}
if (!$plannerPython) { throw '找不到规划器虚拟环境，请按 combined version_personalized/README.md 配置依赖。' }

function Test-Listener([int]$Number) {
    $socket = [Net.Sockets.TcpClient]::new()
    try { $socket.Connect('127.0.0.1', $Number); return $true }
    catch [Net.Sockets.SocketException] { return $false }
    finally { $socket.Dispose() }
}

function Read-LocalJson([string]$Address) {
    try { return Invoke-RestMethod -Uri $Address -TimeoutSec 3 }
    catch { return $null }
}

function Wait-LocalJson([string]$Address, [Diagnostics.Process]$Process, [string]$Log) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt 55) {
        if ($Process.HasExited) { throw "服务启动失败。日志 $Log" }
        $body = Read-LocalJson $Address
        if ($body) { return $body }
        Start-Sleep -Milliseconds 450
    }
    throw "等待服务超过 55 秒。日志 $Log"
}

$plannerBase = "http://127.0.0.1:$PlannerPort"
$workbenchBase = "http://127.0.0.1:$Port"
$plannerStatus = Read-LocalJson "$plannerBase/options"
$workbenchStatus = Read-LocalJson "$workbenchBase/api/workbench-status"
if ((Test-Listener $PlannerPort) -and (!$plannerStatus -or !$plannerStatus.cities -or
        !$plannerStatus.PSObject.Properties['model_ready'])) {
    throw "端口 $PlannerPort 已被其他服务占用，可用 -PlannerPort 指定规划端口。"
}
if ((Test-Listener $Port) -and ($workbenchStatus.kind -ne 'shorttrip-workbench' -or
        $workbenchStatus.planner_port -ne $PlannerPort -or !$workbenchStatus.archive)) {
    throw "端口 $Port 已被另一套服务占用，可用 -Port 指定工作台端口。"
}

$started = @()
try {
    if (!$plannerStatus -or !$workbenchStatus) {
        $run = Join-Path $planner ('result/workbench_runtime/' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $run | Out-Null
    }
    if (!$plannerStatus) {
        $plannerLog = Join-Path $run 'planner.stderr.log'
        $arguments = @{
            FilePath = (Get-Process -Id $PID).Path
            ArgumentList = @('-NoProfile','-File',('"' + $runner + '"'),'-Web','-Port',"$PlannerPort")
            WindowStyle = 'Hidden'
            PassThru = $true
            RedirectStandardOutput = Join-Path $run 'planner.stdout.log'
            RedirectStandardError = $plannerLog
        }
        $process = Start-Process @arguments
        $started += $process
        $plannerStatus = Wait-LocalJson "$plannerBase/options" $process $plannerLog
    }
    if (!$workbenchStatus) {
        $webLog = Join-Path $run 'workbench.stderr.log'
        $arguments = @{
            FilePath = $plannerPython
            ArgumentList = @('-B','-X','utf8',('"' + $server + '"'),'--demo-routes','--port',"$Port",
                '--personalized-port',"$PlannerPort",'--personalized-archive',('"' + $archive + '"'))
            WindowStyle = 'Hidden'
            PassThru = $true
            RedirectStandardOutput = Join-Path $run 'workbench.stdout.log'
            RedirectStandardError = $webLog
        }
        $priorMapKey = $env:AMAP_KEY
        try {
            if ($AmapEnvFile) {
                $line = Get-Content -LiteralPath $AmapEnvFile -Encoding utf8 |
                    Where-Object { $_ -match '^\s*AMAP_KEY\s*=' } | Select-Object -First 1
                if (!$line) { throw '所选配置文件缺少 AMAP_KEY。' }
                $env:AMAP_KEY = ($line -split '=', 2)[1].Trim().Trim([char[]]@(34,39))
                if (!$env:AMAP_KEY) { throw '所选配置的 AMAP_KEY 为空。' }
            }
            $process = Start-Process @arguments
        } finally {
            $env:AMAP_KEY = $priorMapKey
        }
        $started += $process
        $workbenchStatus = Wait-LocalJson "$workbenchBase/api/workbench-status" $process $webLog
    }
    $linked = Read-LocalJson "$workbenchBase/api/personalized/options"
    if (!$linked -or !$linked.cities) { throw '工作台已启动，但暂时无法读到规划器的城市选项。' }
} catch {
    foreach ($process in $started) {
        if (!$process.HasExited) { Stop-Process -InputObject $process }
    }
    throw
}

Write-Output "新建规划 $workbenchBase/?new=1"
Write-Output "杭州归档 $workbenchBase/?trip=personalized&day=1"
if (!$linked.model_ready) { Write-Output '当前可用表单规划。原话提取需要在规划器启动环境设置 DEEPSEEK_API_KEY。' }
if ($run) { Write-Output "启动日志 $run" }
if ($Open) { Start-Process "$workbenchBase/?new=1" }
