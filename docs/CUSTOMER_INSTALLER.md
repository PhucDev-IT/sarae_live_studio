# Bộ cài Sarae Live Studio cho khách (Windows)

Tài liệu này dành cho người build và phát hành bộ cài. Khách chỉ cần chạy một file
`SaraeLiveStudio-Setup-<version>.exe`: **không cần Python, Node, Rust hay FFmpeg**.

Liên quan: [WINDOWS_AGENT_BUILD.md](WINDOWS_AGENT_BUILD.md) (máy build, lệnh dev),
[HYBRID_CLOUD_AGENT.md](HYBRID_CLOUD_AGENT.md) (mô hình client/server).

## 1. Bộ cài gồm những gì

```text
desktop\scripts\build-windows.bat
  1. fetch-ffmpeg.ps1      FFmpeg 9.0.2 essentials (gyan.dev), kiểm SHA256 đã ghim
                           -> desktop\src-tauri\resources\ffmpeg\{ffmpeg.exe, ffprobe.exe, LICENSE.txt, VERSION.txt}
  2. build-sidecar.bat     PyInstaller one-file, SARAE_BUILD_CHANNEL=customer
                           -> desktop\src-tauri\binaries\sarae-agent-core-<triple>.exe
  3. kiểm --self-check     build_profile.channel = customer, server_url đúng
  4. npm ci + tauri build  NSIS per-user
                           -> desktop\release\SaraeLiveStudio-Setup-<version>.exe (+ .sha256)
```

| Trên máy khách | Vị trí |
|---|---|
| Ứng dụng (cài cho riêng user, không hỏi quyền admin) | `%LOCALAPPDATA%\Sarae Live Studio\` (mặc định của Tauri, khách đổi được lúc cài) |
| GUI | `SaraeAgent.exe` |
| Sidecar Python đã đóng gói | `sarae-agent-core.exe` |
| FFmpeg / FFprobe (+ giấy phép GPL) | `ffmpeg\ffmpeg.exe`, `ffmpeg\ffprobe.exe`, `ffmpeg\LICENSE.txt` |
| **Dữ liệu Studio** (SQLite, video, sản phẩm, cấu hình AI, khóa) | `%LOCALAPPDATA%\SaraeLiveStudio\` |
| Dữ liệu WebView2 của giao diện | `%LOCALAPPDATA%\vn.sarae.agent\` |
| Thư viện Python tạm của sidecar | `%TEMP%\_MEI*`, tự dọn khi Core thoát |

Dữ liệu Studio không bao giờ nằm cạnh file exe: `sarae_agent.studio.storage.data_root()` dùng
`SARAE_STUDIO_DATA_DIR` nếu có, không thì `%LOCALAPPDATA%\SaraeLiveStudio`.

**Gỡ cài đặt không xóa dữ liệu.** Trình gỡ của Tauri chỉ xóa thư mục cài đặt. Ô "Xóa dữ liệu ứng
dụng" trong trình gỡ (nếu khách tích) chỉ xóa thư mục theo `identifier` (`vn.sarae.agent`, tức
cache/localStorage của WebView2), không đụng `SaraeLiveStudio`. Nếu sau này đổi `identifier` hay
đường dẫn dữ liệu, phải kiểm lại điều này.

### FFmpeg không nằm trong sidecar

Trước đây FFmpeg và FFprobe được nhét vào exe one-file của PyInstaller, nên mỗi lần mở app phải
giải nén khoảng 200 MB vào `%TEMP%`. Giờ chúng là *resource* của Tauri, cài vào
`<thư mục cài>\ffmpeg\`. Khi chạy bản đóng gói, `sarae_agent.bundled`:

- tìm `ffmpeg.exe`/`ffprobe.exe` ở `<thư mục cài>\ffmpeg\` trước, rồi mới đến `PATH`
  (`studio/service.py:probe_video`, `studio/media_thumbnails.py:resolve_ffmpeg`);
- thêm đúng thư mục `ffmpeg\` đó vào đầu `PATH` của tiến trình Core, nên những chỗ còn gọi
  `shutil.which("ffmpeg")` (giọng local, xoay video cho OBS) cũng tìm được. Thư mục giải nén
  `_MEI*` không bị đưa vào `PATH`, để Chrome hay OBS do Core mở không nạp nhầm DLL riêng của sidecar.

Khi chạy từ mã nguồn (`start-studio.cmd`), không có gì thay đổi: vẫn dùng FFmpeg trên `PATH`.

## 2. Cấu hình bản build cho khách

`agent-core/build/sarae-agent-core.spec` ghi file `sarae_agent/build_profile.json` vào trong exe:

| Biến lúc build | Mặc định | Ý nghĩa |
|---|---|---|
| `SARAE_BUILD_CHANNEL` | `dev` | `customer` = bản gửi khách. `build-windows.bat` luôn đặt `customer`. |
| `SARAE_BUILD_SERVER_URL` | `https://api.puns.id.vn` | Gateway kích hoạt/license/TTS. Bắt buộc `https://`, không có user/password/query. |

Lúc khởi động, sidecar có profile `customer` sẽ (`sarae_agent.bundled.apply_profile`):

- **xóa `SARAE_STUDIO_DEV_UNLICENSED`** khỏi môi trường, dù ai đặt nó trong Windows hay chạy
  `sarae-agent-core.exe` bằng tay;
- **đặt cứng `SARAE_STUDIO_SERVER_URL`** bằng URL đã ghim, ghi đè giá trị trong môi trường. Khách
  không trỏ app sang máy chủ khác được;
- nếu profile hỏng hoặc URL không hợp lệ: dừng hẳn với thông báo "hãy cài lại", không quay về
  cấu hình dev.

Sidecar `dev` (do `dev-windows.bat` / `start-studio.cmd native` build) không có các ràng buộc này,
để người phát triển vẫn dùng được `SARAE_STUDIO_DEV_UNLICENSED`. Lưu ý: `dev-windows.bat` dùng lại
sidecar đã có trong `src-tauri\binaries\`. Nếu vừa chạy `build-windows.bat` thì đó là bản
`customer`; muốn bản dev thì chạy `dev-windows.bat --rebuild-core`.

`SARAE_STUDIO_VOICE_MODE` **không** được ghim trong profile. Mặc định của nó do
`studio/voice_server.py` (nhánh `feat/hybrid-cloud-server`) quyết định. Nếu bản cho khách cần ép
một chế độ giọng, hãy thêm vào `build_profile.json` và `apply_profile` cùng kiểu với server URL.

## 3. Build

Máy build cần: Windows x64, Python 3.11–3.13, Node 20+, Rust (MSVC) và Visual Studio Build Tools
(xem WINDOWS_AGENT_BUILD.md mục 2). Không cần cài FFmpeg trên máy build nữa.

> ⚠️ Build tốn nhiều CPU (PyInstaller, `cargo` LTO). **Không build trên máy đang LIVE**: OBS sẽ giật.

```powershell
cd desktop
scripts\build-windows.bat
```

Kết quả nằm ở `desktop\release\SaraeLiveStudio-Setup-<version>.exe` và file `.sha256` đi kèm.
Gửi khách cả hai file, hoặc đăng mã SHA256 ở nơi khách tải về.

Các lệnh riêng lẻ:

| Việc | Lệnh |
|---|---|
| Chỉ tải/kiểm FFmpeg | `powershell -NoProfile -ExecutionPolicy Bypass -File desktop\scripts\fetch-ffmpeg.ps1` |
| Máy build không có mạng | Chép zip `ffmpeg-9.0.2-essentials_build.zip` vào, rồi `fetch-ffmpeg.ps1 -Zip <đường dẫn zip>` (vẫn kiểm SHA256) |
| Tải lại dù đã có | `fetch-ffmpeg.ps1 -Force` |
| Build sidecar khách để thử | `set SARAE_BUILD_CHANNEL=customer` rồi `desktop\scripts\build-sidecar.bat` |
| Gateway khác (ví dụ staging) | `set SARAE_BUILD_SERVER_URL=https://staging.example.com` trước `build-windows.bat` |

### FFmpeg: nguồn, checksum, giấy phép

- FFmpeg không phát hành bản Windows chính thức. Trang ffmpeg.org trỏ tới gyan.dev và BtbN. Ta dùng
  bản `essentials` của gyan.dev: đó cũng là bản `9.0.2` đang chạy trên máy dev hiện tại.
- SHA256 `60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba`, 114 768 076 byte. Hai
  nguồn khớp nhau: file `.sha256` trên gyan.dev, và trường `digest` của asset trên GitHub release
  `GyanD/codexffmpeg` 9.0.2. Script tải từ gyan.dev, lỗi thì tải từ GitHub. Sai checksum là dừng.
- Muốn nâng phiên bản: sửa `$Version`, `$Sha256`, `$Size` trong `fetch-ffmpeg.ps1`, và đối chiếu
  đủ hai nguồn như trên trước khi commit.
- Bản essentials dùng **GPLv3**. Bộ cài đã kèm `ffmpeg\LICENSE.txt`. Phát hành cho khách là *phân
  phối lại*, nên người phát hành cần tự kiểm tra nghĩa vụ GPL, nhất là việc cung cấp mã nguồn
  tương ứng (ví dụ ghi link tới mã nguồn FFmpeg 9.0.2 và trang build của gyan.dev trong mục
  "Giới thiệu" hoặc trang tải). Tài liệu này không thay cho tư vấn pháp lý.

## 4. Ký mã (Authenticode): tùy chọn, nên có

Bộ cài chưa ký vẫn chạy được, nhưng Windows SmartScreen sẽ báo "Windows protected your PC" (khách
phải bấm *More info → Run anyway*). Một số phần mềm diệt virus cũng hay báo nhầm exe PyInstaller
chưa ký.

1. Mua chứng thư ký mã (OV/EV) hoặc dùng Azure Trusted Signing. **Người dùng tự làm**, không đưa
   khóa riêng hay file `.pfx` vào repo.
2. Cài chứng thư vào kho chứng thư của máy build (hoặc dùng USB token/HSM theo hướng dẫn của nhà
   cung cấp), rồi lấy thumbprint:
   `Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Format-List Subject, Thumbprint`
3. Chép `desktop\src-tauri\tauri.signing.example.json` thành `desktop\src-tauri\tauri.signing.json`
   (đã gitignore) rồi điền thumbprint. Nếu dùng Azure Trusted Signing hoặc công cụ khác, thay ba
   khóa đó bằng `"signCommand": "<lệnh ký> %1"`.
4. Build: `set SARAE_SIGN_CONFIG=%CD%\src-tauri\tauri.signing.json` rồi `scripts\build-windows.bat`.
   Script sẽ truyền `--config` cho `tauri build`.
5. Kiểm tra: `Get-AuthenticodeSignature desktop\release\SaraeLiveStudio-Setup-*.exe`. Sau khi cài,
   kiểm thêm `SaraeAgent.exe` và `sarae-agent-core.exe`. Nếu sidecar chưa được ký, ký nó bằng
   `signtool` ngay sau `build-sidecar.bat` (trước `tauri build`).

## 5. Thứ tự kiểm tra trên một máy Windows sạch

Dùng một máy ảo Windows 10 22H2 hoặc Windows 11 mới cài, đăng nhập bằng **tài khoản thường (không
admin)**, có mạng. Không cài Python, Node hay FFmpeg.

1. **Máy thật sự sạch**: `where python`, `where node`, `where ffmpeg` đều báo không tìm thấy.
2. **Checksum bộ cài**: `Get-FileHash SaraeLiveStudio-Setup-<v>.exe` khớp với file `.sha256`.
3. **Cài**: chạy bộ cài. Không được hiện hộp thoại UAC (vì cài per-user). Nếu bộ cài chưa ký, sẽ
   có SmartScreen. Nếu máy thiếu WebView2, bộ cài tự tải về (cần mạng).
4. **File đã cài**: `%LOCALAPPDATA%\Sarae Live Studio\` có `SaraeAgent.exe`,
   `sarae-agent-core.exe`, `ffmpeg\ffmpeg.exe`, `ffmpeg\ffprobe.exe`, `ffmpeg\LICENSE.txt`.
5. **Tự kiểm sidecar**:
   `& "$env:LOCALAPPDATA\Sarae Live Studio\sarae-agent-core.exe" --self-check`. Phải thấy:
   - `"ok": true`
   - `build_profile.channel = "customer"` và `server_url = "https://api.puns.id.vn"`
   - `tools.ffmpeg`, `tools.ffprobe` trỏ vào `...\Sarae Live Studio\ffmpeg\`
   - `studio_data_root = ...\AppData\Local\SaraeLiveStudio`
6. **Mở app** từ Start Menu: Core báo sẵn sàng, màn kích hoạt gọi tới `https://api.puns.id.vn`
   (phần này phụ thuộc nhánh `feat/hybrid-cloud-server`).
7. **Không bỏ qua được license**: đóng app, chạy `setx SARAE_STUDIO_DEV_UNLICENSED 1`, đăng xuất và
   đăng nhập lại, mở app: vẫn phải đòi kích hoạt. Thử luôn `setx SARAE_STUDIO_SERVER_URL
   http://127.0.0.1:1`: app vẫn gọi `api.puns.id.vn`. Xong thì xóa hai biến đó.
8. **FFmpeg không cần PATH**: nhập một video MP4. Video phải được kiểm tra (FFprobe) và có ảnh thu
   nhỏ (FFmpeg). Nhập thử một video quay dọc có metadata xoay rồi phát qua OBS.
9. **OBS**: cài OBS 28+ và bật WebSocket. Kết nối, phát thử video và giọng đọc
   (WINDOWS_AGENT_BUILD.md mục 7).
10. **Thoát sạch**: đóng cửa sổ, chờ 20 giây. Task Manager không còn `sarae-agent-core.exe`.
11. **Dữ liệu nằm trong AppData**: có `%LOCALAPPDATA%\SaraeLiveStudio\studio.sqlite3`, và không có
    file dữ liệu nào trong thư mục cài.
12. **Cài đè bản mới hơn** (tăng `version`, build lại, cài lên trên): sản phẩm và video vẫn còn.
13. **Gỡ cài đặt** (Settings → Apps), thử cả hai trường hợp tích và không tích "xóa dữ liệu ứng
    dụng": thư mục cài biến mất, `%LOCALAPPDATA%\SaraeLiveStudio\` **vẫn còn**. Cài lại thì dữ liệu
    cũ hiện lại.
14. **Máy không có mạng**: Studio mở được, các tính năng local (video, OBS) chạy được. Kích
    hoạt/TTS báo lỗi rõ ràng, không bị treo.

## 6. Đề xuất: tự cập nhật bằng Tauri updater (chưa làm)

Đây chỉ là thiết kế. Chưa thêm plugin, chưa làm gì phía server, và **không có khóa thật nào được
tạo**.

### 6.1 Khóa ký bản cập nhật: người dùng tự tạo và tự cất

Tauri updater dùng khóa ký riêng (minisign), **khác** với chứng thư Authenticode ở mục 4. App đã cài
chỉ chấp nhận bản cập nhật ký bằng khóa riêng tương ứng với khóa công khai được nhúng trong app.

```powershell
cd desktop
npx tauri signer generate -w "$env:USERPROFILE\.tauri\sarae-updater.key"
```

- Lệnh này hỏi mật khẩu và tạo `sarae-updater.key` (khóa riêng) cùng `sarae-updater.key.pub`.
- Cất khóa riêng và mật khẩu trong trình quản lý mật khẩu, kèm một bản sao offline (USB đã mã
  hóa). **Không** commit, không để trong thư mục repo, không chép lên PC gateway, không gửi qua chat.
- Mất khóa riêng thì mọi máy đã cài sẽ không nhận được cập nhật nữa: khách phải tải bộ cài mới về
  cài tay. Lộ khóa thì người khác có thể đẩy bản cập nhật giả cho khách. Khi đó phải phát hành bản
  mới với khóa mới (cài tay).
- Lúc build: `set TAURI_SIGNING_PRIVATE_KEY=%USERPROFILE%\.tauri\sarae-updater.key` và
  `set TAURI_SIGNING_PRIVATE_KEY_PASSWORD=...`. Chỉ đặt trong cửa sổ build, không dùng `setx`.

### 6.2 Thay đổi phía client (khi quyết định làm)

1. `Cargo.toml`: `tauri-plugin-updater = "2"` (và `tauri-plugin-process` nếu cần khởi động lại).
   `lib.rs`: `.plugin(tauri_plugin_updater::Builder::new().build())`.
   `capabilities/default.json`: thêm `updater:default`.
2. `tauri.conf.json`:

   ```json
   "bundle": { "createUpdaterArtifacts": true },
   "plugins": {
     "updater": {
       "pubkey": "<nội dung file sarae-updater.key.pub>",
       "endpoints": ["https://api.puns.id.vn/updates/windows-x86_64/latest.json"],
       "windows": { "installMode": "passive" }
     }
   }
   ```

   `pubkey` là khóa công khai nên commit được. Endpoint bắt buộc `https`.
3. **Không bao giờ cập nhật khi đang LIVE.** App chỉ kiểm tra cập nhật khi không có phiên LIVE. Nếu
   có bản mới thì hỏi người dùng ("Cập nhật và khởi động lại?"), không tự cài. Trình cài NSIS sẽ đóng
   app, và khi đóng app thì Core dừng theo (`RunEvent::ExitRequested` → `core.stop`). Nếu Core không
   dừng, file `sarae-agent-core.exe` bị khóa và không ghi đè được.
4. `installMode: "passive"` hiện thanh tiến trình, không hỏi gì. Vì cài per-user nên không có UAC.
   Dữ liệu trong `%LOCALAPPDATA%\SaraeLiveStudio` không bị đụng tới.

### 6.3 Manifest tĩnh phía server

Khi `createUpdaterArtifacts` bật, `tauri build` tạo thêm
`Sarae Live Studio_<v>_x64-setup.exe.sig` cạnh bộ cài. Mỗi lần phát hành:

1. Tải `SaraeLiveStudio-Setup-<v>.exe` lên chỗ chứa tĩnh qua HTTPS: gateway
   (`/updates/files/...`), Cloudflare R2 hoặc GitHub Releases.
2. Tạo `latest.json`, trong đó `signature` là **nội dung** file `.sig`, không phải đường dẫn:

   ```json
   {
     "version": "0.3.1",
     "notes": "Sửa lỗi ... ",
     "pub_date": "2026-12-05T10:00:00Z",
     "platforms": {
       "windows-x86_64": {
         "signature": "<nội dung file .exe.sig>",
         "url": "https://api.puns.id.vn/updates/files/SaraeLiveStudio-Setup-0.3.1.exe"
       }
     }
   }
   ```

3. Đăng `latest.json` **sau cùng**, khi bộ cài đã tải lên xong. Muốn rút một bản lỗi thì trỏ
   `latest.json` về bản cũ hơn (updater chỉ cài khi `version` lớn hơn bản đang chạy).

Gateway chỉ cần trả hai file tĩnh, không xử lý logic. Nếu PC nhà tắt, app chỉ báo không kiểm tra
được cập nhật. Muốn kiểm tra cập nhật không phụ thuộc PC nhà thì đặt hai file này trên R2 hoặc
GitHub Releases.

## 7. Checklist phát hành

- [ ] Tăng `version` ở `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/Cargo.toml`,
      `desktop/package.json`, `agent-core/pyproject.toml`.
- [ ] Chạy test: `start-studio.cmd test`.
- [ ] Build trên máy **không đang LIVE**: `desktop\scripts\build-windows.bat` (có ký nếu có chứng thư).
- [ ] Chạy hết mục 5 trên máy sạch.
- [ ] Gửi `.exe` + `.sha256` (và cập nhật `latest.json` nếu đã bật updater).
