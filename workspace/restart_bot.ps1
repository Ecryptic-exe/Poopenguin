param([switch]$StopOnly, [string]$Reason = 'Authorized local restart')
$ErrorActionPreference = 'Stop'
$projectDir = [IO.Path]::GetFullPath('D:\WorkSpace\Codex_WorkSpace\LB_Penguin')
$runtimeDir = Join-Path $projectDir 'data\runtime'
$recordPath = Join-Path $runtimeDir 'bot-process.json'
$botPython = Join-Path $projectDir '.venv\Scripts\python.exe'
$old = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$controllerId = [int]$old.process_id
$processes = @(Get-CimInstance Win32_Process)
$controller = $processes | Where-Object ProcessId -eq $controllerId
$stopped = @()
if ($controller) {
    if ($controller.ExecutablePath -ne $botPython -or $controller.CommandLine -notmatch 'launcher\.py\s+--env-file\s+\.env') {
        throw 'The saved controller identity differs from the authorized workspace bot.'
    }
    $descendants = @($controllerId)
    $frontier = @($controllerId)
    while ($frontier.Count -gt 0) {
        $next = @($processes | Where-Object { $frontier -contains $_.ParentProcessId } | ForEach-Object { [int]$_.ProcessId })
        $descendants += $next
        $frontier = $next
    }
    [array]::Reverse($descendants)
    foreach ($targetId in $descendants) {
        if (Get-Process -Id $targetId -ErrorAction SilentlyContinue) {
            Stop-Process -Id $targetId -Force
            $stopped += $targetId
        }
    }
}
if ($StopOnly) {
    [ordered]@{stopped_process_ids = $stopped; reason = $Reason} | ConvertTo-Json
    return
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdoutPath = Join-Path $runtimeDir "bot-$stamp.stdout.log"
$stderrPath = Join-Path $runtimeDir "bot-$stamp.stderr.log"
$process = Start-Process -FilePath $botPython -ArgumentList @('-X','utf8','-u','launcher.py','--env-file','.env') -WorkingDirectory $projectDir -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
$record = [ordered]@{
    process_id = $process.Id
    started_at = (Get-Date).ToString('o')
    working_directory = $projectDir
    stdout = $stdoutPath
    stderr = $stderrPath
    restart_reason = $Reason
    replaced_process_id = $controllerId
    stopped_process_ids = $stopped
}
$record | ConvertTo-Json | Set-Content -LiteralPath $recordPath -Encoding utf8
$record | ConvertTo-Json
