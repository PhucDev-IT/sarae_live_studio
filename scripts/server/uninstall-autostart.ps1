#Requires -Version 5.1
<#
.SYNOPSIS
Gỡ các task Sarae Server Watchdog và Sarae Server Backup khỏi Task Scheduler.

.DESCRIPTION
Mặc định chỉ dừng và gỡ task: voice server, gateway và cloudflared mà watchdog đã khởi động VẪN CHẠY tiếp
(khách không bị gián đoạn), chỉ là không còn ai tự bật lại khi chúng chết.
Thêm -StopServer để dừng luôn các tiến trình đó. Chỉ dừng đúng tiến trình watchdog đã ghi PID trong
%LOCALAPPDATA%\SaraeServer\state (kiểm tra cả thời điểm start), không bao giờ đụng tiến trình khác.
Voice server được dừng êm (file supervisor.stop, câu đang đọc dở trên GPU chạy xong trước).
Log, state và bản sao lưu được giữ nguyên.

.EXAMPLE
.\scripts\server\uninstall-autostart.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param([switch]$StopServer)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'SaraeServer.Common.ps1')
$dry = [bool]$WhatIfPreference
$cfg = Initialize-SaraeServer -LogName 'install' -DryRun $dry

$taskPath = '\Sarae\'
$found = $false
foreach ($name in @('Sarae Server Watchdog', 'Sarae Server Backup')) {
    $t = Get-ScheduledTask -TaskPath $taskPath -TaskName $name -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host ('Không có task {0}{1}.' -f $taskPath, $name); continue }
    $found = $true
    Write-Host ('Task {0}{1}: {2}' -f $taskPath, $name, $t.State)
    if ($t.State -eq 'Running' -and $PSCmdlet.ShouldProcess("$taskPath$name", 'Dừng task')) {
        Stop-ScheduledTask -TaskPath $taskPath -TaskName $name
    }
    if ($PSCmdlet.ShouldProcess("$taskPath$name", 'Gỡ task')) {
        Unregister-ScheduledTask -TaskPath $taskPath -TaskName $name -Confirm:$false
        Write-SaraeLog ('Đã gỡ task {0}{1}.' -f $taskPath, $name)
    }
}

$components = @('cloudflared', 'gateway', 'voice')   # dừng ngược thứ tự khởi động
Write-Host ''
foreach ($c in $components) {
    $tr = Get-SaraeTracked $c
    if (-not $tr) { Write-Host ('{0}: không có tiến trình nào do watchdog khởi động.' -f $c); continue }
    if (-not $StopServer) {
        Write-Host ('{0}: PID {1} vẫn chạy (thêm -StopServer để dừng).' -f $c, $tr.Pid)
        continue
    }
    if (-not $PSCmdlet.ShouldProcess("$c (PID $($tr.Pid))", 'Dừng tiến trình do watchdog khởi động')) { continue }
    if ($c -eq 'voice') {
        $stopFile = Join-Path $cfg.VoiceDataDir 'supervisor.stop'
        [IO.File]::WriteAllText($stopFile, 'stop')
        Write-Host 'voice: đã yêu cầu supervisor dừng êm, chờ tối đa 90 giây...'
        if (-not $tr.Process.WaitForExit(90000)) { Stop-SaraeProcessTree -ProcessId $tr.Pid }
    } else {
        Stop-SaraeProcessTree -ProcessId $tr.Pid
    }
    Clear-SaraeTracked $c
    Write-SaraeLog ('Đã dừng {0} (PID {1}).' -f $c, $tr.Pid)
}
if ($dry) { Write-Host 'WhatIf: không gỡ hay dừng gì.' -ForegroundColor Cyan }
elseif (-not $found) { Write-Host 'Không có task nào để gỡ.' }
