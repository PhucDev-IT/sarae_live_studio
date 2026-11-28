#Requires -Version 5.1
<#
.SYNOPSIS
Đăng ký Task Scheduler để server PC nhà tự chạy: watchdog (voice server -> gateway -> cloudflared) và sao lưu DB hằng ngày.

.DESCRIPTION
Tạo 2 task trong thư mục \Sarae\ của Task Scheduler (không cần cài phần mềm dịch vụ nào):

  Sarae Server Watchdog  chạy scripts\server\watchdog.ps1 (vòng lặp vô hạn, không cửa sổ). Lặp lại trigger mỗi 5 phút
                         để nếu watchdog bị tắt thì Task Scheduler bật lại (MultipleInstances = IgnoreNew nên không
                         bao giờ có 2 bản cùng chạy). Priority 4 (Normal): mặc định của Task Scheduler là 7 (thấp hơn
                         bình thường), các tiến trình con sẽ thừa hưởng mức thấp đó nếu không đổi.
  Sarae Server Backup    chạy scripts\server\backup-db.ps1 mỗi ngày lúc -BackupAt (mặc định 03:30). Nếu máy tắt đúng giờ đó
                         thì chạy bù ngay khi bật lại (StartWhenAvailable).

Chọn kiểu khởi chạy bằng -Trigger:

  Startup (khuyến nghị cho server)  chạy khi Windows khởi động, KHÔNG cần ai đăng nhập. Sau khi Windows Update tự khởi
        động lại lúc 3 giờ sáng hay sau mất điện, server tự lên. Dùng kiểu đăng nhập S4U: chạy dưới chính tài khoản
        của bạn, KHÔNG lưu mật khẩu. Cần mở PowerShell bằng "Run as administrator" để đăng ký. Tiến trình chạy ở
        session 0 (không có cửa sổ). Nếu voice server không thấy GPU ở chế độ này, dùng -Trigger Logon.
  Logon  chạy khi bạn đăng nhập Windows. Không cần quyền admin, không lưu mật khẩu. Nhược điểm: sau khi máy tự
        khởi động lại, server chỉ chạy khi có người đăng nhập (trừ khi bật tự đăng nhập Windows).

Script chỉ đăng ký task. Không tắt hay khởi động lại tiến trình nào; muốn chạy ngay thì thêm -StartNow.
Nên chạy từ thư mục chính của repo (nơi có backend\.venv và backend\data), không chạy từ worktree.

.EXAMPLE
.\scripts\server\install-autostart.ps1 -WhatIf
In ra kế hoạch, không đăng ký gì.

.EXAMPLE
.\scripts\server\install-autostart.ps1 -Trigger Startup -StartNow
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet('Startup', 'Logon')]
    [string]$Trigger = 'Startup',
    [ValidatePattern('^\d{2}:\d{2}$')]
    [string]$BackupAt = '03:30',
    [switch]$NoBackup,
    [switch]$StartNow
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'SaraeServer.Common.ps1')
$dry = [bool]$WhatIfPreference
$cfg = Initialize-SaraeServer -LogName 'install' -DryRun $dry

$taskPath = '\Sarae\'
$watchdogTask = 'Sarae Server Watchdog'
$backupTask = 'Sarae Server Backup'
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$watchdogScript = Join-Path $PSScriptRoot 'watchdog.ps1'
$backupScript = Join-Path $PSScriptRoot 'backup-db.ps1'
$user = '{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME

function Write-Check([string]$Label, [bool]$Ok, [string]$Detail) {
    $mark = '[OK]  '
    $color = 'Green'
    if (-not $Ok) { $mark = '[THIẾU]'; $color = 'Yellow' }
    Write-Host ('{0} {1}: {2}' -f $mark, $Label, $Detail) -ForegroundColor $color
}

Write-Host ''
Write-Host '== Kiểm tra trước khi cài ==' -ForegroundColor Cyan
Write-Check 'Repo' $true $cfg.RepoRoot
if ($cfg.RepoRoot -match '\\\.claude\\worktrees\\') {
    Write-Host '        Đang chạy từ worktree. Nên cài từ thư mục chính (F:\Coder\sarae_live_studio) sau khi merge.' -ForegroundColor Yellow
}
Write-Check 'Python backend' (Test-Path -LiteralPath $cfg.BackendPython) $cfg.BackendPython
Write-Check 'Gateway (app\gateway_main.py)' (Test-Path -LiteralPath (Join-Path $cfg.BackendDir 'app\gateway_main.py')) (Join-Path $cfg.BackendDir 'app\gateway_main.py')
Write-Check 'Voice server pythonw' (Test-Path -LiteralPath $cfg.VoicePythonW) $cfg.VoicePythonW
Write-Check 'Voice server .env' (Test-Path -LiteralPath (Join-Path $cfg.VoiceDir '.env')) (Join-Path $cfg.VoiceDir '.env')
Write-Check 'cloudflared.exe' (Test-Path -LiteralPath $cfg.CloudflaredExe) $cfg.CloudflaredExe
Write-Check 'Tunnel config.yml' (Test-Path -LiteralPath $cfg.CloudflaredConfig) $cfg.CloudflaredConfig
Write-Check 'Telegram' ([bool]($cfg.TelegramToken -and $cfg.TelegramChatId)) $(if ($cfg.TelegramToken -and $cfg.TelegramChatId) { 'đã có token và chat id' } else { 'chưa cấu hình, cảnh báo chỉ ghi log' })
Write-Host ('        Log/state/backup: {0}' -f $cfg.HomeDir)

$voiceTask = Get-ScheduledTask -TaskName 'SaraeVoiceServer' -ErrorAction SilentlyContinue
if ($voiceTask) {
    Write-Host ('[CHÚ Ý] Đã có task SaraeVoiceServer (scripts\service.ps1 của voice server, trạng thái {0}).' -f $voiceTask.State) -ForegroundColor Yellow
    Write-Host '        Watchdog cũng tự chạy voice server. Hai bên không chạy trùng được (voice server có khóa instance),' -ForegroundColor Yellow
    Write-Host '        nhưng nên chỉ giữ một: gỡ task kia bằng  F:\Coder\sarae-voice-server\scripts\service.ps1 uninstall' -ForegroundColor Yellow
}
$cfService = Get-Service -Name 'cloudflared' -ErrorAction SilentlyContinue
if ($cfService) {
    Write-Host ('[CHÚ Ý] Đã có Windows service "cloudflared" ({0}). Watchdog sẽ coi tunnel là do service đó quản lý và' -f $cfService.Status) -ForegroundColor Yellow
    Write-Host '        không tự chạy cloudflared (khi đó không cần config.yml). Muốn watchdog quản lý thì gỡ service:' -ForegroundColor Yellow
    Write-Host '        cloudflared service uninstall (admin). Xem docs\HYBRID_SERVER_OPS.md mục 2.' -ForegroundColor Yellow
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# ---- Dựng task (chỉ là object, chưa đăng ký) ----
$taskSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -Priority 4
$backupSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -Priority 6

if ($Trigger -eq 'Startup') {
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
    $watchdogTrigger = New-ScheduledTaskTrigger -AtStartup
    $watchdogTrigger.Delay = 'PT1M'   # chờ mạng và driver GPU lên
} else {
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $watchdogTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $watchdogTrigger.Delay = 'PT30S'
}
# Lặp lại mỗi 5 phút, vô thời hạn: nếu watchdog chết, lần lặp kế tiếp bật lại (IgnoreNew khi nó còn chạy).
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$watchdogTrigger.Repetition = $repeat.Repetition

$watchdogArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $watchdogScript
$watchdogAction = New-ScheduledTaskAction -Execute $ps -Argument $watchdogArgs -WorkingDirectory $PSScriptRoot
$watchdogDef = New-ScheduledTask -Action $watchdogAction -Trigger $watchdogTrigger -Settings $taskSettings -Principal $principal `
    -Description 'Sarae server PC nhà: giữ voice server, gateway và cloudflared luôn chạy (scripts\server\watchdog.ps1).'

$backupArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $backupScript
$backupAction = New-ScheduledTaskAction -Execute $ps -Argument $backupArgs -WorkingDirectory $PSScriptRoot
$backupTrigger = New-ScheduledTaskTrigger -Daily -At $BackupAt
$backupDef = New-ScheduledTask -Action $backupAction -Trigger $backupTrigger -Settings $backupSettings -Principal $principal `
    -Description 'Sarae server PC nhà: sao lưu identity.sqlite3 và studio_gateway.sqlite3, giữ 14 bản (scripts\server\backup-db.ps1).'

Write-Host ''
Write-Host '== Kế hoạch ==' -ForegroundColor Cyan
Write-Host ('Task {0}{1}' -f $taskPath, $watchdogTask)
if ($Trigger -eq 'Startup') {
    Write-Host ('  Khi máy khởi động (trễ 1 phút), không cần đăng nhập. Chạy dưới {0}, kiểu S4U, không lưu mật khẩu.' -f $user)
} else {
    Write-Host ('  Khi {0} đăng nhập (trễ 30 giây). Không lưu mật khẩu.' -f $user)
}
Write-Host '  Lặp lại mỗi 5 phút để bật lại nếu watchdog bị tắt; không bao giờ chạy 2 bản. Không giới hạn thời gian. Priority Normal.'
Write-Host ('  Lệnh: {0} {1}' -f $ps, $watchdogArgs)
if (-not $NoBackup) {
    Write-Host ('Task {0}{1}' -f $taskPath, $backupTask)
    Write-Host ('  Mỗi ngày lúc {0}; nếu lỡ giờ thì chạy bù khi máy bật. Giới hạn 30 phút.' -f $BackupAt)
    Write-Host ('  Lệnh: {0} {1}' -f $ps, $backupArgs)
}
if ($StartNow) { Write-Host ('Sau đó chạy ngay task {0}.' -f $watchdogTask) }

if ($Trigger -eq 'Startup' -and -not $isAdmin) {
    if ($dry) {
        Write-Host ''
        Write-Host 'Lưu ý: -Trigger Startup cần PowerShell "Run as administrator" khi cài thật.' -ForegroundColor Yellow
    } else {
        throw 'Đăng ký task chạy khi khởi động (S4U) cần quyền admin. Mở PowerShell bằng "Run as administrator", hoặc dùng -Trigger Logon.'
    }
}

Write-Host ''
if ($PSCmdlet.ShouldProcess("$taskPath$watchdogTask", 'Đăng ký scheduled task')) {
    Register-ScheduledTask -TaskPath $taskPath -TaskName $watchdogTask -InputObject $watchdogDef -Force | Out-Null
    Write-SaraeLog ("Đã đăng ký task {0}{1} (trigger {2})." -f $taskPath, $watchdogTask, $Trigger)
}
if (-not $NoBackup -and $PSCmdlet.ShouldProcess("$taskPath$backupTask", 'Đăng ký scheduled task')) {
    Register-ScheduledTask -TaskPath $taskPath -TaskName $backupTask -InputObject $backupDef -Force | Out-Null
    Write-SaraeLog ("Đã đăng ký task {0}{1} (mỗi ngày {2})." -f $taskPath, $backupTask, $BackupAt)
}
if ($StartNow -and $PSCmdlet.ShouldProcess("$taskPath$watchdogTask", 'Chạy task ngay')) {
    Start-ScheduledTask -TaskPath $taskPath -TaskName $watchdogTask
    Write-SaraeLog 'Đã chạy watchdog. Xem log: Get-Content "$env:LOCALAPPDATA\SaraeServer\logs\watchdog.log" -Tail 30 -Wait'
}
if ($dry) {
    Write-Host 'WhatIf: không đăng ký hay chạy gì.' -ForegroundColor Cyan
} elseif (-not $StartNow) {
    Write-Host ('Chưa chạy. Chạy ngay bằng: Start-ScheduledTask -TaskPath "{0}" -TaskName "{1}"' -f $taskPath, $watchdogTask)
}
