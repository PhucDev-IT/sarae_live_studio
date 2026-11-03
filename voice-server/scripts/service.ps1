#Requires -Version 5.1
<#
Sarae Voice Server as a background service (Windows Task Scheduler + the Python supervisor).

  .\scripts\service.ps1 install [-AtStartup]   register the task (default: at your logon, no password stored)
  .\scripts\service.ps1 start | stop | restart | status | logs
  .\scripts\service.ps1 uninstall

The task runs `pythonw -m sarae_voice.supervisor` in voice-server\ (no window). The supervisor starts the server,
restarts it after a crash or a CUDA failure (backoff, gives up after 10 quick failures in 10 minutes) and stops it
gracefully through data\supervisor.stop. Only the supervisor's own child is ever stopped: this script never kills
other processes, GPU or not.
-AtStartup runs it at boot without a logon; Windows then asks for your account password to store with the task.
#>
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("install", "uninstall", "start", "stop", "restart", "status", "logs")]
    [string]$Action,
    [switch]$AtStartup
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$task = "SaraeVoiceServer"
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"

function Get-EnvValue([string]$name, [string]$default) {
    $envFile = Join-Path $root ".env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$name\s*=" } | Select-Object -Last 1
        if ($line) { $v = ($line -split "=", 2)[1].Trim(); if ($v) { return $v } }
    }
    return $default
}

function Get-DataDir {
    $d = Get-EnvValue "SARAE_VOICE_DATA_DIR" "data"
    if ([System.IO.Path]::IsPathRooted($d)) { return $d }
    return (Join-Path $root $d)
}

function Show-Status {
    $t = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host "Task $task is not installed."; return }
    $info = Get-ScheduledTaskInfo -TaskName $task
    Write-Host ("Task: {0}  last run: {1}  last result: {2}" -f $t.State, $info.LastRunTime, $info.LastTaskResult)
    $port = Get-EnvValue "SARAE_VOICE_PORT" "8780"
    $host_ = Get-EnvValue "SARAE_VOICE_HOST" "127.0.0.1"
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://${host_}:${port}/health/ready"
        Write-Host "Server: $($r.Content)"
    } catch {
        $body = $null
        if ($_.Exception.Response) { $body = (New-Object IO.StreamReader($_.Exception.Response.GetResponseStream())).ReadToEnd() }
        if ($body) { Write-Host "Server: $body" } else { Write-Host "Server: not answering on ${host_}:${port}" }
    }
}

function Stop-Service_ {
    $t = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    if (-not $t -or $t.State -ne "Running") { Write-Host "Not running."; return }
    $dataDir = Get-DataDir
    New-Item -ItemType Directory -Force $dataDir | Out-Null
    Set-Content -Path (Join-Path $dataDir "supervisor.stop") -Value "stop"
    Write-Host "Asked the supervisor to stop (the running GPU call finishes first)..."
    $grace = [double](Get-EnvValue "SARAE_VOICE_SHUTDOWN_GRACE_S" "30") + 45
    $deadline = (Get-Date).AddSeconds($grace)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if ((Get-ScheduledTask -TaskName $task).State -ne "Running") { Write-Host "Stopped."; return }
    }
    Write-Warning "Supervisor did not stop within $grace s; ending the scheduled task (its own processes only)."
    Stop-ScheduledTask -TaskName $task
}

switch ($Action) {
    "install" {
        if (-not (Test-Path $pyw)) { throw "Run scripts\install.ps1 first ($pyw missing)" }
        if (-not (Test-Path (Join-Path $root ".env"))) { throw "Create voice-server\.env first (scripts\run.ps1 creates one)" }
        $action = New-ScheduledTaskAction -Execute $pyw -Argument "-m sarae_voice.supervisor" -WorkingDirectory $root
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
            -StartWhenAvailable
        $user = "$env:USERDOMAIN\$env:USERNAME"
        if ($AtStartup) {
            $trigger = New-ScheduledTaskTrigger -AtStartup
            $cred = Get-Credential -UserName $user -Message "Password stored with the scheduled task (run at boot)"
            Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings `
                -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
        } else {
            $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
            $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
            Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings `
                -Principal $principal -Force | Out-Null
        }
        Write-Host "Installed task $task. Start it now with: .\scripts\service.ps1 start"
    }
    "uninstall" {
        Stop-Service_
        Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "Removed task $task (data\ and the model cache are kept)."
    }
    "start" {
        Start-ScheduledTask -TaskName $task
        Write-Host "Started. The model needs ~20 s (longer right after boot); check with: .\scripts\service.ps1 status"
    }
    "stop" { Stop-Service_ }
    "restart" { Stop-Service_; Start-ScheduledTask -TaskName $task; Write-Host "Restarted." }
    "status" { Show-Status }
    "logs" {
        $logs = Join-Path (Get-DataDir) "logs"
        Get-Content (Join-Path $logs "supervisor.log") -Tail 20 -ErrorAction SilentlyContinue
        Get-Content (Join-Path $logs "server.log") -Tail 40 -ErrorAction SilentlyContinue
    }
}
