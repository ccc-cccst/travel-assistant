param(
    [ValidateSet('LLMNeSy','ReAct','ReAct0','Act','LLM-modulo')][string]$Agent = 'LLMNeSy',
    [ValidateSet('smoke_local','pilot10_local','preview5_local','easy_local','medium_local','human_local')][string]$Split = 'smoke_local',
    [switch]$Oracle,
    [switch]$Evaluate,
    [switch]$DryRun
)
& (Join-Path $PSScriptRoot 'Start-Reproduction.ps1') -ModelId 'deepseek-v4-pro' -BaseUrl 'https://api.deepseek.com' -ModelVersion 'DeepSeek-V4-Pro official API; non-thinking' -Agent $Agent -Split $Split -Oracle:$Oracle -Evaluate:$Evaluate -DryRun:$DryRun
