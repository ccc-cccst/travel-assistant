param(
    [ValidateSet('LLMNeSy','ReAct','ReAct0','Act','LLM-modulo')][string]$Agent = 'LLMNeSy',
    [ValidateSet('smoke_local','pilot10_local','preview5_local','patterns9_local','patterns14_local','easy_local','medium_local','human_local')][string]$Split = 'preview5_local',
    [switch]$Oracle,
    [switch]$Evaluate,
    [switch]$DryRun
)
& (Join-Path $PSScriptRoot 'Start-Reproduction.ps1') -ModelId 'deepseek-flash' -BaseUrl 'https://api.deepseek.com' -ModelVersion 'DeepSeek-V4.1-Flash official API; non-thinking; checked 2026-10-17' -Agent $Agent -Split $Split -Oracle:$Oracle -Evaluate:$Evaluate -DryRun:$DryRun
