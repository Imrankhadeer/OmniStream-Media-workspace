import os
import sys
import subprocess
import shutil
import urllib.request
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def get_python_exe():
    venv_py = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable

def check_updates():
    """Check if updates are available via git or yt-dlp version."""
    results = {
        "git_available": False,
        "app_updates_available": False,
        "ytdlp_update_available": False,
        "current_commit": "unknown",
        "latest_commit": "unknown",
        "message": ""
    }
    
    # 1. Check Git status
    try:
        proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=5)
        if proc.returncode == 0:
            results["git_available"] = True
            results["current_commit"] = proc.stdout.strip()
            
            # Fetch remote without modifying working tree
            fetch = subprocess.run(["git", "fetch", "--dry-run"], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=10)
            status = subprocess.run(["git", "status", "-uno"], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=5)
            if "behind" in status.stdout.lower():
                results["app_updates_available"] = True
                results["message"] = "A newer version of OmniStream is available on GitHub."
            else:
                results["message"] = "OmniStream is up to date."
    except Exception as e:
        results["message"] = f"Update check: {e}"

    # 2. Check yt-dlp version vs PyPI
    try:
        import yt_dlp
        current_ytdlp = yt_dlp.version.__version__
        results["ytdlp_version"] = current_ytdlp
        
        req = urllib.request.Request("https://pypi.org/pypi/yt-dlp/json", headers={"User-Agent": "OmniStream-Updater"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            latest_ytdlp = data["info"]["version"]
            results["latest_ytdlp_version"] = latest_ytdlp
            if latest_ytdlp != current_ytdlp:
                results["ytdlp_update_available"] = True
    except Exception:
        pass

    return results

def perform_update(update_app: bool = True, update_ytdlp: bool = True):
    """Perform self-update without losing downloads, databases, or cookies."""
    logs = []
    py_exe = get_python_exe()

    # 1. Update yt-dlp first (critical for YouTube/X scraper compatibility)
    if update_ytdlp:
        try:
            logs.append("Updating yt-dlp extraction engine...")
            res = subprocess.run(
                [py_exe, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=60
            )
            if res.returncode == 0:
                logs.append("Successfully updated yt-dlp to latest version!")
            else:
                logs.append(f"yt-dlp update notice: {res.stderr.strip() or res.stdout.strip()}")
        except Exception as e:
            logs.append(f"Could not update yt-dlp: {e}")

    # 2. Pull git repository updates
    if update_app:
        try:
            logs.append("Checking for application code updates via Git...")
            res = subprocess.run(
                ["git", "pull", "--rebase"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=45
            )
            logs.append(res.stdout.strip() if res.stdout else res.stderr.strip())
        except Exception as e:
            logs.append(f"Git pull: {e}")

    return {
        "status": "completed",
        "logs": logs
    }

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "check"
    if action == "check":
        print(json.dumps(check_updates(), indent=2))
    elif action == "update":
        print(json.dumps(perform_update(), indent=2))
