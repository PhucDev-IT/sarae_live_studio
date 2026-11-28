#Requires -Version 5.1
<#
.SYNOPSIS
Chỉ ĐỌC và báo các cài đặt có thể làm server PC nhà ngừng chạy: ngủ, ngủ đông, Windows Update tự khởi động lại.

.DESCRIPTION
Không sửa bất kỳ cài đặt nào. Cuối báo cáo in ra lệnh / đường dẫn Settings để bạn tự sửa nếu muốn.
Đọc bằng: powercfg /query (chỉ đọc), registry HKLM (chỉ đọc), Win32_OperatingSystem, Win32_Battery.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param()
$ErrorActionPreference = 'Stop'
if ($WhatIfPreference) { Write-Host 'Script này chỉ đọc, -WhatIf không thay đổi gì.' -ForegroundColor Cyan }
$WhatIfPreference = $false   # chỉ đọc; tránh "What if: Set Alias" khi PowerShell tự nạp module CimCmdlets

$issues = New-Object System.Collections.ArrayList
$fixes = New-Object System.Collections.ArrayList
function Add-Issue([string]$Text, [string]$Fix) {
    [void]$issues.Add($Text)
    if ($Fix -and -not $fixes.Contains($Fix)) { [void]$fixes.Add($Fix) }
}
function Write-Row([string]$Label, [string]$Value, [bool]$Ok = $true) {
    $color = 'Green'
    if (-not $Ok) { $color = 'Yellow' }
    Write-Host ('  {0,-46} {1}' -f $Label, $Value) -ForegroundColor $color
}

function Get-PowerSetting([string]$Sub, [string]$Setting) {
    # powercfg /query in ra (theo ngôn ngữ Windows) các dòng Min/Max/Increment rồi AC, DC. Lấy 2 số hex cuối: AC, DC.
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $out = & powercfg.exe /query SCHEME_CURRENT $Sub $Setting 2>&1 } finally { $ErrorActionPreference = $old }
    if ($LASTEXITCODE -ne 0) { return $null }
    $hex = @([regex]::Matches(($out -join "`n"), '0x[0-9a-fA-F]{8}') | ForEach-Object { [Convert]::ToInt64($_.Value, 16) })
    if ($hex.Count -lt 2) { return $null }
    return @{ AC = $hex[$hex.Count - 2]; DC = $hex[$hex.Count - 1] }
}

function Format-Timeout($sec) {
    if ($null -eq $sec) { return 'không đọc được' }
    if ($sec -eq 0) { return 'Không bao giờ' }
    if ($sec -lt 3600) { return ('{0} phút' -f [math]::Round($sec / 60)) }
    return ('{0:n1} giờ' -f ($sec / 3600))
}

function Get-RegValue([string]$Path, [string]$Name) {
    try { return (Get-ItemProperty -LiteralPath $Path -Name $Name -ErrorAction Stop).$Name } catch { return $null }
}

$hasBattery = [bool](Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue)
$os = Get-CimInstance Win32_OperatingSystem
Write-Host ''
Write-Host ('Máy: {0}  {1} (build {2})' -f $env:COMPUTERNAME, $os.Caption, $os.BuildNumber) -ForegroundColor Cyan
$uptime = (Get-Date) - $os.LastBootUpTime
Write-Host ('Khởi động lần cuối: {0:dd/MM/yyyy HH:mm} (đã chạy {1:n1} ngày)' -f $os.LastBootUpTime, $uptime.TotalDays)
$scheme = (& powercfg.exe /getactivescheme) -join ' '
Write-Host ('Power plan: {0}' -f $scheme.Trim())

# ---- Ngủ / ngủ đông ----
Write-Host ''
Write-Host '== Ngủ và ngủ đông (khi cắm điện) ==' -ForegroundColor Cyan
$standby = Get-PowerSetting 'SUB_SLEEP' 'STANDBYIDLE'
$hibernate = Get-PowerSetting 'SUB_SLEEP' 'HIBERNATEIDLE'
$hybrid = Get-PowerSetting 'SUB_SLEEP' 'HYBRIDSLEEP'
$unattended = Get-PowerSetting 'SUB_SLEEP' 'UNATTENDSLEEP'
$hibernateEnabled = Get-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Control\Power' 'HibernateEnabled'

if ($standby) {
    $ok = $standby.AC -eq 0
    Write-Row 'Tự ngủ (Sleep) sau' (Format-Timeout $standby.AC) $ok
    if (-not $ok) { Add-Issue ('Máy tự ngủ sau {0} không dùng: server sẽ mất kết nối.' -f (Format-Timeout $standby.AC)) 'powercfg /change standby-timeout-ac 0' }
} else { Write-Row 'Tự ngủ (Sleep) sau' 'không đọc được' $false }
if ($hibernate) {
    $ok = $hibernate.AC -eq 0
    Write-Row 'Tự ngủ đông (Hibernate) sau' (Format-Timeout $hibernate.AC) $ok
    if (-not $ok) { Add-Issue ('Máy tự ngủ đông sau {0}.' -f (Format-Timeout $hibernate.AC)) 'powercfg /change hibernate-timeout-ac 0' }
}
if ($unattended -and $unattended.AC -ne 0) {
    Write-Row 'Ngủ khi bị đánh thức tự động' (Format-Timeout $unattended.AC) $true
}
if ($hybrid) { Write-Row 'Hybrid sleep' $(if ($hybrid.AC -eq 1) { 'Bật' } else { 'Tắt' }) $true }
if ($null -ne $hibernateEnabled) {
    Write-Row 'Ngủ đông được bật trên máy' $(if ($hibernateEnabled -eq 1) { 'Có' } else { 'Không' }) $true
}
if ($hasBattery) {
    Write-Host ''
    Write-Host '  Máy có pin (laptop):' -ForegroundColor Yellow
    if ($standby) { Write-Row 'Tự ngủ khi chạy pin sau' (Format-Timeout $standby.DC) ($standby.DC -eq 0) }
    Add-Issue 'Máy có pin: nhớ luôn cắm sạc, và kiểm tra "Khi gập màn hình" không phải Sleep.' 'Control Panel > Power Options > Choose what closing the lid does > When I close the lid (Plugged in) = Do nothing'
}

# ---- Windows Update ----
Write-Host ''
Write-Host '== Windows Update ==' -ForegroundColor Cyan
$ux = 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'
$start = Get-RegValue $ux 'ActiveHoursStart'
$end = Get-RegValue $ux 'ActiveHoursEnd'
$smart = Get-RegValue $ux 'SmartActiveHoursState'
$pause = Get-RegValue $ux 'PauseUpdatesExpiryTime'
$pol = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'
$polSet = Get-RegValue $pol 'SetActiveHours'
$polStart = Get-RegValue $pol 'ActiveHoursStart'
$polEnd = Get-RegValue $pol 'ActiveHoursEnd'
$noReboot = Get-RegValue "$pol\AU" 'NoAutoRebootWithLoggedOnUsers'

if ($polSet -eq 1 -and $null -ne $polStart) {
    Write-Row 'Active hours (Group Policy)' ('{0:00}:00 - {1:00}:00' -f $polStart, $polEnd) $true
} elseif ($null -ne $start) {
    $mode = 'đặt tay'
    if ($smart -eq 1) { $mode = 'Windows tự điều chỉnh theo thói quen dùng' }
    Write-Row 'Active hours' ('{0:00}:00 - {1:00}:00 ({2})' -f $start, $end, $mode) $true
} else {
    Write-Row 'Active hours' 'không đọc được' $false
}
Add-Issue 'Ngoài active hours, Windows Update có thể tự khởi động lại máy. Watchdog sẽ tự bật lại server nếu cài autostart kiểu Startup, nhưng khách sẽ mất vài phút.' 'Settings > Windows Update > Advanced options > Active hours: đặt Manually, bao trọn giờ khách hay LIVE (tối đa 18 tiếng)'
if ($pause) { Write-Row 'Tạm dừng cập nhật đến' $pause $true }
if ($noReboot -eq 1) {
    Write-Row 'Không tự khởi động lại khi có người đăng nhập' 'Bật (Group Policy)' $true
} else {
    Write-Row 'Không tự khởi động lại khi có người đăng nhập' 'Chưa bật' $false
    Add-Issue 'Chưa bật chính sách "No auto-restart with logged on users" (Windows Pro có thể bật bằng gpedit).' 'gpedit.msc > Computer Configuration > Administrative Templates > Windows Components > Windows Update > Manage end user experience > No auto-restart with logged on users for scheduled automatic updates installations = Enabled'
}
$rebootPending = (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') -or
    (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')
Write-Row 'Đang chờ khởi động lại để cập nhật' $(if ($rebootPending) { 'CÓ' } else { 'Không' }) (-not $rebootPending)
if ($rebootPending) { Add-Issue 'Windows đang chờ khởi động lại để hoàn tất cập nhật: hãy tự khởi động lại vào lúc vắng khách (ngoài giờ LIVE), đừng để Windows tự chọn giờ.' 'Settings > Windows Update > Schedule the restart' }

# ---- Tóm tắt ----
Write-Host ''
Write-Host '== Cần xem lại ==' -ForegroundColor Cyan
if ($issues.Count -eq 0) { Write-Host '  Không thấy vấn đề.' -ForegroundColor Green }
foreach ($i in $issues) { Write-Host ('  - ' + $i) -ForegroundColor Yellow }

Write-Host ''
Write-Host '== Hướng dẫn tự sửa (script KHÔNG tự làm) ==' -ForegroundColor Cyan
Write-Host '  Lệnh powercfg chạy trong PowerShell thường; lệnh có (admin) cần "Run as administrator".'
foreach ($f in $fixes) { Write-Host ('  * ' + $f) }
Write-Host '  * Màn hình tự tắt thì không sao, chỉ cần máy không ngủ.'
Write-Host '  * (admin, tùy chọn) Tắt hẳn ngủ đông nếu không cần: powercfg /hibernate off'
Write-Host '  * BIOS/UEFI: bật "Restore on AC Power Loss" = Power On để máy tự bật lại sau khi mất điện.'
Write-Host '  * Nếu cài autostart kiểu Logon: cần tự đăng nhập sau khi khởi động lại, nên dùng kiểu Startup (xem docs\HYBRID_SERVER_OPS.md).'
Write-Host ''
