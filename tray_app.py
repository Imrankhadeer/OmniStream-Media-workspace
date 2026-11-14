import os
import sys
import time
import threading
import webbrowser
import urllib.request
import ctypes
from ctypes import wintypes
from pathlib import Path
from PIL import Image
import pystray
from pystray import MenuItem as item, Menu

# 1. Immediately set explicit AppUserModelID so Windows identifies the process as OmniStream
if sys.platform == "win32":
    APP_ID = "OmniStream.Workstation.App.2.1"
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass

# Application paths
BASE_DIR = Path(__file__).resolve().parent
ICON_PNG = BASE_DIR / "static" / "icon.png"
ICON_ICO = BASE_DIR / "static" / "icon.ico"

# Windows API constants
SW_HIDE = 0
SW_SHOW = 5
MB_YESNO = 0x00000004
MB_ICONQUESTION = 0x00000020
MB_ICONINFORMATION = 0x00000040
IDYES = 6

console_visible = False
hwnd = ctypes.windll.kernel32.GetConsoleWindow() if sys.platform == "win32" else None
owner_hwnd = None

def setup_branding():
    """Configure OmniStream title, icon, and taskbar branding on all Windows windows."""
    global owner_hwnd, hwnd
    if sys.platform != "win32":
        return

    ico_path = str(ICON_ICO) if ICON_ICO.exists() else None

    # Brand console window if one is currently attached
    if hwnd:
        ctypes.windll.kernel32.SetConsoleTitleW("OmniStream — Terminal Console")
        if ico_path:
            h_icon = ctypes.windll.user32.LoadImageW(None, ico_path, 1, 0, 0, 0x10 | 0x40)
            if h_icon:
                ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, h_icon)  # ICON_SMALL
                ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, h_icon)  # ICON_BIG

    # Create hidden top-level owner window with OmniStream identity
    try:
        h_icon = None
        if ico_path:
            h_icon = ctypes.windll.user32.LoadImageW(None, ico_path, 1, 0, 0, 0x10 | 0x40)

        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        def _wnd_proc(h, m, w, l):
            return ctypes.windll.user32.DefWindowProcW(h, m, w, l)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ('cbSize', wintypes.UINT),
                ('style', wintypes.UINT),
                ('lpfnWndProc', WNDPROC),
                ('cbClsExtra', ctypes.c_int),
                ('cbWndExtra', ctypes.c_int),
                ('hInstance', wintypes.HINSTANCE),
                ('hIcon', wintypes.HICON),
                ('hCursor', wintypes.HICON),
                ('hbrBackground', wintypes.HBRUSH),
                ('lpszMenuName', wintypes.LPCWSTR),
                ('lpszClassName', wintypes.LPCWSTR),
                ('hIconSm', wintypes.HICON)
            ]

        cls_name = "OmniStreamMasterWindowClass"
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = WNDPROC(_wnd_proc)
        wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc.lpszClassName = cls_name
        wc.hIcon = h_icon
        wc.hIconSm = h_icon
        ctypes.windll.user32.RegisterClassExW(ctypes.byref(wc))

        owner_hwnd = ctypes.windll.user32.CreateWindowExW(
            0, cls_name, "OmniStream", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
        )
    except Exception:
        owner_hwnd = None

def hide_console():
    global console_visible, hwnd
    if sys.platform != "win32":
        return
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)
        console_visible = False

def show_console():
    global console_visible, hwnd
    if sys.platform != "win32":
        return
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if not hwnd:
        # If running under pythonw.exe, dynamically allocate terminal console on demand
        ctypes.windll.kernel32.AllocConsole()
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        # Re-redirect standard streams to the allocated console
        try:
            sys.stdout = open('CONOUT$', 'w', encoding='utf-8', buffering=1)
            sys.stderr = open('CONOUT$', 'w', encoding='utf-8', buffering=1)
        except Exception:
            pass

    if hwnd:
        ctypes.windll.kernel32.SetConsoleTitleW("OmniStream — Terminal Console")
        ico_path = str(ICON_ICO) if ICON_ICO.exists() else None
        if ico_path:
            h_icon = ctypes.windll.user32.LoadImageW(None, ico_path, 1, 0, 0, 0x10 | 0x40)
            if h_icon:
                ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, h_icon)
                ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, h_icon)

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
    def _do_check():
        try:
            import updater
            res = updater.check_updates()
            msg = res.get("message", "OmniStream is up to date.")
            if res.get("ytdlp_update_available"):
                msg += f"\n\nNew yt-dlp version available: {res.get('latest_ytdlp_version')}"
            if sys.platform == "win32":
                ctypes.windll.user32.MessageBoxW(
                    owner_hwnd or 0,
                    msg,
                    "OmniStream Updates",
                    MB_ICONINFORMATION | 0x00040000 | 0x00010000
                )
        except Exception as e:
            if sys.platform == "win32":
                ctypes.windll.user32.MessageBoxW(
                    owner_hwnd or 0,
                    f"Error checking updates: {e}",
                    "OmniStream Updates",
                    0x00040000 | 0x00010000
                )
    threading.Thread(target=_do_check, daemon=True).start()

def exit_app(icon, item):
    def _do_exit():
        if sys.platform == "win32":
            ret = ctypes.windll.user32.MessageBoxW(
                owner_hwnd or 0,
                "Are you sure you want to stop OmniStream?\n\nAny active downloads will be stopped.",
                "Exit OmniStream",
                MB_YESNO | MB_ICONQUESTION | 0x00040000 | 0x00010000
            )
            if ret != IDYES:
                return

        try:
            icon.stop()
        except Exception:
            pass
        os._exit(0)

    threading.Thread(target=_do_exit, daemon=True).start()

def wait_for_server_and_open():
    """Poll backend API until it is 100% online before opening browser."""
    url = "http://127.0.0.1:8000/api/system_status"
    for _ in range(60):
        time.sleep(0.25)
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    open_browser()
                    return
        except Exception:
            pass
    open_browser()

def run_uvicorn():
    import uvicorn
    from app import app
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

def main():
    # 1. Apply Windows branding & hide initial console
    setup_branding()
    hide_console()

    # 2. Start Uvicorn in background thread
    server_thread = threading.Thread(target=run_uvicorn, daemon=True)
    server_thread.start()

    # 3. Start browser readiness monitor
    launch_thread = threading.Thread(target=wait_for_server_and_open, daemon=True)
    launch_thread.start()

    # 4. Load icon
    try:
        tray_image = Image.open(str(ICON_PNG))
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
