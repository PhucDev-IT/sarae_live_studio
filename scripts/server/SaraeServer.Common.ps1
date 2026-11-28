#Requires -Version 5.1
<#
Dùng chung cho các script vận hành server PC nhà (scripts\server\*.ps1). Không chạy trực tiếp: dot-source nó.

  . (Join-Path $PSScriptRoot 'SaraeServer.Common.ps1')
  Initialize-SaraeServer -LogName 'watchdog' -DryRun:$WhatIfPreference

Cấu hình đọc theo thứ tự: biến môi trường của tiến trình, rồi file %LOCALAPPDATA%\SaraeServer\server.env
(dạng KEY=VALUE, dòng bắt đầu bằng # là chú thích), rồi giá trị mặc định bên dưới.

  SARAE_SERVER_HOME          thư mục vận hành (log, state, backup)   mặc định %LOCALAPPDATA%\SaraeServer
  SARAE_DATA_DIR             dữ liệu gateway (giống backend)          mặc định <repo>\backend\data
  SARAE_BACKUP_DIR           nơi lưu bản sao lưu                      mặc định <SARAE_SERVER_HOME>\backups
  SARAE_VOICE_SERVER_DIR     thư mục sarae-voice-server               mặc định F:\Coder\sarae-voice-server
  SARAE_GATEWAY_PORT         cổng gateway                             mặc định 8000
  SARAE_GATEWAY_ACCESS_LOG   1 = bật access log của uvicorn           mặc định tắt
  SARAE_CLOUDFLARED_EXE      đường dẫn cloudflared.exe                mặc định tìm trên PATH / Program Files
  SARAE_CLOUDFLARED_CONFIG   file config.yml của tunnel              mặc định %USERPROFILE%\.cloudflared\config.yml
  SARAE_CLOUDFLARED_TUNNEL   tên/UUID tunnel (nếu config.yml không có dòng tunnel:)
  SARAE_CLOUDFLARED_METRICS  địa chỉ metrics/ready của cloudflared    mặc định 127.0.0.1:20241
  SARAE_PUBLIC_HEALTH_URL    URL kiểm tra từ ngoài vào (rỗng = tắt)    mặc định https://api.puns.id.vn/api/health
  SARAE_TELEGRAM_BOT_TOKEN   token bot Telegram (không bao giờ ghi ra log)
  SARAE_TELEGRAM_CHAT_ID     chat id nhận cảnh báo
#>
Set-StrictMode -Version 2.0

# Nạp sẵn module dùng tới với WhatIf tắt: nếu để PowerShell tự nạp khi đang -WhatIf, nó in ra hàng loạt
# "What if: Set Alias ..." vô nghĩa (module tự tạo alias lúc nạp).
$script:SaraeWhatIfSaved = $WhatIfPreference
$WhatIfPreference = $false
Import-Module CimCmdlets, NetTCPIP, ScheduledTasks -ErrorAction SilentlyContinue
$WhatIfPreference = $script:SaraeWhatIfSaved

$script:SaraeConfig = $null
$script:SaraeDryRun = $false
$script:SaraeLogName = 'server'
$script:SaraeSecrets = @()
$script:SaraeFileEnv = @{}
$script:SaraeAlertQueue = New-Object System.Collections.ArrayList
$script:SaraeTelegramMissingLogged = $false

function Read-SaraeEnvFile {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $map }
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith('#')) { continue }
        $i = $t.IndexOf('=')
        if ($i -lt 1) { continue }
        $k = $t.Substring(0, $i).Trim()
        $v = $t.Substring($i + 1).Trim().Trim('"').Trim("'")
        $map[$k] = $v
    }
    return $map
}

function Get-SaraeSetting {
    param([string]$Name, [string]$Default = '')
    $v = [Environment]::GetEnvironmentVariable($Name)
    if (-not [string]::IsNullOrWhiteSpace($v)) { return $v.Trim() }
    if ($script:SaraeFileEnv.ContainsKey($Name) -and $script:SaraeFileEnv[$Name]) { return $script:SaraeFileEnv[$Name] }
    return $Default
}

function Resolve-SaraeCloudflared {
    $explicit = Get-SaraeSetting 'SARAE_CLOUDFLARED_EXE'
    if ($explicit) { return $explicit }
    $cmd = Get-Command cloudflared.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    $candidates = @()
    if (${env:ProgramFiles(x86)}) { $candidates += (Join-Path ${env:ProgramFiles(x86)} 'cloudflared\cloudflared.exe') }
    if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles 'cloudflared\cloudflared.exe') }
    foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { return $c } }
    return $candidates[0]
}

function Initialize-SaraeServer {
    param([string]$LogName = 'server', [bool]$DryRun = $false)
    $script:SaraeLogName = $LogName
    $script:SaraeDryRun = $DryRun

    $repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
    $homeDefault = Join-Path $env:LOCALAPPDATA 'SaraeServer'
    $homeEnv = [Environment]::GetEnvironmentVariable('SARAE_SERVER_HOME')
    if ([string]::IsNullOrWhiteSpace($homeEnv)) { $homeDir = $homeDefault } else { $homeDir = $homeEnv.Trim() }
    $script:SaraeFileEnv = Read-SaraeEnvFile (Join-Path $homeDir 'server.env')

    $backend = Join-Path $repo 'backend'
    $dataDir = Get-SaraeSetting 'SARAE_DATA_DIR' (Join-Path $backend 'data')
    $voiceDir = Get-SaraeSetting 'SARAE_VOICE_SERVER_DIR' 'F:\Coder\sarae-voice-server'

    # Cổng và thư mục dữ liệu của voice server lấy từ .env của chính nó (giống scripts\service.ps1 bên đó).
    $voiceEnv = Read-SaraeEnvFile (Join-Path $voiceDir '.env')
    $voicePort = 8780
    if ($voiceEnv.ContainsKey('SARAE_VOICE_PORT') -and $voiceEnv['SARAE_VOICE_PORT'] -match '^\d+$') { $voicePort = [int]$voiceEnv['SARAE_VOICE_PORT'] }
    $voiceData = 'data'
    if ($voiceEnv.ContainsKey('SARAE_VOICE_DATA_DIR') -and $voiceEnv['SARAE_VOICE_DATA_DIR']) { $voiceData = $voiceEnv['SARAE_VOICE_DATA_DIR'] }
    if (-not [IO.Path]::IsPathRooted($voiceData)) { $voiceData = Join-Path $voiceDir $voiceData }

    $gatewayPort = [int](Get-SaraeSetting 'SARAE_GATEWAY_PORT' '8000')
    $metrics = Get-SaraeSetting 'SARAE_CLOUDFLARED_METRICS' '127.0.0.1:20241'
    $metricsPort = [int]($metrics.Split(':')[-1])

    $script:SaraeConfig = [pscustomobject]@{
        RepoRoot           = $repo
        BackendDir         = $backend
        BackendPython      = Join-Path $backend '.venv\Scripts\python.exe'
        DataDir            = $dataDir
        HomeDir            = $homeDir
        LogDir             = Join-Path $homeDir 'logs'
        StateDir           = Join-Path $homeDir 'state'
        BackupDir          = Get-SaraeSetting 'SARAE_BACKUP_DIR' (Join-Path $homeDir 'backups')
        MaintenanceFlag    = Join-Path $homeDir 'maintenance'
        VoiceDir           = $voiceDir
        VoicePythonW       = Join-Path $voiceDir '.venv\Scripts\pythonw.exe'
        VoiceDataDir       = $voiceData
        VoicePort          = $voicePort
        VoiceHealthUrl     = "http://127.0.0.1:$voicePort/health/ready"
        GatewayPort        = $gatewayPort
        GatewayHealthUrl   = "http://127.0.0.1:$gatewayPort/api/health"
        GatewayAccessLog   = ((Get-SaraeSetting 'SARAE_GATEWAY_ACCESS_LOG' '0') -eq '1')
        CloudflaredExe     = Resolve-SaraeCloudflared
        CloudflaredConfig  = Get-SaraeSetting 'SARAE_CLOUDFLARED_CONFIG' (Join-Path $env:USERPROFILE '.cloudflared\config.yml')
        CloudflaredTunnel  = Get-SaraeSetting 'SARAE_CLOUDFLARED_TUNNEL'
        CloudflaredMetrics = $metrics
        CloudflaredPort    = $metricsPort
        CloudflaredReadyUrl = "http://$metrics/ready"
        PublicHealthUrl    = Get-SaraeSetting 'SARAE_PUBLIC_HEALTH_URL' 'https://api.puns.id.vn/api/health'
        TelegramToken      = Get-SaraeSetting 'SARAE_TELEGRAM_BOT_TOKEN'
        TelegramChatId     = Get-SaraeSetting 'SARAE_TELEGRAM_CHAT_ID'
    }
    $script:SaraeSecrets = @($script:SaraeConfig.TelegramToken) | Where-Object { $_ }

    if (-not $DryRun) {
        foreach ($d in @($script:SaraeConfig.LogDir, $script:SaraeConfig.StateDir)) {
            if (-not (Test-Path -LiteralPath $d)) { [void][IO.Directory]::CreateDirectory($d) }
        }
    }
    return $script:SaraeConfig
}

function Protect-SaraeSecret {
    # Thay mọi bí mật đã biết (token Telegram) bằng *** trước khi in hay ghi log.
    param([string]$Text)
    if (-not $Text) { return $Text }
    foreach ($s in $script:SaraeSecrets) {
        if ($s -and $s.Length -ge 6) { $Text = $Text.Replace($s, '***') }
    }
    return [regex]::Replace($Text, 'bot\d{5,}:[A-Za-z0-9_-]{20,}', 'bot***')
}

function Invoke-SaraeLogRotation {
    # Đổi tên file.log -> file.log.1 -> ... -> file.log.<Keep>, bỏ bản cũ nhất. Lỗi (file đang mở) thì bỏ qua.
    param([string]$Path, [long]$MaxBytes = 5MB, [int]$Keep = 5)
    if ($script:SaraeDryRun) { return }
    try {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
        if ((Get-Item -LiteralPath $Path).Length -lt $MaxBytes) { return }
        $oldest = "$Path.$Keep"
        if ([IO.File]::Exists($oldest)) { [IO.File]::Delete($oldest) }
        for ($i = $Keep - 1; $i -ge 1; $i--) {
            $src = "$Path.$i"
            if ([IO.File]::Exists($src)) { [IO.File]::Move($src, "$Path.$($i + 1)") }
        }
        [IO.File]::Move($Path, "$Path.1")
    } catch {
        Write-Host ("Không xoay vòng được log {0}: {1}" -f $Path, $_.Exception.Message)
    }
}

function Write-SaraeLog {
    param([string]$Message, [ValidateSet('INFO', 'WARN', 'ERROR')][string]$Level = 'INFO', [string]$Name = '')
    if (-not $Name) { $Name = $script:SaraeLogName }
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, (Protect-SaraeSecret $Message)
    $color = 'Gray'
    if ($Level -eq 'WARN') { $color = 'Yellow' } elseif ($Level -eq 'ERROR') { $color = 'Red' }
    Write-Host $line -ForegroundColor $color
    if ($script:SaraeDryRun -or -not $script:SaraeConfig) { return }
    try {
        $path = Join-Path $script:SaraeConfig.LogDir "$Name.log"
        Invoke-SaraeLogRotation -Path $path
        [IO.File]::AppendAllText($path, $line + "`r`n", (New-Object Text.UTF8Encoding $false))
    } catch {
        Write-Host ("Không ghi được log: {0}" -f $_.Exception.Message)
    }
}

function Send-SaraeTelegram {
    param([string]$Text)
    $cfg = $script:SaraeConfig
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $body = @{ chat_id = $cfg.TelegramChatId; text = $Text; disable_web_page_preview = $true } | ConvertTo-Json -Compress
    $uri = 'https://api.telegram.org/bot' + $cfg.TelegramToken + '/sendMessage'
    # -Verbose:$false: nếu ai đó chạy script với -Verbose, PowerShell sẽ in URI (có token) ra màn hình.
    Invoke-RestMethod -Method Post -Uri $uri -ContentType 'application/json; charset=utf-8' `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 15 -Verbose:$false | Out-Null
}

function Send-SaraeAlert {
    # Gửi cảnh báo Telegram nếu đã cấu hình token + chat id. Gửi lỗi (mất mạng) thì xếp hàng, lần sau gửi lại.
    param([string]$Text)
    $cfg = $script:SaraeConfig
    $msg = '[Sarae server {0}] {1} ({2})' -f $env:COMPUTERNAME, $Text, (Get-Date -Format 'dd/MM HH:mm:ss')
    if (-not $cfg.TelegramToken -or -not $cfg.TelegramChatId) {
        if (-not $script:SaraeTelegramMissingLogged) {
            Write-SaraeLog -Level WARN 'Chưa đặt SARAE_TELEGRAM_BOT_TOKEN và SARAE_TELEGRAM_CHAT_ID: cảnh báo chỉ ghi vào log.'
            $script:SaraeTelegramMissingLogged = $true
        }
        Write-SaraeLog -Level WARN ("CẢNH BÁO: " + $Text)
        return
    }
    if ($script:SaraeDryRun) { Write-Host ("[WhatIf] Sẽ gửi Telegram: " + (Protect-SaraeSecret $msg)) -ForegroundColor Cyan; return }
    [void]$script:SaraeAlertQueue.Add($msg)
    while ($script:SaraeAlertQueue.Count -gt 20) { $script:SaraeAlertQueue.RemoveAt(0) }
    Send-SaraeQueuedAlerts
}

function Send-SaraeQueuedAlerts {
    if ($script:SaraeDryRun -or $script:SaraeAlertQueue.Count -eq 0) { return }
    while ($script:SaraeAlertQueue.Count -gt 0) {
        $msg = [string]$script:SaraeAlertQueue[0]
        try {
            Send-SaraeTelegram -Text $msg
            $script:SaraeAlertQueue.RemoveAt(0)
            Write-SaraeLog ("Đã gửi Telegram: " + $msg)
        } catch {
            Write-SaraeLog -Level WARN ("Chưa gửi được Telegram ({0} tin đang chờ): {1}" -f $script:SaraeAlertQueue.Count, $_.Exception.Message)
            return
        }
    }
}

function Test-SaraeHttp {
    # GET một URL, trả về Ok/Status/Error. Mặc định không dùng proxy (chỉ gọi 127.0.0.1).
    param([string]$Url, [int]$TimeoutSec = 5, [switch]$UseProxy)
    $resp = $null
    try {
        $req = [Net.HttpWebRequest]::Create($Url)
        $req.Method = 'GET'
        $req.Timeout = $TimeoutSec * 1000
        $req.ReadWriteTimeout = $TimeoutSec * 1000
        $req.UserAgent = 'sarae-server-watchdog'
        if (-not $UseProxy) { $req.Proxy = $null }
        $resp = $req.GetResponse()
        $code = [int]$resp.StatusCode
        return [pscustomobject]@{ Ok = ($code -ge 200 -and $code -lt 300); Status = $code; Error = '' }
    } catch {
        $ex = $_.Exception
        while ($ex -and -not ($ex -is [Net.WebException])) { $ex = $ex.InnerException }
        if ($ex -and $ex.Response) {
            $code = [int]([Net.HttpWebResponse]$ex.Response).StatusCode
            $ex.Response.Close()
            return [pscustomobject]@{ Ok = $false; Status = $code; Error = "HTTP $code" }
        }
        $msg = $_.Exception.Message
        if ($ex) { $msg = '{0}: {1}' -f $ex.Status, $ex.Message }
        return [pscustomobject]@{ Ok = $false; Status = 0; Error = $msg }
    } finally {
        if ($resp) { $resp.Close() }
    }
}

# ---- Theo dõi tiến trình do watchdog khởi động (PID + thời điểm start, tránh nhầm PID bị Windows dùng lại) ----

function Get-SaraeStatePath { param([string]$Name) Join-Path $script:SaraeConfig.StateDir "$Name.json" }

function Save-SaraeTracked {
    param([string]$Name, [System.Diagnostics.Process]$Process)
    if ($script:SaraeDryRun) { return }
    $p = Get-Process -Id $Process.Id -ErrorAction SilentlyContinue
    if (-not $p) { return }
    $data = [pscustomobject]@{ pid = $p.Id; start_utc_ticks = $p.StartTime.ToUniversalTime().Ticks; started = (Get-Date).ToString('o'); path = $p.Path }
    [IO.File]::WriteAllText((Get-SaraeStatePath $Name), ($data | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding $false))
}

function Get-SaraeTracked {
    # Trả về tiến trình còn sống mà watchdog đã khởi động cho thành phần $Name, hoặc $null.
    param([string]$Name)
    $path = Get-SaraeStatePath $Name
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try { $data = [IO.File]::ReadAllText($path) | ConvertFrom-Json } catch { return $null }
    $p = Get-Process -Id ([int]$data.pid) -ErrorAction SilentlyContinue
    if (-not $p) { return $null }
    try { $ticks = $p.StartTime.ToUniversalTime().Ticks } catch { return $null }
    if ($ticks -ne [long]$data.start_utc_ticks) { return $null }
    return [pscustomobject]@{ Pid = $p.Id; Process = $p; StartedAt = $p.StartTime }
}

function Clear-SaraeTracked {
    param([string]$Name)
    if ($script:SaraeDryRun) { return }
    $path = Get-SaraeStatePath $Name
    if (Test-Path -LiteralPath $path) { [IO.File]::Delete($path) }
}

function Stop-SaraeProcessTree {
    # Chỉ dừng đúng cây tiến trình mà watchdog đã khởi động (PID đã kiểm tra thời điểm start).
    param([int]$ProcessId)
    if ($script:SaraeDryRun) { Write-Host "[WhatIf] Sẽ dừng cây tiến trình PID $ProcessId (taskkill /T /F)" -ForegroundColor Cyan; return }
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & taskkill.exe /PID $ProcessId /T /F 2>&1 | Out-Null } finally { $ErrorActionPreference = $old }
}

function Get-SaraePortOwner {
    # PID đang LISTEN trên cổng 127.0.0.1:<Port>, hoặc 0.
    param([int]$Port)
    $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return [int]$c.OwningProcess }
    return 0
}

function Test-SaraeDescendant {
    # $ChildId có phải là $AncestorId hoặc con/cháu của nó không (venv python.exe là launcher sinh ra tiến trình con).
    param([int]$ChildId, [int]$AncestorId)
    $seen = @{}
    $id = $ChildId
    for ($i = 0; $i -lt 8 -and $id -gt 0 -and -not $seen.ContainsKey($id); $i++) {
        if ($id -eq $AncestorId) { return $true }
        $seen[$id] = $true
        $w = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
        if (-not $w) { return $false }
        $id = [int]$w.ParentProcessId
    }
    return $false
}
