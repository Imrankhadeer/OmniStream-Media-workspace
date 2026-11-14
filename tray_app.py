import os
import sys
import time
import threading
import webbrowser
import urllib.request
import ctypes
from pathlib import Path
from PIL import Image
import pystray
from pystray import MenuItem as item, Menu

# Application paths
BASE_DIR = Path(__file__).resolve().parent
ICON_PATH = BASE_DIR / "static" / "icon.png"
if not ICON_PATH.exists():
    ICON_PATH = BASE_DIR / "icon.png"

# Windows API constants
SW_HIDE = 0
SW_SHOW = 5
MB_YESNO = 0x00000004
MB_ICONQUESTION = 0x00000020
MB_ICONINFORMATION = 0x00000040
IDYES = 6

console_visible = False
hwnd = ctypes.windll.kernel32.GetConsoleWindow() if sys.platform == "win32" else None

def hide_console():
    global console_visible
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)
        console_visible = False

def show_console():
    global console_visible
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, SW_SHOW)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        console_visible = True

def toggle_console(icon, item):
    global console_visible
    if console_visible:
        hide_console()
    else:
        show_console()

def open_browser():
    webbrowser.open("http://127.0.0.1:8000")

def open_downloads_folder():
    from downloader import get_download_dir
    d_dir = get_download_dir()
    if os.path.exists(d_dir):
        if sys.platform == "win32":
            os.startfile(d_dir)

def check_for_updates():
    try:
        import updater
        res = updater.check_updates()
        msg = res.get("message", "OmniStream is up to date.")
        if res.get("ytdlp_update_available"):
            msg += f"\n\nNew yt-dlp version available: {res.get('latest_ytdlp_version')}"
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(0, msg, "OmniStream Updates", MB_ICONINFORMATION)
    except Exception as e:
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(0, f"Error checking updates: {e}", "OmniStream Updates", 0)

def exit_app(icon, item):
    if sys.platform == "win32":
        ret = ctypes.windll.user32.MessageBoxW(
            0,
            "Are you sure you want to stop OmniStream?\n\nAny active downloads will be stopped.",
            "Exit OmniStream",
            MB_YESNO | MB_ICONQUESTION
        )
        if ret != IDYES:
            return

    # Graceful shutdown
    icon.stop()
    os._exit(0)

def wait_for_server_and_open():
    """Poll the backend API until it is 100% ready before opening the browser.
    Completely eliminates 'This site can't be reached' error!"""
    url = "http://127.0.0.1:8000/api/system_status"
    for _ in range(60):  # up to 15 seconds
        time.sleep(0.25)
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    open_browser()
                    return
        except Exception:
            pass
    # Fallback open
    open_browser()

def run_uvicorn():
    import uvicorn
    from app import app
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

def main():
    # 1. Hide terminal by default so app goes directly into system tray
    hide_console()

    # 2. Start Uvicorn in background thread
    server_thread = threading.Thread(target=run_uvicorn, daemon=True)
    server_thread.start()

    # 3. Start browser readiness monitor
    launch_thread = threading.Thread(target=wait_for_server_and_open, daemon=True)
    launch_thread.start()

    # 4. Load icon
    try:
        tray_image = Image.open(str(ICON_PATH))
    except Exception:
        tray_image = Image.new('RGB', (64, 64), color=(195, 244, 120))

    # 5. Define Tray Menu
    def get_console_label(item):
        return "Hide Terminal Console" if console_visible else "Show Terminal Console"

    menu = Menu(
        item("Open OmniStream", lambda icon, item: open_browser(), default=True),
        item(get_console_label, toggle_console),
        item("Open Downloads Folder", lambda icon, item: open_downloads_folder()),
        Menu.SEPARATOR,
        item("Check for Updates…", lambda icon, item: check_for_updates()),
        Menu.SEPARATOR,
        item("Exit OmniStream", exit_app)
    )

    tray_icon = pystray.Icon("OmniStream", tray_image, "OmniStream — Media Workstation", menu)
    tray_icon.run()

if __name__ == "__main__":
    main()
