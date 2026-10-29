$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$record = Join-Path $projectRoot 'work\fullstack\processes.json'
if (-not (Test-Path -LiteralPath $record)) { Write-Output 'No workbench process record.'; exit 0 }
$processes = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
if ($processes.root -ne $projectRoot) { throw 'Process record belongs to a different checkout.' }
$entries = @($processes.components)
if (-not $processes.components) {
    $entries = @(@{pid=$processes.api;started=$null}, @{pid=$processes.web;started=$null})
}
[array]::Reverse($entries)
foreach ($entry in $entries) {
    $processId = $entry.pid
    if (-not $processId) { continue }
    $process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$processId)
    if (-not $process) { continue }
    if (-not $process.CommandLine -or -not $process.CommandLine.Contains($projectRoot)) {
        throw 'Process identity changed; refusing to stop it.'
    }
    if ($entry.started) {
        $expected = [datetime]::Parse($entry.started).ToUniversalTime()
        if ([math]::Abs(($process.CreationDate.ToUniversalTime()-$expected).TotalSeconds) -gt 2) {
            throw 'Process start time changed; refusing to stop it.'
        }
    }
    & taskkill.exe /PID $processId /T /F | Out-Null
}
Remove-Item -LiteralPath $record
Write-Output 'Workbench services stopped. Shared database was left running.'
