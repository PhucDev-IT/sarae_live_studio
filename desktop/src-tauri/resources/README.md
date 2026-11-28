Generated folder (except this README). `scripts/fetch-ffmpeg.ps1` downloads the pinned FFmpeg
Windows build, verifies its SHA256 and writes `ffmpeg/ffmpeg.exe`, `ffmpeg/ffprobe.exe`,
`ffmpeg/LICENSE.txt` and `ffmpeg/VERSION.txt` here. Tauri installs them as `<install dir>/ffmpeg/`
(`bundle.resources` in `tauri.conf.json`). Never commit the binaries.
