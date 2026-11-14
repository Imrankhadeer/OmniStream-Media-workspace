# OmniStream — Modern Media Workspace

A fast, lightweight, and fully asynchronous media downloading, transcription, and processing workstation for desktop and laptop environments.

---

## Overview

**OmniStream** is an all-in-one local media application combining high-speed streaming downloads, intelligent concurrency queueing, AI speech-to-text transcription, media compression, format conversion, and an organized local media gallery.

Designed desktop-first with responsive support for compact laptop displays, OmniStream operates completely locally with no external cloud dependencies for processing.

---

## Key Features

### 1. Universal Media Downloader
- **Multi-Platform Support**: Seamlessly download from YouTube, X / Twitter, Reddit, ClipMotion, TikTok, Vimeo, Twitch, and generic HTTP/HLS sources.
- **Visual Format Selector**: Automatically inspects source links and offers clean resolution options (360p, 720p HD, 1080p Full HD, 2K QHD, 4K UHD, and Audio-Only MP3).
- **HLS & Stream Remuxing**: High-speed fragment retrieval with automatic FFmpeg assembly into clean `.mp4` or `.mp3` outputs.

### 2. Smart Queue & Asynchronous Multitasking
- **Concurrent Processing**: Perform downloads, transcriptions, and file compressions simultaneously without blocking the user interface.
- **Smart Concurrency Controller**: Automatically balances network bandwidth and system resources:
  - Runs up to **2 heavy video streams** in parallel for maximum line speed.
  - Bypasses queue for lightweight audio streams (up to 4 concurrent tasks).
  - Queues additional heavy downloads with live position indicators (`Queued (#1 in line)`).
  - Automatically begins the next waiting task the moment an active download finishes.
  - Includes a manual **`▶ Start Now`** bypass button for immediate priority execution.

### 3. Precision Portion Trimming
- **Integrated Time Selectors**: Set exact start and end timestamps using dedicated `HH:MM:SS` numeric spinner inputs.
- **Keyframe-Accurate Cuts**: Leverages stream keyframe cutting via `yt-dlp` and FFmpeg `-ss`/`-t` flags to download only the desired portion, saving bandwidth and disk space.

### 4. AI Speech-to-Text & Subtitle Generator
- **Local Whisper Engine**: Powered by `faster-whisper` (`tiny`/`base` with `int8` quantization) for fast, low-CPU transcription.
- **Input Flexibility**: Transcribe uploaded local audio/video files or direct web links.
- **Standard Output**: Generates cleanly formatted `.srt` subtitle files ready for video players or editors.

### 5. Media Compression Suite
- **Continuous Compression Slider**: Adjust output quality from 5% (ultra-high visual fidelity) to 95% (maximum space saving).
- **Live Size Estimation**: Instant real-time preview of projected output file size and estimated percentage saved.
- **Format Support**: Optimizes MP4, WebM, MKV videos, MP3/WAV/FLAC audio, and PNG/JPEG images using tuned FFmpeg CRF and bitrate presets.

### 6. Universal Format Converter
- **Cross-Format Transcoding**: Convert between MP4, MKV, WebM, MOV, MP3, WAV, FLAC, M4A, OPUS, and animated GIF.
- **Audio Stripping & Normalization**: Built-in broadcast-standard audio normalization (`loudnorm`) or audio stripping options.

### 7. Media Library & Visual Gallery
- **Categorized Browser**: Filter your library by All, Videos, Audio, Subtitles (`.srt`), and Images.
- **Visual Fallback Cards**: Custom category-themed visual badges with SVG iconography for subtitle files, audio tracks, and images without thumbnails.
- **Direct Playback & Streaming**: In-browser video player dialog and instant one-click downloads.

### 8. In-Memory Session & Cookie Manager
- **Safe Authentication**: Paste session cookies for protected or rate-limited sources.
- **Memory-Safe Security**: Cookies are held safely in browser memory and wiped upon session close, with optional persistent storage to disk.

---

## User Interface & Navigation

- **Activity Dashboard**: Central real-time monitoring panel displaying all downloads, transcriptions, and media conversions with live progress bars, speed, ETA, and cancellation controls.
- **Sidebar Organization**:
  1. `↧` **Downloads** (`Alt + 1`): Main single & batch downloader.
  2. `≋` **Transcription** (`Alt + 2`): AI speech-to-text subtitle generator.
  3. `⚙` **Tools** (`Alt + 3`): Compression & format conversion studio.
  4. `◷` **Activity** (`Alt + 4`): Real-time queue and task progress tracker.
  5. `▦` **Library** (`Alt + 5`): Completed media file explorer.

---

## Quick Start

### Prerequisites
- Python 3.10+
- FFmpeg installed and available in system PATH

### Running on Windows
Simply run the included startup script:
```cmd
run.bat
```
This activates the local environment, starts the Uvicorn application server, and opens OmniStream in your default browser at:
```
http://127.0.0.1:8000
```

### Manual Execution
```bash
# Activate virtual environment
.venv\Scripts\activate   # Windows
source .venv/bin/activate # Linux/macOS

# Start server
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

---

## Tech Stack

- **Backend**: FastAPI, Uvicorn, Python 3
- **Engines**: `yt-dlp`, FFmpeg, `faster-whisper`, `aria2c`
- **Database**: SQLite with WAL mode (`db.py`)
- **Frontend**: Vanilla HTML5, Modern CSS (CSS Variables, Grid, Flexbox), Vanilla JavaScript (No heavy frameworks, fast load times)
