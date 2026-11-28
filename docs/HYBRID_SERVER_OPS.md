# Vận hành server PC nhà (hybrid)

Tài liệu này hướng dẫn chạy server ở PC nhà một cách ổn định: tự lên khi khởi động, tự hồi phục khi lỗi, có cảnh báo Telegram và sao lưu hằng ngày. Hợp đồng API giữa gateway và Studio nằm ở `docs/HYBRID_STUDIO_SERVER.md` (nhánh `feat/hybrid-cloud-server`).

```
máy khách ──HTTPS──▶ Cloudflare ──tunnel──▶ cloudflared ──▶ 127.0.0.1:8000 gateway ──▶ 127.0.0.1:8780 voice server (GPU)
```

| # | Tiến trình | Chạy từ | Health |
|---|---|---|---|
| 1 | Voice server (supervisor + server GPU) | `F:\Coder\sarae-voice-server`: `.venv\Scripts\pythonw.exe -m sarae_voice.supervisor` | `GET http://127.0.0.1:8780/health/ready` |
| 2 | Gateway FastAPI | `backend\`: `.venv\Scripts\python.exe -m uvicorn app.gateway_main:app --host 127.0.0.1 --port 8000` | `GET http://127.0.0.1:8000/api/health` |
| 3 | Cloudflare Tunnel | Windows service `cloudflared` (cách A), hoặc watchdog chạy `cloudflared tunnel --config %USERPROFILE%\.cloudflared\config.yml --metrics 127.0.0.1:20241 run` (cách B), xem mục 2 | `GET http://127.0.0.1:20241/ready` |

Các script nằm trong `scripts\server\` (PowerShell 5.1, không cần cài thêm phần mềm dịch vụ):

| Script | Việc |
|---|---|
| `install-autostart.ps1` | Đăng ký 2 task trong Task Scheduler (`\Sarae\`): watchdog và sao lưu hằng ngày. |
| `uninstall-autostart.ps1` | Gỡ 2 task đó. Thêm `-StopServer` để dừng luôn các tiến trình watchdog đã bật. |
| `watchdog.ps1` | Kiểm tra health mỗi 30 giây, bật lại theo đúng thứ tự, ghi log xoay vòng, cảnh báo Telegram. |
| `backup-db.ps1` | Sao lưu `identity.sqlite3` và `studio_gateway.sqlite3` bằng SQLite backup API, giữ 14 bản. |
| `check-power.ps1` | Chỉ đọc và báo cấu hình ngủ, ngủ đông, Windows Update; in hướng dẫn để bạn tự sửa. |
| `SaraeServer.Common.ps1` | Hàm dùng chung (cấu hình, log, Telegram). Không chạy trực tiếp. |

Mọi script cài/gỡ/watchdog/sao lưu đều có `-WhatIf`: in ra việc sẽ làm mà không thay đổi gì. Nên chạy `-WhatIf` trước mỗi lần làm thật.

Dữ liệu vận hành nằm ngoài repo, ở `%LOCALAPPDATA%\SaraeServer\` (đổi bằng biến `SARAE_SERVER_HOME`):

```
%LOCALAPPDATA%\SaraeServer\
  server.env          cấu hình riêng của máy (token Telegram...), tùy chọn
  maintenance         có file này = watchdog tạm ngừng tự bật lại (bảo trì)
  logs\               watchdog.log, backup.log, install.log, gateway.out.log, gateway.err.log, cloudflared.log
  state\              <thành phần>.json (PID do watchdog bật), watchdog.status.json (trạng thái mới nhất)
  backups\            yyyyMMdd-HHmmss\identity.sqlite3, studio_gateway.sqlite3, manifest.json
```

> Cài và chạy các script từ **thư mục chính** `F:\Coder\sarae_live_studio` (nơi có `backend\.venv` và `backend\data`), sau khi đã merge nhánh gateway. Không cài từ worktree trong `.claude\worktrees\`.

## 1. Chuẩn bị

1. Gateway: đã merge nhánh `feat/hybrid-cloud-server`, có `backend\app\gateway_main.py`, và `backend\.venv` đã cài `requirements.txt` (có `uvicorn`). Thử chạy tay một lần bằng `start-server.cmd` rồi mở `http://127.0.0.1:8000/api/health`.
2. Voice server: đã chạy `F:\Coder\sarae-voice-server\scripts\install.ps1`, và đã chạy `scripts\run.ps1` một lần để tạo `.env` (có token orchestrator). Token này phải trùng với `SARAE_TTS_SERVER_TOKEN` của gateway.
   - Nếu trước đây đã cài task `SaraeVoiceServer` bằng `scripts\service.ps1 install` của voice server, hãy gỡ nó (`service.ps1 uninstall`) để chỉ watchdog quản lý. Hai bên không chạy trùng được (voice server có khóa instance), nhưng một người quản lý thì dễ theo dõi hơn.
3. Chạy `check-power.ps1` và tự sửa các mục nó báo (mục 4).

## 2. Cài cloudflared và tạo tunnel

Các bước liên quan tài khoản Cloudflare bạn tự làm; script không đăng nhập hay tạo gì trên Cloudflare.

Có hai cách chạy tunnel, watchdog hỗ trợ cả hai:

| | A. Windows service `cloudflared` (tunnel tạo trên dashboard, cài bằng token) | B. Watchdog chạy cloudflared với `config.yml` (tunnel tạo bằng dòng lệnh) |
|---|---|---|
| Ai bật / bật lại | Windows (service Automatic, tự restart khi crash) | Watchdog |
| Cấu hình hostname | Dashboard Zero Trust > Networks > Tunnels > Public Hostname | File `config.yml` trên máy |
| Watchdog làm gì | Chỉ kiểm tra service và `/ready` ở `127.0.0.1:20241`, cảnh báo khi lỗi. Không tự restart service (cần admin) | Kiểm tra, bật lại, xoay vòng log |

**PC nhà hiện đã có service `cloudflared` (Automatic, đang chạy, tự restart sau 20 giây khi crash, `/ready` ở cổng 20241 trả 4 kết nối).** Cách A là cách đơn giản nhất: giữ nguyên service, chỉ cần kiểm tra trên dashboard rằng Public Hostname `api.puns.id.vn` trỏ tới `http://127.0.0.1:8000` (Type HTTP, URL `127.0.0.1:8000`), rồi bỏ qua các bước tạo tunnel bên dưới. Không cần `config.yml`. Mục `[THIẾU] Tunnel config.yml` khi chạy `install-autostart.ps1` chỉ là thông tin, không ảnh hưởng.

Nếu muốn chuyển sang cách B: tạo tunnel theo các bước dưới, rồi gỡ service (PowerShell admin: `cloudflared service uninstall`). Không chạy cả hai cho cùng một hostname.

Các bước cho cách B:

1. Cài cloudflared (một trong hai cách):

   ```powershell
   winget install --id Cloudflare.cloudflared
   ```

   hoặc tải bản `cloudflared-windows-amd64.msi` từ trang phát hành chính thức của Cloudflare. Mở cửa sổ PowerShell mới rồi kiểm tra:

   ```powershell
   cloudflared --version
   ```

   Watchdog tìm `cloudflared.exe` trên PATH, rồi trong `C:\Program Files (x86)\cloudflared\` và `C:\Program Files\cloudflared\`. Ở chỗ khác thì đặt `SARAE_CLOUDFLARED_EXE` (mục 3).

2. Đăng nhập (mở trình duyệt, chọn zone `puns.id.vn`). Lệnh tạo `%USERPROFILE%\.cloudflared\cert.pem`:

   ```powershell
   cloudflared tunnel login
   ```

3. Tạo tunnel. Lệnh in ra UUID và tạo file thông tin đăng nhập `%USERPROFILE%\.cloudflared\<UUID>.json`. Giữ file này bí mật và sao lưu nó: mất file thì phải tạo tunnel mới.

   ```powershell
   cloudflared tunnel create sarae-api
   ```

4. Trỏ DNS `api.puns.id.vn` vào tunnel (tạo bản ghi CNAME trên Cloudflare):

   ```powershell
   cloudflared tunnel route dns sarae-api api.puns.id.vn
   ```

5. Tạo `%USERPROFILE%\.cloudflared\config.yml` (thay `<UUID>` và tên người dùng):

   ```yaml
   # Tunnel cho server Sarae ở PC nhà. Watchdog chạy: cloudflared tunnel --config <file này> --metrics 127.0.0.1:20241 run
   tunnel: <UUID>
   credentials-file: C:\Users\<ten-nguoi-dung>\.cloudflared\<UUID>.json

   ingress:
     - hostname: api.puns.id.vn
       service: http://127.0.0.1:8000
       originRequest:
         connectTimeout: 10s
     # Mọi hostname khác: trả 404, không lộ dịch vụ nào khác trên máy.
     - service: http_status:404
   ```

   - Không cần khai báo `metrics:` trong file: watchdog tự truyền `--metrics 127.0.0.1:20241` để kiểm tra `/ready`. Cổng khác thì đặt `SARAE_CLOUDFLARED_METRICS`.
   - Chỉ public gateway (cổng 8000). Không bao giờ public voice server (8780).
   - Cloudflare cắt request quá 100 giây. Gateway đã giới hạn hàng đợi TTS 60 giây nên vẫn nằm trong giới hạn này.

6. Kiểm tra cấu hình rồi chạy thử tay (Ctrl+C để dừng):

   ```powershell
   cloudflared tunnel ingress validate
   ```

   ```powershell
   cloudflared tunnel ingress rule https://api.puns.id.vn/api/health
   ```

   ```powershell
   cloudflared tunnel run sarae-api
   ```

   Khi đang chạy, mở `https://api.puns.id.vn/api/health` từ điện thoại (tắt Wi-Fi) sẽ thấy `{"status":"UP",...}`.

7. Ở cách B **không** chạy `cloudflared service install`: watchdog tự quản lý cloudflared. Hễ còn Windows service `cloudflared`, watchdog coi tunnel là do service quản lý (cách A): chỉ kiểm tra và cảnh báo, không tự chạy thêm.

## 3. Cấu hình (tùy chọn) và cảnh báo Telegram

Watchdog và các script đọc cấu hình theo thứ tự: biến môi trường, rồi file `%LOCALAPPDATA%\SaraeServer\server.env`, rồi mặc định. Nên dùng `server.env`: dùng được cho cả hai kiểu autostart và không cần khởi động lại phiên đăng nhập.

| Biến | Mặc định |
|---|---|
| `SARAE_TELEGRAM_BOT_TOKEN` | (trống: chỉ ghi log) |
| `SARAE_TELEGRAM_CHAT_ID` | (trống) |
| `SARAE_BACKUP_DIR` | `%LOCALAPPDATA%\SaraeServer\backups` |
| `SARAE_DATA_DIR` | `<repo>\backend\data` (giống gateway) |
| `SARAE_VOICE_SERVER_DIR` | `F:\Coder\sarae-voice-server` |
| `SARAE_GATEWAY_PORT` | `8000` |
| `SARAE_GATEWAY_ACCESS_LOG` | `0` (đặt `1` để uvicorn ghi từng request vào `gateway.err.log`) |
| `SARAE_CLOUDFLARED_EXE` | tìm trên PATH / Program Files |
| `SARAE_CLOUDFLARED_CONFIG` | `%USERPROFILE%\.cloudflared\config.yml` |
| `SARAE_CLOUDFLARED_TUNNEL` | (trống: lấy `tunnel:` trong config.yml) |
| `SARAE_CLOUDFLARED_METRICS` | `127.0.0.1:20241` |
| `SARAE_PUBLIC_HEALTH_URL` | `https://api.puns.id.vn/api/health` (để trống để tắt kiểm tra từ ngoài) |
| `SARAE_SERVER_HOME` | `%LOCALAPPDATA%\SaraeServer` (chỉ đặt bằng biến môi trường) |

Tạo bot Telegram:

1. Trên Telegram, nhắn `@BotFather` lệnh `/newbot`, đặt tên, nhận token dạng `123456789:AA...`.
2. Nhắn một tin bất kỳ cho bot vừa tạo (hoặc thêm bot vào nhóm nhận cảnh báo).
3. Mở `https://api.telegram.org/bot<token>/getUpdates` trong trình duyệt, tìm `"chat":{"id":...}`. Đó là chat id (nhóm thì là số âm).
4. Tạo file `%LOCALAPPDATA%\SaraeServer\server.env`:

   ```
   # Bí mật: không commit, không gửi file này cho ai.
   SARAE_TELEGRAM_BOT_TOKEN=123456789:AA...
   SARAE_TELEGRAM_CHAT_ID=123456789
   # Nên lưu bản sao lưu sang ổ khác hoặc thư mục đồng bộ đám mây:
   SARAE_BACKUP_DIR=D:\SaraeBackups
   ```

Token không bao giờ được ghi ra log hay màn hình: mọi dòng log đều được che token thành `***`.

Watchdog gửi Telegram khi: watchdog vừa chạy (kèm giờ máy bật, cho biết máy vừa khởi động lại), một thành phần chết hoặc treo, thành phần hồi phục, khởi động lại 5 lần trong 15 phút vẫn lỗi (tạm ngưng 15 phút), và khi khách không vào được `api.puns.id.vn` dù mọi thứ ở PC nhà đều OK (3 lần liên tiếp, kiểm tra mỗi 5 phút). Mỗi trạng thái chỉ báo một lần, không spam. Khi mất mạng, tin được xếp hàng (tối đa 20) và gửi khi có mạng lại.

## 4. Nguồn điện và Windows Update

```powershell
.\scripts\server\check-power.ps1
```

Script chỉ đọc và in ra những gì cần sửa, ví dụ:

- Tắt tự ngủ khi cắm điện: `powercfg /change standby-timeout-ac 0`
- Tắt tự ngủ đông khi cắm điện: `powercfg /change hibernate-timeout-ac 0`
- Settings > Windows Update > Advanced options > Active hours: chọn Manually, bao trọn giờ khách hay dùng.
- Windows Pro: `gpedit.msc` > Windows Update > Manage end user experience > "No auto-restart with logged on users for scheduled automatic updates installations" = Enabled.
- BIOS/UEFI: "Restore on AC Power Loss" = Power On, để máy tự bật lại sau khi mất điện.

Dù đã chỉnh, Windows Update vẫn có thể khởi động lại máy. Vì vậy nên cài autostart kiểu `Startup` (mục 5) để server tự lên mà không cần ai đăng nhập.

## 5. Cài autostart

Mở PowerShell trong `F:\Coder\sarae_live_studio`. Luôn xem kế hoạch trước:

```powershell
.\scripts\server\install-autostart.ps1 -WhatIf
```

Script kiểm tra các file cần có (python, gateway, voice server, cloudflared, config.yml, Telegram) và in ra task sẽ tạo. Mục nào báo `[THIẾU]` thì sửa trước.

### Chọn kiểu khởi chạy

| | `-Trigger Startup` (mặc định, khuyến nghị) | `-Trigger Logon` |
|---|---|---|
| Khi nào chạy | Khi Windows khởi động, không cần ai đăng nhập | Khi bạn đăng nhập Windows |
| Sau khi Windows Update / mất điện khởi động lại | Server tự lên | Server chỉ lên khi có người đăng nhập |
| Quyền khi cài | Cần "Run as administrator" | Không cần admin |
| Mật khẩu | Không lưu (kiểu đăng nhập S4U, chạy dưới chính tài khoản của bạn) | Không lưu |
| Cửa sổ | Không có (chạy ở session 0) | Ẩn |

Chọn `Startup` vì server phải chạy kể cả khi máy tự khởi động lại lúc không có ai. Hạn chế của S4U: tiến trình không truy cập được ổ mạng/thư mục chia sẻ cần đăng nhập (server này không cần), và chạy ở session 0. CUDA vẫn chạy được ở session 0; nếu voice server báo không thấy GPU ở chế độ này thì gỡ và cài lại với `-Trigger Logon`, rồi bật tự đăng nhập Windows nếu muốn.

Cài thật (PowerShell "Run as administrator" cho kiểu Startup):

```powershell
.\scripts\server\install-autostart.ps1 -Trigger Startup -StartNow
```

Không muốn chạy ngay thì bỏ `-StartNow`; task sẽ chạy ở lần khởi động tới, hoặc tự chạy bằng `Start-ScheduledTask -TaskPath '\Sarae\' -TaskName 'Sarae Server Watchdog'`.

Hai task được tạo trong Task Scheduler (`taskschd.msc` > Task Scheduler Library > Sarae):

- **Sarae Server Watchdog**: chạy `watchdog.ps1` (vòng lặp vô hạn). Trigger lặp lại mỗi 5 phút để nếu watchdog bị tắt thì Task Scheduler bật lại; không bao giờ có 2 bản cùng chạy. Priority Normal (mặc định của Task Scheduler thấp hơn bình thường, sẽ làm chậm gateway và voice server).
- **Sarae Server Backup**: chạy `backup-db.ps1` mỗi ngày lúc 03:30 (đổi bằng `-BackupAt 04:00`). Máy tắt đúng giờ đó thì chạy bù khi bật lại.

Watchdog làm gì mỗi 30 giây, theo thứ tự voice → gateway → cloudflared:

- Health OK: không làm gì.
- Tiến trình không chạy: bật lại. Nhiều cái cùng chết thì bật lần lượt, chờ cái trước sẵn sàng (voice tối đa 120 giây, gateway 60 giây). Gateway vẫn được bật dù voice chưa lên, vì kích hoạt máy và license không cần GPU; TTS khi đó trả `TTS_UNAVAILABLE` và Studio đọc bằng giọng Edge.
- Tiến trình còn sống nhưng health lỗi quá lâu (sau thời gian khởi động: voice 4 phút + 3 phút, gateway 45 + 60 giây, cloudflared 60 + 120 giây): dừng đúng cây tiến trình đó rồi bật lại. Voice server được dừng êm qua `supervisor.stop`: câu đang đọc trên GPU chạy xong trước.
- Quá 5 lần khởi động lại trong 15 phút: tạm ngưng thành phần đó 15 phút và cảnh báo.
- Watchdog chỉ dừng tiến trình do chính nó bật (PID và thời điểm start lưu trong `state\`). Cổng đang bị tiến trình khác giữ (ví dụ bạn tự chạy `start-voice-server.bat` hay `start-server.cmd`) thì chỉ cảnh báo, không tắt. Nếu tiến trình đó vẫn khỏe thì watchdog dùng luôn.
- Bản thân supervisor của voice server đã tự bật lại server khi crash hoặc lỗi CUDA; watchdog chỉ bật lại supervisor khi nó bỏ cuộc (10 lỗi nhanh trong 10 phút) hoặc bị treo.

## 6. Xem trạng thái và đọc log

Kiểm tra nhanh một lượt, không thay đổi gì (không bật, không tắt, không gửi Telegram, không ghi file):

```powershell
.\scripts\server\watchdog.ps1 -WhatIf
```

Trạng thái mới nhất watchdog ghi mỗi vòng:

```powershell
Get-Content "$env:LOCALAPPDATA\SaraeServer\state\watchdog.status.json"
```

Trạng thái task:

```powershell
Get-ScheduledTask -TaskPath '\Sarae\' | Get-ScheduledTaskInfo | Format-Table TaskName, LastRunTime, LastTaskResult, NextRunTime
```

Theo dõi log watchdog trực tiếp (Ctrl+C để thoát):

```powershell
Get-Content "$env:LOCALAPPDATA\SaraeServer\logs\watchdog.log" -Tail 50 -Wait
```

| Log | Nội dung |
|---|---|
| `logs\watchdog.log` | Mọi việc watchdog làm: health lỗi, bật/tắt, cảnh báo đã gửi. Xoay vòng 5 MB × 5 file (`.1` ... `.5`). |
| `logs\gateway.err.log` | Log của uvicorn/gateway (uvicorn ghi vào stderr). Mỗi lần gateway được bật lại, log lần trước thành `.1`, giữ 5 lần. |
| `logs\gateway.out.log` | stdout của gateway (thường trống). |
| `logs\cloudflared.log` | Log tunnel: kết nối tới Cloudflare, lỗi gọi gateway. Xoay vòng khi quá 10 MB lúc khởi động. |
| `logs\backup.log` | Kết quả sao lưu mỗi ngày. |
| `F:\Coder\sarae-voice-server\data\logs\supervisor.log`, `server.log` | Log của voice server (do chính voice server ghi và xoay vòng). |

Lỗi thường gặp:

- `Gateway: không khởi động được: thiếu backend\app\gateway_main.py`: chưa merge nhánh gateway vào thư mục chính.
- `Cloudflare Tunnel: không khởi động được: thiếu file cấu hình tunnel`: chưa làm mục 2.5, hoặc file nằm chỗ khác (đặt `SARAE_CLOUDFLARED_CONFIG`).
- `cổng/tiến trình đang do PID ... giữ, không phải watchdog khởi động`: có bản chạy tay đang bị lỗi. Tự tắt cửa sổ đó; watchdog sẽ bật bản của nó.
- `Khách không vào được api.puns.id.vn dù ... đều OK`: xem `cloudflared.log`, trang Zero Trust/Tunnels trên Cloudflare, và bản ghi DNS `api`.

## 7. Bảo trì: tạm dừng watchdog

Muốn tự tay dừng một thành phần (cập nhật code, khôi phục DB) mà watchdog không bật lại ngay:

```powershell
New-Item -ItemType File "$env:LOCALAPPDATA\SaraeServer\maintenance" -Force
```

Watchdog vẫn chạy nhưng không kiểm tra, không bật lại gì. Làm xong thì xóa file; trong khoảng 30 giây watchdog bật lại những gì đang tắt:

```powershell
Remove-Item "$env:LOCALAPPDATA\SaraeServer\maintenance"
```

Cập nhật code gateway: bật bảo trì, dừng gateway (xem mục 9), `git pull`, cài lại dependency nếu cần, rồi xóa file bảo trì.

## 8. Sao lưu

Task **Sarae Server Backup** chạy mỗi ngày. Chạy tay (xem trước bằng `-WhatIf`):

```powershell
.\scripts\server\backup-db.ps1 -WhatIf
```

```powershell
.\scripts\server\backup-db.ps1
```

- Sao lưu `backend\data\identity.sqlite3` (máy, license) và `backend\data\studio_gateway.sqlite3` (usage, hàng đợi) bằng `sqlite3.Connection.backup()` của Python: an toàn khi gateway đang ghi, không copy file thô. Sau đó chạy `PRAGMA integrity_check` trên bản sao.
- Mỗi lần là một thư mục `backups\yyyyMMdd-HHmmss\` (kèm `manifest.json`). Thư mục chỉ được đổi sang tên này khi mọi DB đều xong, nên thư mục nào có tên đó cũng dùng được.
- Giữ 14 bản mới nhất (`-Keep 30` để giữ nhiều hơn). Bản cũ chỉ bị xóa khi lần sao lưu mới thành công.
- Lỗi thì ghi `logs\backup.log` và gửi Telegram.
- Nên đặt `SARAE_BACKUP_DIR` sang ổ khác hoặc thư mục OneDrive/Google Drive, để hỏng ổ cứng không mất cả DB lẫn bản sao lưu. Cũng nên sao lưu riêng `%USERPROFILE%\.cloudflared\` (chứng chỉ và file `<UUID>.json` của tunnel) và `F:\Coder\sarae-voice-server\.env`.

## 9. Khôi phục bản sao lưu

Ví dụ khôi phục `identity.sqlite3` từ bản `20261128-033000`. Khách sẽ mất kết nối gateway trong lúc làm (Studio vẫn LIVE được, TTS chuyển sang giọng Edge).

1. Bật bảo trì để watchdog không bật lại gateway:

   ```powershell
   New-Item -ItemType File "$env:LOCALAPPDATA\SaraeServer\maintenance" -Force
   ```

2. Dừng gateway do watchdog bật (chỉ đúng PID đó):

   ```powershell
   $g = Get-Content "$env:LOCALAPPDATA\SaraeServer\state\gateway.json" | ConvertFrom-Json; taskkill /PID $g.pid /T /F
   ```

   Nếu bạn chạy gateway bằng tay (`start-server.cmd`) thì đóng cửa sổ đó. Kiểm tra cổng 8000 đã trống: `Get-NetTCPConnection -LocalPort 8000 -State Listen` không in ra gì.

3. Cất DB hiện tại (kèm file `-wal`, `-shm` nếu có) sang một thư mục riêng. Không được để lại file `-wal` cũ cạnh DB khôi phục, SQLite sẽ áp nó lên DB mới và làm hỏng dữ liệu:

   ```powershell
   $data = 'F:\Coder\sarae_live_studio\backend\data'
   $keep = Join-Path $data ('before-restore-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
   New-Item -ItemType Directory $keep | Out-Null
   Move-Item "$data\identity.sqlite3*" $keep
   ```

4. Chép bản sao lưu vào:

   ```powershell
   Copy-Item "$env:LOCALAPPDATA\SaraeServer\backups\20261128-033000\identity.sqlite3" $data
   ```

   (Nếu đã đặt `SARAE_BACKUP_DIR` thì lấy từ thư mục đó.)

5. Kiểm tra bản vừa chép:

   ```powershell
   & 'F:\Coder\sarae_live_studio\backend\.venv\Scripts\python.exe' -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0])" "$data\identity.sqlite3"
   ```

   Phải in ra `ok`.

6. Tắt bảo trì; watchdog bật lại gateway trong khoảng 30 giây:

   ```powershell
   Remove-Item "$env:LOCALAPPDATA\SaraeServer\maintenance"
   ```

7. Kiểm tra `https://api.puns.id.vn/api/health`, rồi thử kích hoạt / đăng nhập từ một máy khách. Giữ thư mục `before-restore-...` vài ngày; khi chắc chắn mọi thứ ổn thì tự tay xóa.

Khôi phục `studio_gateway.sqlite3` cũng làm như vậy, thay tên file.

## 10. Gỡ autostart

```powershell
.\scripts\server\uninstall-autostart.ps1 -WhatIf
```

```powershell
.\scripts\server\uninstall-autostart.ps1
```

Mặc định chỉ dừng và gỡ 2 task: voice server, gateway và cloudflared đang chạy vẫn chạy tiếp (khách không bị ngắt), chỉ là không còn ai bật lại khi chúng chết. Muốn dừng luôn (voice server được dừng êm):

```powershell
.\scripts\server\uninstall-autostart.ps1 -StopServer
```

Log, state và bản sao lưu được giữ nguyên trong `%LOCALAPPDATA%\SaraeServer\`.
