# OmniStream Downloader

A lightweight, modern local web application to download videos and HLS (`.m3u8`) streams with custom header support.

---

### Features
1. **Universal Video Downloader:** Supports YouTube, Twitter/X, Reddit, TikTok, Vimeo, and hundreds of tube sites via `yt-dlp`.
2. **HLS / Stream Downloader with Custom Headers:** Handles `.m3u8` playlists and encrypted AES-128 streams (such as those from MissAV, JAV portals, or embedded players) by automatically injecting required `Referer`, `Origin`, and `User-Agent` headers.
3. **Automatic Remuxing:** Uses FFmpeg to merge audio and video fragments into clean `.mp4` or `.mp3` files.
4. **Live Progress Dashboard:** Real-time download percentage, download speed (MB/s), and ETA.
5. **Direct Library Access:** One-click button to open your local `downloads/` folder in Windows Explorer.

---

### How to Run

Simply double-click:
```cmd
run.bat
```
This automatically launches the local server and opens the dashboard at `http://127.0.0.1:8000`.

---

### How to Download from Stream Sites (e.g. MissAV / JAV Portals)

1. Open the video page in your browser (Chrome/Edge/Firefox).
2. Press `F12` to open **Developer Tools** and switch to the **Network** tab.
3. In the filter box, type: `m3u8`.
4. Click play on the video player. You will see a playlist link appear (e.g. `playlist.m3u8` or `index.m3u8`).
5. Right-click the `.m3u8` request -> **Copy** -> **Copy URL**.
6. Paste the URL into **OmniStream**.
7. The app will auto-fill the **Referer** header with the source website domain (or you can paste the webpage URL into the Referer field).
8. Click **Start Download**. The app will download the chunks, decrypt them, and assemble them into a finished `.mp4`.
