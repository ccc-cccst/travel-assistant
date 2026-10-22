param(
    [ValidateSet('LLMNeSy','ReAct','ReAct0','Act','LLM-modulo')][string]$Agent = 'LLMNeSy',
    [ValidateSet('smoke_local','pilot10_local','easy_local','medium_local','human_local')][string]$Split = 'smoke_local',
    [switch]$Oracle,
    [switch]$Evaluate,
    [switch]$DryRun
)
$travelPreviousKey = $env:CHINATRAVEL_OPENAI_API_KEY
try {
    if (-not $env:CHINATRAVEL_OPENAI_API_KEY -and $env:ARK_API_KEY) {
        $env:CHINATRAVEL_OPENAI_API_KEY = $env:ARK_API_KEY
    }
    & (Join-Path $PSScriptRoot 'Start-Reproduction.ps1') -ModelId 'doubao-seed-2-0-mini-260428' -BaseUrl 'https://ark.cn-beijing.volces.com/api/v3' -ModelVersion 'Doubao-Seed-2.0-Mini-260428 official Ark API; non-thinking' -Agent $Agent -Split $Split -Oracle:$Oracle -Evaluate:$Evaluate -DryRun:$DryRun
}
finally {
    $env:CHINATRAVEL_OPENAI_API_KEY = $travelPreviousKey
}
