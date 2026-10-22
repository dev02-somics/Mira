param(
    [string]$DeviceUrl = 'http://192.168.11.102:8088'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.tools\python\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    $projectPython = (Get-Command python -ErrorAction Stop).Source
}
& $projectPython (Join-Path $PSScriptRoot 'wifi_menu.py') --url $DeviceUrl
