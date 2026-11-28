#Requires -Version 5.1
<#
.SYNOPSIS
Giữ 3 tiến trình của server PC nhà luôn chạy: voice server (GPU) -> gateway -> cloudflared.

.DESCRIPTION
Mỗi -IntervalSec giây (mặc định 30) kiểm tra health theo đúng thứ tự phụ thuộc:

  1. voice        http://127.0.0.1:8780/health/ready   chạy: <voice>\.venv\Scripts\pythonw.exe -m sarae_voice.supervisor
  2. gateway      http://127.0.0.1:8000/api/health     chạy: backend\.venv\Scripts\python.exe -m uvicorn app.gateway_main:app
  3. cloudflared  http://127.0.0.1:20241/ready         chạy: cloudflared tunnel --config <config.yml> --metrics ... run

Tiến trình chết thì khởi động lại; nếu nhiều cái cùng chết thì bật lần lượt, chờ cái trước sẵn sàng (có giới hạn thời gian:
gateway vẫn được bật dù voice chưa lên, vì kích hoạt máy và license không cần GPU). Tiến trình còn sống nhưng health lỗi
quá lâu (sau thời gian khởi động) thì dừng đúng cây tiến trình đó rồi bật lại; voice được dừng êm qua supervisor.stop.
Quá 5 lần khởi động lại trong 15 phút thì tạm ngưng 15 phút cho thành phần đó và cảnh báo.

Watchdog chỉ dừng tiến trình do chính nó khởi động (PID + thời điểm start ghi trong %LOCALAPPDATA%\SaraeServer\state).
Nếu cổng đang bị tiến trình khác giữ (vd. bạn tự chạy start-voice-server.bat), nó chỉ cảnh báo, không tắt.
Nếu có Windows service "cloudflared", tunnel được coi là do service quản lý: watchdog chỉ kiểm tra và cảnh báo.

Tạo file %LOCALAPPDATA%\SaraeServer\maintenance để tạm dừng mọi việc tự khởi động lại (bảo trì, khôi phục DB);
xóa file đó để watchdog hoạt động lại.

Log: %LOCALAPPDATA%\SaraeServer\logs\watchdog.log (xoay vòng 5 MB x 5 file), gateway.out/err.log, cloudflared.log.
Trạng thái mới nhất: %LOCALAPPDATA%\SaraeServer\state\watchdog.status.json.
Cảnh báo Telegram khi đặt SARAE_TELEGRAM_BOT_TOKEN và SARAE_TELEGRAM_CHAT_ID (biến môi trường hoặc server.env).
Token không bao giờ được ghi ra log hay màn hình.

.PARAMETER Once
Chỉ chạy một vòng kiểm tra rồi thoát.

.EXAMPLE
.\scripts\server\watchdog.ps1 -WhatIf
Kiểm tra health một lần và in ra việc sẽ làm, không khởi động, không dừng, không gửi Telegram, không ghi file.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateRange(5, 3600)][int]$IntervalSec = 30,
    [switch]$Once,
    [ValidateRange(0, 900)][int]$VoiceWaitSec = 120,
    [ValidateRange(0, 600)][int]$GatewayWaitSec = 60,
    [ValidateRange(0, 86400)][int]$PublicCheckEverySec = 300
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'SaraeServer.Common.ps1')
$dry = [bool]$WhatIfPreference
$cfg = Initialize-SaraeServer -LogName 'watchdog' -DryRun $dry
$script:Cmdlet = $PSCmdlet
$script:FirstPass = $true

$components = @(
    @{ Name = 'voice'; Title = 'Voice server'; Url = $cfg.VoiceHealthUrl; Port = $cfg.VoicePort; GraceSec = 240; HungSec = 180; WaitSec = $VoiceWaitSec },
    @{ Name = 'gateway'; Title = 'Gateway'; Url = $cfg.GatewayHealthUrl; Port = $cfg.GatewayPort; GraceSec = 45; HungSec = 60; WaitSec = $GatewayWaitSec },
    @{ Name = 'cloudflared'; Title = 'Cloudflare Tunnel'; Url = $cfg.CloudflaredReadyUrl; Port = $cfg.CloudflaredPort; GraceSec = 60; HungSec = 120; WaitSec = 0 }
)
$state = @{}
foreach ($c in $components) {
    $state[$c.Name] = @{ Down = $false; Alerted = $false; Reason = ''; DownSince = $null; FailingSince = $null
        Restarts = (New-Object System.Collections.ArrayList); PausedUntil = $null; Ok = $false; Detail = ''; Pid = 0 }
}
$public = @{ Last = [DateTime]::MinValue; Fails = 0; Alerted = $false; Ok = $null }
$script:MaintenanceLogged = [DateTime]::MinValue

function Format-Duration([TimeSpan]$t) {
    if ($t.TotalMinutes -lt 1) { return ('{0:n0} giây' -f $t.TotalSeconds) }
    if ($t.TotalHours -lt 1) { return ('{0:n0} phút' -f $t.TotalMinutes) }
    return ('{0:n1} giờ' -f $t.TotalHours)
}

function Set-ComponentUp($c) {
    $st = $state[$c.Name]
    if ($st.Down) {
        $msg = '{0}: đã hoạt động lại' -f $c.Title
        if ($st.DownSince) { $msg += ' sau ' + (Format-Duration ((Get-Date) - $st.DownSince)) }
        Write-SaraeLog $msg
        if ($st.Alerted) { Send-SaraeAlert ('✅ ' + $msg) }
    }
    $st.Down = $false; $st.Alerted = $false; $st.Reason = ''; $st.DownSince = $null; $st.FailingSince = $null
}

function Set-ComponentDown($c, [string]$Reason, [string]$Key = '') {
    # Ghi log mỗi lần; chỉ gửi Telegram khi trạng thái hoặc lý do đổi (không spam mỗi 30 giây).
    $st = $state[$c.Name]
    if (-not $Key) { $Key = $Reason }
    $text = '{0}: {1}' -f $c.Title, $Reason
    Write-SaraeLog -Level WARN $text
    if (-not $st.Down) { $st.DownSince = Get-Date }
    $st.Down = $true
    if ($st.Reason -ne $Key) {
        $st.Reason = $Key
        if (-not $script:FirstPass) { Send-SaraeAlert ('⚠️ ' + $text); $st.Alerted = $true }
    }
}

function Get-ComponentPrereqError($c) {
    switch ($c.Name) {
        'voice' {
            if (-not (Test-Path -LiteralPath $cfg.VoicePythonW)) { return "thiếu $($cfg.VoicePythonW) (chạy scripts\install.ps1 của voice server)" }
            if (-not (Test-Path -LiteralPath (Join-Path $cfg.VoiceDir '.env'))) { return "thiếu $($cfg.VoiceDir)\.env (chạy scripts\run.ps1 của voice server một lần để tạo)" }
        }
        'gateway' {
            if (-not (Test-Path -LiteralPath $cfg.BackendPython)) { return "thiếu $($cfg.BackendPython)" }
            if (-not (Test-Path -LiteralPath (Join-Path $cfg.BackendDir 'app\gateway_main.py'))) { return "thiếu backend\app\gateway_main.py (chưa merge nhánh gateway?)" }
        }
        'cloudflared' {
            if (-not (Test-Path -LiteralPath $cfg.CloudflaredExe)) { return "không thấy cloudflared.exe ($($cfg.CloudflaredExe))" }
            if (-not (Test-Path -LiteralPath $cfg.CloudflaredConfig)) { return "thiếu file cấu hình tunnel $($cfg.CloudflaredConfig)" }
        }
    }
    return ''
}

function Get-ComponentCommand($c) {
    $q = { param($s) '"{0}"' -f $s }
    switch ($c.Name) {
        'voice' {
            return @{ File = $cfg.VoicePythonW; Args = @('-m', 'sarae_voice.supervisor'); Dir = $cfg.VoiceDir }
        }
        'gateway' {
            $a = @('-m', 'uvicorn', 'app.gateway_main:app', '--host', '127.0.0.1', '--port', [string]$cfg.GatewayPort)
            if (-not $cfg.GatewayAccessLog) { $a += '--no-access-log' }
            return @{ File = $cfg.BackendPython; Args = $a; Dir = $cfg.BackendDir
                Out = (Join-Path $cfg.LogDir 'gateway.out.log'); Err = (Join-Path $cfg.LogDir 'gateway.err.log') }
        }
        'cloudflared' {
            $log = Join-Path $cfg.LogDir 'cloudflared.log'
            $a = @('tunnel', '--config', (& $q $cfg.CloudflaredConfig), '--no-autoupdate', '--metrics', $cfg.CloudflaredMetrics,
                '--loglevel', 'info', '--logfile', (& $q $log), 'run')
            if ($cfg.CloudflaredTunnel) { $a += $cfg.CloudflaredTunnel }
            return @{ File = $cfg.CloudflaredExe; Args = $a; Dir = (Split-Path -Parent $cfg.CloudflaredConfig); Log = $log }
        }
    }
}

function Start-Component($c) {
    $cmd = Get-ComponentCommand $c
    $line = '{0} {1}' -f $cmd.File, ($cmd.Args -join ' ')
    if (-not $script:Cmdlet.ShouldProcess($c.Title, "Khởi động: $line")) { return $null }
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUNBUFFERED = '1'
    $sp = @{ FilePath = $cmd.File; ArgumentList = $cmd.Args; WorkingDirectory = $cmd.Dir; WindowStyle = 'Hidden'; PassThru = $true }
    if ($cmd.ContainsKey('Out')) {
        # Start-Process ghi đè file redirect, nên luôn giữ log lần chạy trước thành .1, .2 ...
        Invoke-SaraeLogRotation -Path $cmd.Out -MaxBytes 0 -Keep 5
        Invoke-SaraeLogRotation -Path $cmd.Err -MaxBytes 0 -Keep 5
        $sp.RedirectStandardOutput = $cmd.Out
        $sp.RedirectStandardError = $cmd.Err
    }
    if ($cmd.ContainsKey('Log')) { Invoke-SaraeLogRotation -Path $cmd.Log -MaxBytes 10MB -Keep 5 }
    $p = Start-Process @sp
    Save-SaraeTracked -Name $c.Name -Process $p
    Write-SaraeLog ('{0}: đã khởi động PID {1}: {2}' -f $c.Title, $p.Id, $line)
    return $p
}

function Stop-Component($c, $tracked) {
    if (-not $script:Cmdlet.ShouldProcess("$($c.Title) PID $($tracked.Pid)", 'Dừng tiến trình do watchdog khởi động')) { return }
    if ($c.Name -eq 'voice') {
        # Dừng êm: supervisor đợi câu đang đọc trên GPU xong rồi mới tắt server.
        try { [IO.File]::WriteAllText((Join-Path $cfg.VoiceDataDir 'supervisor.stop'), 'stop') } catch { Write-SaraeLog -Level WARN ('Không tạo được supervisor.stop: ' + $_.Exception.Message) }
        Write-SaraeLog 'Voice server: đã yêu cầu dừng êm, chờ tối đa 90 giây.'
        if ($tracked.Process.WaitForExit(90000)) { Clear-SaraeTracked $c.Name; return }
        Write-SaraeLog -Level WARN 'Voice server: không tự dừng trong 90 giây, dừng hẳn cây tiến trình của nó.'
    }
    Stop-SaraeProcessTree -ProcessId $tracked.Pid
    [void]$tracked.Process.WaitForExit(10000)
    Clear-SaraeTracked $c.Name
}

function Wait-ComponentHealthy($c, [int]$Seconds, $Process) {
    if ($Seconds -le 0 -or -not $Process) { return $false }
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        if ((Test-SaraeHttp -Url $c.Url -TimeoutSec 3).Ok) { Write-SaraeLog ('{0}: sẵn sàng.' -f $c.Title); return $true }
        if ($Process.HasExited) { Write-SaraeLog -Level WARN ('{0}: tiến trình thoát ngay (mã {1}).' -f $c.Title, $Process.ExitCode); return $false }
    }
    Write-SaraeLog -Level WARN ('{0}: chưa sẵn sàng sau {1} giây, tiếp tục với thành phần sau.' -f $c.Title, $Seconds)
    return $false
}

function Restart-Component($c, $tracked) {
    $st = $state[$c.Name]
    $now = Get-Date
    if ($st.PausedUntil -and $now -lt $st.PausedUntil) {
        Write-SaraeLog -Level WARN ('{0}: đang tạm ngưng tự khởi động lại đến {1:HH:mm}.' -f $c.Title, $st.PausedUntil)
        return
    }
    $st.PausedUntil = $null
    foreach ($t in @($st.Restarts)) { if (($now - $t).TotalMinutes -gt 15) { $st.Restarts.Remove($t) } }
    if ($st.Restarts.Count -ge 5) {
        $st.PausedUntil = $now.AddMinutes(15)
        $st.Restarts.Clear()
        $msg = '{0}: đã khởi động lại 5 lần trong 15 phút vẫn lỗi. Tạm ngưng 15 phút; xem log trong {1}.' -f $c.Title, $cfg.LogDir
        Write-SaraeLog -Level ERROR $msg
        Send-SaraeAlert ('🛑 ' + $msg)
        return
    }
    $err = Get-ComponentPrereqError $c
    if ($err) { Set-ComponentDown $c ('không chạy và không khởi động được: ' + $err) 'prereq'; return }
    if ($tracked) { Stop-Component $c $tracked }
    [void]$st.Restarts.Add($now)
    $st.FailingSince = $null
    $p = Start-Component $c
    if ($p) { [void](Wait-ComponentHealthy $c $c.WaitSec $p) }
}

function Get-ExternalOwner($c) {
    # Tiến trình KHÔNG do watchdog khởi động đang giữ cổng (hoặc cloudflared chạy ngoài). 0 nếu không có.
    $owner = Get-SaraePortOwner $c.Port
    if ($owner) { return $owner }
    if ($c.Name -eq 'cloudflared') {
        $p = Get-Process -Name cloudflared -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($p) { return $p.Id }
    }
    return 0
}

function Invoke-CloudflaredServiceCheck($c, $svc) {
    $st = $state[$c.Name]
    $svc.Refresh()
    $st.Pid = 0
    if ($svc.Status -eq 'Running') {
        # Service mặc định cũng mở metrics ở 127.0.0.1:20241, nên vẫn kiểm tra được /ready (có kết nối tới Cloudflare chưa).
        $h = Test-SaraeHttp -Url $c.Url -TimeoutSec 5
        $st.Ok = $h.Ok
        if ($h.Ok) {
            $st.Detail = 'OK (Windows service cloudflared, watchdog không quản lý)'
            if ($dry) { Write-Host ('{0}: OK, Windows service cloudflared ({1})' -f $c.Title, $c.Url) -ForegroundColor Green }
            Set-ComponentUp $c
            return
        }
        $st.Detail = "Windows service cloudflared đang chạy, /ready: $($h.Error)"
        if (-not $st.FailingSince) { $st.FailingSince = Get-Date }
        $failFor = ((Get-Date) - $st.FailingSince).TotalSeconds
        if ($failFor -lt $c.HungSec) {
            Write-SaraeLog -Level WARN ('{0}: service đang chạy nhưng {1} lỗi ({2}), {3:n0}/{4} giây.' -f $c.Title, $c.Url, $h.Error, $failFor, $c.HungSec)
            return
        }
        Set-ComponentDown $c ('Windows service "cloudflared" đang chạy nhưng {0} lỗi quá {1} giây ({2}): có thể mất kết nối tới Cloudflare, hoặc metrics ở cổng khác (đặt SARAE_CLOUDFLARED_METRICS). Watchdog không tự khởi động lại service; nếu cần, chạy (admin): Restart-Service cloudflared' -f $c.Url, $c.HungSec, $h.Error) 'service-notready'
    } else {
        $st.Ok = $false; $st.Detail = "Windows service cloudflared: $($svc.Status)"
        Set-ComponentDown $c ('Windows service "cloudflared" đang {0}. Watchdog không tự bật service; mở services.msc hoặc chạy (admin): Start-Service cloudflared' -f $svc.Status) 'service'
    }
}

function Invoke-ComponentCheck($c) {
    $st = $state[$c.Name]
    if ($c.Name -eq 'cloudflared') {
        $svc = Get-Service -Name 'cloudflared' -ErrorAction SilentlyContinue
        if ($svc) { Invoke-CloudflaredServiceCheck $c $svc; return }
    }
    $h = Test-SaraeHttp -Url $c.Url -TimeoutSec 5
    $tracked = Get-SaraeTracked $c.Name
    $st.Ok = $h.Ok
    $st.Pid = $(if ($tracked) { $tracked.Pid } else { 0 })
    if ($h.Ok) {
        $st.Detail = 'OK'
        if ($dry) { Write-Host ('{0}: OK ({1})' -f $c.Title, $c.Url) -ForegroundColor Green }
        Set-ComponentUp $c
        return
    }
    $st.Detail = $h.Error
    $now = Get-Date
    if ($tracked) {
        $age = ($now - $tracked.StartedAt).TotalSeconds
        if ($age -lt $c.GraceSec) {
            Write-SaraeLog ('{0}: đang khởi động ({1:n0}/{2} giây), health: {3}' -f $c.Title, $age, $c.GraceSec, $h.Error)
            return
        }
        if (-not $st.FailingSince) { $st.FailingSince = $now }
        $failFor = ($now - $st.FailingSince).TotalSeconds
        if ($failFor -lt $c.HungSec) {
            Write-SaraeLog -Level WARN ('{0}: PID {1} còn sống nhưng health lỗi ({2}), {3:n0}/{4} giây.' -f $c.Title, $tracked.Pid, $h.Error, $failFor, $c.HungSec)
            return
        }
        Set-ComponentDown $c ('PID {0} không phản hồi health quá {1} giây ({2}), khởi động lại.' -f $tracked.Pid, $c.HungSec, $h.Error) 'hung'
        Restart-Component $c $tracked
        return
    }
    $owner = Get-ExternalOwner $c
    if ($owner) {
        Set-ComponentDown $c ('health lỗi ({0}); cổng/tiến trình đang do PID {1} giữ, không phải watchdog khởi động nên không tự tắt.' -f $h.Error, $owner) 'external'
        return
    }
    $err = Get-ComponentPrereqError $c
    if ($err) { Set-ComponentDown $c ('không chạy và không khởi động được: ' + $err) 'prereq'; return }
    Set-ComponentDown $c ('không chạy ({0}), khởi động lại.' -f $h.Error) 'dead'
    Restart-Component $c $null
}

function Invoke-PublicCheck {
    if (-not $cfg.PublicHealthUrl -or $PublicCheckEverySec -le 0) { return }
    $allOk = $true
    foreach ($c in $components) { if (-not $state[$c.Name].Ok) { $allOk = $false } }
    if (-not $allOk) { return }   # lỗi local đã được cảnh báo riêng
    if (-not $dry -and ((Get-Date) - $public.Last).TotalSeconds -lt $PublicCheckEverySec) { return }
    $public.Last = Get-Date
    $h = Test-SaraeHttp -Url $cfg.PublicHealthUrl -TimeoutSec 10 -UseProxy
    $public.Ok = $h.Ok
    if ($h.Ok) {
        if ($dry) { Write-Host ('Truy cập công khai: OK ({0})' -f $cfg.PublicHealthUrl) -ForegroundColor Green }
        if ($public.Alerted) { Send-SaraeAlert ('✅ Truy cập công khai {0} đã OK lại.' -f $cfg.PublicHealthUrl) }
        $public.Fails = 0; $public.Alerted = $false
        return
    }
    $public.Fails++
    Write-SaraeLog -Level WARN ('Truy cập công khai {0} lỗi ({1}), lần {2}.' -f $cfg.PublicHealthUrl, $h.Error, $public.Fails)
    if ($public.Fails -ge 3 -and -not $public.Alerted) {
        Send-SaraeAlert ('⚠️ Khách không vào được {0} ({1}) dù voice, gateway, cloudflared ở PC nhà đều OK. Kiểm tra DNS/tunnel trên Cloudflare hoặc mạng nhà.' -f $cfg.PublicHealthUrl, $h.Error)
        $public.Alerted = $true
    }
}

function Write-WatchdogStatus {
    if ($dry) { return }
    $comp = [ordered]@{}
    foreach ($c in $components) {
        $st = $state[$c.Name]
        $comp[$c.Name] = [ordered]@{ ok = $st.Ok; detail = $st.Detail; pid = $st.Pid; down_since = $(if ($st.DownSince) { $st.DownSince.ToString('o') } else { $null }) }
    }
    $obj = [ordered]@{ time = (Get-Date).ToString('o'); watchdog_pid = $PID; maintenance = (Test-Path -LiteralPath $cfg.MaintenanceFlag)
        components = $comp; public_ok = $public.Ok; pending_alerts = $script:SaraeAlertQueue.Count }
    try {
        [IO.File]::WriteAllText((Join-Path $cfg.StateDir 'watchdog.status.json'), ($obj | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding $false))
    } catch { Write-SaraeLog -Level WARN ('Không ghi được watchdog.status.json: ' + $_.Exception.Message) }
}

function Invoke-WatchdogPass {
    if (Test-Path -LiteralPath $cfg.MaintenanceFlag) {
        if (((Get-Date) - $script:MaintenanceLogged).TotalMinutes -ge 10) {
            Write-SaraeLog -Level WARN ('Chế độ bảo trì ({0} tồn tại): không kiểm tra, không khởi động lại gì.' -f $cfg.MaintenanceFlag)
            $script:MaintenanceLogged = Get-Date
        }
        return
    }
    $script:MaintenanceLogged = [DateTime]::MinValue
    foreach ($c in $components) {
        try { Invoke-ComponentCheck $c }
        catch { Write-SaraeLog -Level ERROR ('{0}: lỗi khi kiểm tra/khởi động: {1}' -f $c.Title, $_.Exception.Message) }
    }
    Invoke-PublicCheck
}

# ---- Chạy ----
$mutex = $null
if (-not $dry) {
    $mutex = New-Object Threading.Mutex($false, 'Global\SaraeServerWatchdog')
    $owned = $false
    try { $owned = $mutex.WaitOne(0) }
    catch {
        # Watchdog trước bị tắt ngang: mutex "abandoned" thì bản này nhận luôn quyền sở hữu.
        if ($_.Exception -is [Threading.AbandonedMutexException] -or $_.Exception.InnerException -is [Threading.AbandonedMutexException]) { $owned = $true }
        else { throw }
    }
    if (-not $owned) { Write-Host 'Đã có một watchdog khác đang chạy; thoát.'; exit 0 }
}

Write-SaraeLog ('Watchdog bắt đầu (PID {0}, repo {1}, mỗi {2} giây{3}).' -f $PID, $cfg.RepoRoot, $IntervalSec, $(if ($dry) { ', WhatIf' } elseif ($Once) { ', một vòng' } else { '' }))
try {
    while ($true) {
        try { Invoke-WatchdogPass } catch { Write-SaraeLog -Level ERROR ('Lỗi vòng kiểm tra: ' + $_.Exception.Message) }
        if ($script:FirstPass) {
            $script:FirstPass = $false
            if (-not $Once -and -not $dry) {
                $boot = ''
                try { $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('dd/MM HH:mm') } catch { $boot = '?' }
                $summary = ($components | ForEach-Object { '{0}: {1}' -f $_.Name, $(if ($state[$_.Name].Ok) { 'OK' } else { $state[$_.Name].Detail }) }) -join '; '
                Send-SaraeAlert ('ℹ️ Watchdog đã chạy (máy bật lúc {0}). {1}' -f $boot, $summary)
            }
        }
        Send-SaraeQueuedAlerts
        Write-WatchdogStatus
        if ($Once -or $dry) { break }
        Start-Sleep -Seconds $IntervalSec
    }
} finally {
    if ($mutex) { try { $mutex.ReleaseMutex() } catch { $null = $_ }; $mutex.Dispose() }
}
if ($dry) { Write-Host 'WhatIf: không khởi động, không dừng, không gửi Telegram, không ghi file.' -ForegroundColor Cyan }
