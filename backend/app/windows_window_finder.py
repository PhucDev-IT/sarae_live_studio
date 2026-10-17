"""Identify TikTok LIVE Studio windows by process identity, not title alone."""
import os

# TikTok has used more than one packaging name across releases.
DEFAULT_NAMES = ("tiktok live studio", "tiktoklivestudio", "tiktok_live_studio")

def is_studio_executable(name: str, extra_names: str = "") -> bool:
    normalized = name.lower().replace("-", "").replace("_", "").replace(" ", "")
    allowed = tuple(v.strip().lower().replace("-", "").replace("_", "").replace(" ", "")
                    for v in (",".join(DEFAULT_NAMES)+","+extra_names).split(",") if v.strip())
    return normalized.endswith(".exe") and normalized[:-4] in allowed

def find_studio_window(win32gui, *, preferred_pid: int | None = None):
    import win32process
    import psutil
    candidates = []
    def inspect(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        if win32gui.GetWindow(hwnd, 4):  # GW_OWNER: ignore owned popups
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid)
            name = process.name()
            path = process.exe()
            if not is_studio_executable(name, os.getenv("SARAE_TIKTOK_EXE_NAMES", "")):
                return
            title = win32gui.GetWindowText(hwnd)
            if preferred_pid and pid != preferred_pid:
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            area = max(0, right-left)*max(0, bottom-top)
            if area < 120*100:
                return
            candidates.append((area, {"hwnd": int(hwnd), "pid": int(pid),
                                      "exe_name": name, "exe_path": path,
                                      "title": title, "minimized": bool(win32gui.IsIconic(hwnd))}))
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, RuntimeError):
            return
    win32gui.EnumWindows(inspect, None)
    if not candidates:
        return None
    return max(candidates, key=lambda row: (not row[1]["minimized"],row[0]))[1]
