#Requires -Version 5.1
<#
.SYNOPSIS
Sao lưu identity.sqlite3 và studio_gateway.sqlite3 bằng SQLite backup API, giữ -Keep bản mới nhất (mặc định 14).

.DESCRIPTION
Mỗi lần chạy tạo một thư mục <BackupDir>\yyyyMMdd-HHmmss\ chứa bản sao của từng DB. Bản sao được tạo bằng
sqlite3.Connection.backup() của Python (an toàn khi gateway đang ghi, kể cả chế độ WAL), không copy file thô,
rồi chạy PRAGMA integrity_check trên bản sao. Thư mục được ghi dưới tên tạm .partial và chỉ đổi tên khi mọi DB đều
sao lưu xong, nên một thư mục yyyyMMdd-HHmmss luôn là bản dùng được.
Chỉ xóa bản cũ khi lần sao lưu này thành công. DB chưa tồn tại (vd. gateway chưa chạy lần nào) thì bỏ qua và ghi chú.
Lỗi thì ghi log %LOCALAPPDATA%\SaraeServer\logs\backup.log và gửi Telegram (nếu đã cấu hình).

Mặc định: DataDir = SARAE_DATA_DIR hoặc <repo>\backend\data; BackupDir = SARAE_BACKUP_DIR hoặc
%LOCALAPPDATA%\SaraeServer\backups. Nên đặt SARAE_BACKUP_DIR sang ổ khác hoặc thư mục OneDrive/Google Drive.

.EXAMPLE
.\scripts\server\backup-db.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$DataDir,
    [string]$BackupDir,
    [ValidateRange(1, 365)][int]$Keep = 14,
    [string]$Python
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'SaraeServer.Common.ps1')
$dry = [bool]$WhatIfPreference
$cfg = Initialize-SaraeServer -LogName 'backup' -DryRun $dry
if (-not $DataDir) { $DataDir = $cfg.DataDir }
if (-not $BackupDir) { $BackupDir = $cfg.BackupDir }
$databases = @('identity.sqlite3', 'studio_gateway.sqlite3')

# Python đọc từ stdin (python -I - <src> <dst>): đường dẫn đi qua argv nên không phải lo dấu nháy. Giữ ASCII.
$backupCode = @'
import os, sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
if not os.path.isfile(src):
    print('missing: ' + src)
    sys.exit(4)  # connect() would create an empty DB
tmp = dst + '.tmp'
if os.path.exists(tmp):
    os.remove(tmp)
s = sqlite3.connect(src, timeout=30)
d = sqlite3.connect(tmp)
try:
    s.backup(d)
    r = d.execute('PRAGMA integrity_check').fetchone()[0]
    n = d.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
finally:
    d.close()
    s.close()
if r != 'ok':
    os.remove(tmp)
    print('integrity_check: ' + str(r))
    sys.exit(3)
os.replace(tmp, dst)
print('%d bytes, %d tables' % (os.path.getsize(dst), n))
'@

function Resolve-BackupPython {
    if ($Python) { return @{ Exe = $Python; Pre = @() } }
    if (Test-Path -LiteralPath $cfg.BackendPython) { return @{ Exe = $cfg.BackendPython; Pre = @() } }
    $py = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($py) { return @{ Exe = $py.Source; Pre = @('-3') } }
    $python = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($python) { return @{ Exe = $python.Source; Pre = @() } }
    return $null
}

function Invoke-SqliteBackup($Py, [string]$Source, [string]$Target) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'   # stderr của python không được biến thành lỗi dừng script
    try {
        $out = $backupCode | & $Py.Exe @($Py.Pre) -I - $Source $Target 2>&1
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
    $text = (@($out) | ForEach-Object { "$_" }) -join ' '
    return @{ Ok = ($code -eq 0); Code = $code; Text = $text.Trim() }
}

$py = Resolve-BackupPython
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$final = Join-Path $BackupDir $stamp
$work = "$final.partial"

Write-SaraeLog ('Sao lưu {0} -> {1} (giữ {2} bản).' -f $DataDir, $final, $Keep)
if (-not $py) {
    $msg = 'Sao lưu DB thất bại: không tìm thấy Python (backend\.venv, py hoặc python).'
    Write-SaraeLog -Level ERROR $msg
    Send-SaraeAlert ('🛑 ' + $msg)
    exit 1
}
Write-SaraeLog ('Python: {0} {1}' -f $py.Exe, ($py.Pre -join ' '))

$present = @()
foreach ($db in $databases) {
    $src = Join-Path $DataDir $db
    if (Test-Path -LiteralPath $src -PathType Leaf) {
        $present += $db
        Write-SaraeLog ('  {0}: {1:n0} KB' -f $db, ((Get-Item -LiteralPath $src).Length / 1KB))
    } else {
        Write-SaraeLog -Level WARN ('  {0}: chưa có ({1}), bỏ qua.' -f $db, $src)
    }
}
if ($present.Count -eq 0) {
    $msg = 'Sao lưu DB: không thấy DB nào trong {0}. Kiểm tra SARAE_DATA_DIR.' -f $DataDir
    Write-SaraeLog -Level ERROR $msg
    if (-not $dry) { Send-SaraeAlert ('🛑 ' + $msg); exit 1 }
}

$failed = @()
if ($present.Count -gt 0 -and $PSCmdlet.ShouldProcess($final, ('Sao lưu {0} bằng sqlite3 backup API' -f ($present -join ', ')))) {
    [void][IO.Directory]::CreateDirectory($work)
    foreach ($db in $present) {
        $r = Invoke-SqliteBackup $py (Join-Path $DataDir $db) (Join-Path $work $db)
        if ($r.Ok) { Write-SaraeLog ('  {0}: OK, {1}' -f $db, $r.Text) }
        else { $failed += $db; Write-SaraeLog -Level ERROR ('  {0}: LỖI (mã {1}) {2}' -f $db, $r.Code, $r.Text) }
    }
    if ($failed.Count -gt 0) {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
        $msg = 'Sao lưu DB thất bại: {0}. Xem {1}' -f ($failed -join ', '), (Join-Path $cfg.LogDir 'backup.log')
        Write-SaraeLog -Level ERROR $msg
        Send-SaraeAlert ('🛑 ' + $msg)
        exit 1
    }
    $manifest = [ordered]@{ created = (Get-Date).ToString('o'); host = $env:COMPUTERNAME; data_dir = $DataDir; databases = $present }
    [IO.File]::WriteAllText((Join-Path $work 'manifest.json'), ($manifest | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
    [IO.Directory]::Move($work, $final)
    Write-SaraeLog ('Xong: {0}' -f $final)
}

# ---- Giữ $Keep bản mới nhất ----
$snapshots = @()
if (Test-Path -LiteralPath $BackupDir) {
    $snapshots = @(Get-ChildItem -LiteralPath $BackupDir -Directory | Where-Object { $_.Name -match '^\d{8}-\d{6}$' } | Sort-Object Name -Descending)
}
$existing = $snapshots.Count
$willCreate = $dry -and $present.Count -gt 0   # WhatIf: tính cả bản sẽ tạo
if ($willCreate) { $existing++ }
Write-SaraeLog ('Đang có {0} bản sao lưu{1}.' -f $existing, $(if ($willCreate) { ' (kể cả bản sẽ tạo)' } else { '' }))
$toDelete = @($snapshots | Select-Object -Skip ($(if ($willCreate) { $Keep - 1 } else { $Keep })))
foreach ($old in $toDelete) {
    if ($PSCmdlet.ShouldProcess($old.FullName, 'Xóa bản sao lưu cũ')) {
        Remove-Item -LiteralPath $old.FullName -Recurse -Force
        Write-SaraeLog ('Đã xóa bản cũ {0}' -f $old.Name)
    }
}
# Thư mục .partial sót lại từ lần bị tắt ngang (quá 1 ngày).
if (Test-Path -LiteralPath $BackupDir) {
    foreach ($p in @(Get-ChildItem -LiteralPath $BackupDir -Directory -Filter '*.partial' | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-1) })) {
        if ($PSCmdlet.ShouldProcess($p.FullName, 'Xóa bản sao lưu dở dang')) { Remove-Item -LiteralPath $p.FullName -Recurse -Force }
    }
}
if ($dry) { Write-Host 'WhatIf: không tạo hay xóa bản sao lưu nào.' -ForegroundColor Cyan }
