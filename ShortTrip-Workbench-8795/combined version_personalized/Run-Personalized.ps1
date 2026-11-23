[CmdletBinding()]
param(
  [string]$InputFile,
  [switch]$Web,
  [int]$Port=8781,
  [ValidateSet('deepseek','qwen')][string]$Provider='deepseek',
  [ValidateRange(1,2147483647)][int]$Seconds=1800,
  [ValidateRange(1,2147483647)][int]$Candidates=10,
  [string]$Out
)
$ErrorActionPreference='Stop'
$tripVenv=Join-Path $PSScriptRoot '.venv'
if (!(Test-Path -LiteralPath (Join-Path $tripVenv 'Scripts/python.exe'))) {
  $tripVenv=Join-Path $PSScriptRoot '../ShortTrip/.venv'
}
$tripPython=Join-Path $tripVenv 'Scripts/python.exe'
if (!(Test-Path -LiteralPath $tripPython)) { throw 'Create .venv and install requirements.txt first (see README.md).' }
$initialDeepSeekKey = $env:DEEPSEEK_API_KEY
$localDeepSeekKey = Join-Path $PSScriptRoot '../.secrets/deepseek.dpapi'
Push-Location $PSScriptRoot
try {
  if (!$initialDeepSeekKey -and ($Web -or $Provider -eq 'deepseek') -and (Test-Path -LiteralPath $localDeepSeekKey -PathType Leaf)) {
    $sealed = ConvertTo-SecureString -String (Get-Content -LiteralPath $localDeepSeekKey -Raw).Trim()
    $env:DEEPSEEK_API_KEY = [Net.NetworkCredential]::new('', $sealed).Password
  }
  if($Web){
    $tripNativePython=(& $tripPython -c 'import sys;print(sys._base_executable)').Trim()
    $tripSite=Join-Path $tripVenv 'Lib/site-packages'
    & $tripNativePython -B -X utf8 -c "import site,sys,runpy;site.addsitedir(sys.argv.pop(1));runpy.run_module('chinatravel.personalization.web',run_name='__main__')" $tripSite --port $Port
  }
  else {
    if(!$InputFile){throw 'Specify -InputFile or -Web.'}
    $tripArgs=@('plan','--input',$InputFile,'--provider',$Provider,'--seconds',$Seconds,'--candidates',$Candidates)
    if($Out){$tripArgs+=@('--out',$Out)}
    & $tripPython -B -X utf8 personalize.py @tripArgs
  }
  $tripExit=$LASTEXITCODE
} finally {
  $env:DEEPSEEK_API_KEY = $initialDeepSeekKey
  Pop-Location
}
exit $tripExit
