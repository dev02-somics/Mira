param(
    [ValidateSet('test','build','demo','validate')]
    [string]$Command = 'test'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.tools\python\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    $projectPython = (Get-Command python -ErrorAction Stop).Source
}
& $projectPython (Join-Path $PSScriptRoot 'dev.py') $Command
exit $LASTEXITCODE
