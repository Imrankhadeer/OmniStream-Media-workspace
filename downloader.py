import os
import sys
import re
import glob
import uuid
import time
import shutil
import threading
import subprocess
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import psutil
import yt_dlp
import db
from resolver import clean_url, format_friendly_error, resolve_page_stream, parse_hls_qualities, strip_ansi, DEFAULT_UA, http_get

DEFAULT_DOWNLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "downloads"))

def get_download_dir() -> str:
    """Retrieve persistent download directory from database, falling back to default."""
    saved = db.get_setting("download_dir")
    if saved and saved.strip():
        clean = os.path.abspath(os.path.expanduser(saved.strip().strip("'\"")))
        try:
            os.makedirs(clean, exist_ok=True)
            return clean
        except Exception:
            pass
    os.makedirs(DEFAULT_DOWNLOAD_DIR, exist_ok=True)
    return DEFAULT_DOWNLOAD_DIR

def set_download_dir(new_path: str) -> str:
    """Validate, create, and save custom persistent download directory."""
    if not new_path or not new_path.strip():
        db.set_setting("download_dir", DEFAULT_DOWNLOAD_DIR)
        return DEFAULT_DOWNLOAD_DIR
    clean = os.path.abspath(os.path.expanduser(new_path.strip().strip("'\"")))
    os.makedirs(clean, exist_ok=True)
    db.set_setting("download_dir", clean)
    return clean

DOWNLOAD_DIR = get_download_dir()
COOKIE_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "cookies.txt"))
ARIA2_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "bin", "aria2c.exe"))
os.makedirs(DEFAULT_DOWNLOAD_DIR, exist_ok=True)

# In-memory storage synced with SQLite database
jobs = {}
active_job_ids = set()

class SmartDownloadQueue:
    """Intelligent concurrency controller that balances multi-stream speed & resource limits.
    Allows up to 2 high-bitrate video streams concurrently to maximize throughput without thrashing,
    while allowing lightweight audio or clipped tasks to run in parallel up to 4 total concurrent tasks.
    Extra requests automatically wait in a prioritized FIFO queue."""
    def __init__(self, max_concurrent_heavy: int = 2, max_concurrent_total: int = 4):
        self.max_concurrent_heavy = max_concurrent_heavy
        self.max_concurrent_total = max_concurrent_total
        self.lock = threading.Condition()
        self.active_jobs = set()
        self.waiting_queue = []  # list of {'id': str, 'is_heavy': bool, 'event': Event}

    def is_heavy_job(self, format_id: str) -> bool:
        return (format_id or '').lower() != 'audio_only'

    def can_run_job(self, is_heavy: bool) -> bool:
        if len(self.active_jobs) >= self.max_concurrent_total:
            return False
        if is_heavy:
            active_heavy = sum(
                1 for jid in self.active_jobs
                if self.is_heavy_job(jobs.get(jid, {}).get('format_id', 'best'))
            )
            if active_heavy >= self.max_concurrent_heavy:
                return False
        return True

    def acquire(self, job_id: str, format_id: str) -> bool:
        is_heavy = self.is_heavy_job(format_id)
        with self.lock:
            # If no queue and capacity permits, run immediately (e.g. download both at once)
            if not self.waiting_queue and self.can_run_job(is_heavy):
                self.active_jobs.add(job_id)
                return True

            # Otherwise register in waiting queue
            evt = threading.Event()
            self.waiting_queue.append({'id': job_id, 'is_heavy': is_heavy, 'event': evt})
            self._update_queue_metadata()

        while True:
            evt.wait(timeout=1.0)
            status = jobs.get(job_id, {}).get('status')
            if status in ('cancelled', 'failed'):
                with self.lock:
                    self.waiting_queue = [item for item in self.waiting_queue if item['id'] != job_id]
                    self.active_jobs.discard(job_id)
                    self._update_queue_metadata()
                return False
            with self.lock:
                if job_id in self.active_jobs:
                    return True

    def release(self, job_id: str):
        with self.lock:
            self.active_jobs.discard(job_id)
            self._schedule_next_under_lock()
            self._update_queue_metadata()

    def force_start(self, job_id: str) -> bool:
        with self.lock:
            for i, item in enumerate(self.waiting_queue):
                if item['id'] == job_id:
                    self.waiting_queue.pop(i)
                    self.active_jobs.add(job_id)
                    item['event'].set()
                    self._update_queue_metadata()
                    return True
        return False

    def remove(self, job_id: str):
        with self.lock:
            self.waiting_queue = [item for item in self.waiting_queue if item['id'] != job_id]
            self.active_jobs.discard(job_id)
            self._schedule_next_under_lock()
            self._update_queue_metadata()

    def _schedule_next_under_lock(self):
        for i, item in enumerate(self.waiting_queue):
            if self.can_run_job(item['is_heavy']):
                self.waiting_queue.pop(i)
                self.active_jobs.add(item['id'])
                item['event'].set()
                return

    def _update_queue_metadata(self):
        for idx, item in enumerate(self.waiting_queue):
            jid = item['id']
            if jid in jobs and jobs[jid].get('status') == 'queued':
                pos = idx + 1
                reason = "Waiting for active video slot" if item['is_heavy'] else "Waiting for concurrency slot"
                jobs[jid]['speed'] = f'Queued (#{pos})'
                jobs[jid]['eta'] = f'#{pos} in line'
                jobs[jid]['size_str'] = f'Queued (#{pos}) · {reason}'
                db.update_job_progress(jid, status='queued', speed=jobs[jid]['speed'], eta=jobs[jid]['eta'], size_str=jobs[jid]['size_str'])

_DOWNLOAD_QUEUE = SmartDownloadQueue(max_concurrent_heavy=2, max_concurrent_total=4)

def format_bytes(bytes_num: float) -> str:
    """Format bytes into human-readable B, KB, MB, GB."""
    if not bytes_num or bytes_num <= 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.2f} TB"

def format_seconds(seconds) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    if seconds is None or seconds < 0:
        return "--:--"
    try:
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
    except Exception:
        return "--:--"

def get_hls_duration(playlist_text: str) -> float:
    """Sum EXTINF tags in playlist to find duration in seconds."""
    extinfs = re.findall(r'#EXTINF:([\d.]+)', playlist_text)
    if extinfs:
        return sum(float(x) for x in extinfs)
    return 0.0

def time_to_seconds(time_str: str) -> float:
    """Convert HH:MM:SS or HH:MM:SS.ms to seconds."""
    try:
        parts = time_str.split(':')
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
    except Exception:
        pass
    return 0.0

class DownloadManager:
    @staticmethod
    def init():
        """Load stored jobs from database and auto-resume any interrupted tasks."""
        stored = db.get_all_jobs()
        for j in stored:
            jobs[j['id']] = j
            jobs[j['id']]['proc'] = None

        # Auto-resume interrupted downloads in the background
        interrupted = db.recover_interrupted_jobs()
        if interrupted:
            print(f"[DURABLE RESUME] Found {len(interrupted)} unfinished download(s). Auto-resuming in background...")
            for j in interrupted:
                job_id = j['id']
                if job_id not in active_job_ids:
                    jobs[job_id]['status'] = 'downloading'
                    jobs[job_id]['error'] = 'Auto-resuming in background...'
                    db.update_job_progress(job_id, status='downloading', error='Auto-resuming in background...')
                    threading.Thread(
                        target=DownloadManager._retry_job_task,
                        args=(job_id,),
                        daemon=True
                    ).start()

    @staticmethod
    def get_info(url: str, referer: str = None, user_agent: str = None, cookies: str = None):
        """Extract metadata, thumbnail, and available qualities."""
        cleaned_url = clean_url(url)

        # Skip generic html scraping for major platforms natively supported by yt-dlp
        parsed = urlparse(cleaned_url)
        netloc = parsed.netloc.lower()
        is_major_platform = any(d in netloc for d in [
            'youtube.com', 'youtu.be', 'twitter.com', 'x.com', 'reddit.com',
            'tiktok.com', 'instagram.com', 'facebook.com', 'vimeo.com',
            'twitch.tv', 'dailymotion.com', 'bilibili.com'
        ])

        resolved = None if is_major_platform else resolve_page_stream(cleaned_url, custom_cookies=cookies)
        target_url = resolved['stream_url'] if resolved else cleaned_url
        effective_referer = (resolved['referer'] if resolved else None) or referer
        auto_title = (resolved.get('title') if resolved else None)
        thumbnail = (resolved.get('thumbnail') if resolved else None)

        formats = []
        if resolved and resolved.get('formats'):
            for q in resolved['formats']:
                formats.append({
                    'format_id': q['id'],
                    'label': q['label'],
                    'resolution': q['resolution']
                })
        elif '.m3u8' in target_url:
            hls_quals = parse_hls_qualities(target_url, effective_referer or target_url)
            for q in hls_quals:
                formats.append({
                    'format_id': q['id'],
                    'label': q['label'],
                    'resolution': q['resolution']
                })

        headers = {}
        if effective_referer:
            headers['Referer'] = effective_referer
            try:
                p_ref = urlparse(effective_referer)
                headers['Origin'] = f"{p_ref.scheme}://{p_ref.netloc}"
            except Exception:
                pass

        if cookies:
            headers['Cookie'] = cookies

        headers['User-Agent'] = user_agent or DEFAULT_UA

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'http_headers': headers,
            'extract_flat': False,
            'socket_timeout': 10,
        }
        # Provide Node runtime for yt-dlp to accelerate YouTube EJS extraction
        node_path = r"C:\Program Files\nodejs\node.exe"
        if os.path.exists(node_path):
            ydl_opts['js_runtimes'] = {'node': {'path': node_path}}

        if os.path.exists(COOKIE_FILE):
            ydl_opts['cookiefile'] = COOKIE_FILE

        duration = None
        uploader = None
        if not formats:
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(target_url, download=False)
                    if not auto_title and info.get('title'):
                        auto_title = info.get('title')
                    if not thumbnail and info.get('thumbnail'):
                        thumbnail = info.get('thumbnail')
                    duration = info.get('duration')
                    uploader = info.get('uploader')

                    raw_formats = info.get('formats', [])
                    # Deduplicate and organize actual video resolutions present in the stream
                    video_formats = []
                    seen_heights = set()
                    
                    # Sort candidates by resolution height descending
                    sorted_video_candidates = sorted(
                        [f for f in raw_formats if f.get('vcodec') != 'none' and f.get('height')],
                        key=lambda x: (x.get('height') or 0, x.get('tbr') or 0),
                        reverse=True
                    )

                    for f in sorted_video_candidates:
                        h = f.get('height')
                        if h and h > 144 and h not in seen_heights:
                            seen_heights.add(h)
                            fid = f"height_{h}"
                            # Standard labels
                            res_tag = f"{h}p"
                            if h >= 2160:
                                label = f"4K UHD {h}p (MP4)"
                            elif h >= 1440:
                                label = f"2K QHD {h}p (MP4)"
                            elif h >= 1080:
                                label = f"1080p Full HD (MP4)"
                            elif h >= 720:
                                label = f"720p HD (MP4)"
                            else:
                                label = f"{h}p SD (MP4)"

                            video_formats.append({
                                'format_id': fid,
                                'label': label,
                                'resolution': res_tag,
                                'category': 'video'
                            })

                    formats.extend(video_formats)
            except Exception:
                pass

        duration_str = None
        if duration:
            m, s = divmod(int(duration), 60)
            h, m = divmod(m, 60)
            duration_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

        # Only present resolutions that actually exist for this video
        result_formats = [{'format_id': 'best', 'label': '🌟 Best Quality (Auto)', 'resolution': 'Best', 'category': 'video'}]
        result_formats.extend(formats[:10])
        # Always provide clean audio extraction option
        result_formats.append({'format_id': 'audio_only', 'label': '🎵 Audio Only (MP3)', 'resolution': 'Audio', 'category': 'audio'})

        return {
            'title': auto_title or cleaned_url.split('/')[-1].split('?')[0] or 'Video',
            'thumbnail': thumbnail,
            'duration': duration_str,
            'uploader': uploader,
            'formats': result_formats,
            'resolved_stream': target_url if resolved else None,
            'effective_referer': effective_referer
        }

    @staticmethod
    def start_download(url: str, format_id: str = "best", referer: str = None, 
                       user_agent: str = None, custom_title: str = None, thumbnail: str = None,
                       cookies: str = None, start_time: str = None, end_time: str = None) -> str:
        cleaned_url = clean_url(url)
        job_id = str(uuid.uuid4())[:8]
        start_ts = time.time()
        job_record = {
            'id': job_id,
            'url': cleaned_url,
            'title': custom_title or 'Resolving stream...',
            'thumbnail': thumbnail,
            'format_id': format_id or 'best',
            'referer': referer,
            'cookies': cookies,
            'start_time_param': start_time,
            'end_time_param': end_time,
            'status': 'starting',
            'percent': 0.0,
            'speed': '0 KB/s',
            'eta': '--:--',
            'size_str': '',
            'elapsed': '00:00',
            'start_time': start_ts,
            'filename': None,
            'out_path': None,
            'error': None,
            'retries': 0,
            'proc': None
        }
        jobs[job_id] = job_record
        db.save_job(job_record)

        thread = threading.Thread(
            target=DownloadManager._run_download_task,
            args=(job_id, cleaned_url, format_id, referer, user_agent, custom_title, cookies, start_time, end_time),
            daemon=True
        )
        thread.start()
        return job_id

    @staticmethod
    def retry_job(job_id: str) -> bool:
        job = db.get_job(job_id)
        if not job:
            return False
        jobs[job_id] = job
        jobs[job_id]['status'] = 'starting'
        jobs[job_id]['error'] = None
        jobs[job_id]['proc'] = None
        db.save_job(jobs[job_id])

        thread = threading.Thread(
            target=DownloadManager._retry_job_task,
            args=(job_id,),
            daemon=True
        )
        thread.start()
        return True

    @staticmethod
    def _retry_job_task(job_id: str):
        j = jobs.get(job_id) or db.get_job(job_id)
        if not j:
            return
        DownloadManager._run_download_task(
            job_id=j['id'],
            url=j['url'],
            format_id=j.get('format_id', 'best'),
            referer=j.get('referer'),
            user_agent=DEFAULT_UA,
            custom_title=j.get('title'),
            cookies=j.get('cookies')
        )

    @staticmethod
    def pause_job(job_id: str) -> bool:
        if job_id in jobs:
            jobs[job_id]['status'] = 'paused'
            db.update_job_progress(job_id, status='paused')
            if jobs[job_id].get('proc'):
                try:
                    proc = jobs[job_id]['proc']
                    p = psutil.Process(proc.pid)
                    p.suspend()
                except Exception:
                    pass
            return True
        return False

    @staticmethod
    def resume_job(job_id: str) -> bool:
        if job_id in jobs:
            jobs[job_id]['status'] = 'downloading'
            db.update_job_progress(job_id, status='downloading')
            if jobs[job_id].get('proc'):
                try:
                    proc = jobs[job_id]['proc']
                    p = psutil.Process(proc.pid)
                    p.resume()
                except Exception:
                    pass
                return True
            else:
                # Re-trigger background execution for thread-based task
                return DownloadManager.retry_job(job_id)
        return False

    @staticmethod
    def cancel_job(job_id: str) -> bool:
        _DOWNLOAD_QUEUE.remove(job_id)
        if job_id in jobs:
            try:
                jobs[job_id]['status'] = 'cancelled'
                jobs[job_id]['error'] = 'Download cancelled by user.'
                db.update_job_progress(job_id, status='cancelled', error='Download cancelled by user.')
                if jobs[job_id].get('proc'):
                    proc = jobs[job_id]['proc']
                    p = psutil.Process(proc.pid)
                    for child in p.children(recursive=True):
                        child.kill()
                    p.kill()
                out_path = jobs[job_id].get('out_path')
                if out_path and os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except Exception:
                        pass
                tmp_dir = os.path.join(get_download_dir(), f".tmp_{job_id}")
                if os.path.exists(tmp_dir):
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                return True
            except Exception:
                pass
        return False

    @staticmethod
    def start_now_job(job_id: str) -> bool:
        """Allow user to bypass queue order and start immediately."""
        return _DOWNLOAD_QUEUE.force_start(job_id)

    @staticmethod
    def _run_download_task(job_id: str, url: str, format_id: str, referer: str, user_agent: str, custom_title: str, cookies: str = None, start_time: str = None, end_time: str = None):
        if job_id in active_job_ids:
            return
        active_job_ids.add(job_id)

        # Mark as queued until a concurrency slot becomes available
        if jobs.get(job_id) and jobs[job_id].get('status') == 'starting':
            jobs[job_id]['status'] = 'queued'
            jobs[job_id]['speed'] = 'Queued'
            jobs[job_id]['size_str'] = 'In Queue'
            db.update_job_progress(job_id, status='queued')

        acquired = _DOWNLOAD_QUEUE.acquire(job_id, format_id)
        if not acquired or jobs.get(job_id, {}).get('status') in ('cancelled', 'failed'):
            active_job_ids.discard(job_id)
            return

        max_attempts = 10
        try:
            if jobs.get(job_id):
                jobs[job_id]['status'] = 'starting'
                jobs[job_id]['speed'] = 'Starting…'
                db.update_job_progress(job_id, status='starting')

            for attempt in range(max_attempts):
                if jobs[job_id]['status'] == 'cancelled':
                    return

                try:
                    DownloadManager._execute_download_attempt(
                        job_id, url, format_id, referer, user_agent, custom_title, cookies, start_time, end_time
                    )
                    # If completed without exception
                    if jobs[job_id]['status'] == 'completed':
                        return
                except Exception as e:
                    if jobs[job_id]['status'] in ['cancelled', 'completed']:
                        return

                    err_str = str(e)
                    jobs[job_id]['retries'] = attempt + 1
                    if attempt < max_attempts - 1:
                        jobs[job_id]['status'] = 'retrying'
                        if any(k in err_str for k in ['572', '403', 'Forbidden', 'token']):
                            jobs[job_id]['error'] = f"Stream token expired (HTTP 572). Refreshing token and resuming... (Attempt {attempt+1}/{max_attempts})"
                        else:
                            jobs[job_id]['error'] = f"Connection interrupted. Auto-resuming in 3s... (Attempt {attempt+1}/{max_attempts})"
                        db.save_job(jobs[job_id])
                        time.sleep(3)
                    else:
                        jobs[job_id]['status'] = 'failed'
                        jobs[job_id]['error'] = format_friendly_error(err_str, url)
                        db.save_job(jobs[job_id])
        finally:
            _DOWNLOAD_QUEUE.release(job_id)
            active_job_ids.discard(job_id)

    @staticmethod
    def _execute_download_attempt(job_id: str, url: str, format_id: str, referer: str, user_agent: str, custom_title: str, cookies: str = None, start_time: str = None, end_time: str = None):
        # 1. Resolve embedded page streams
        resolved = resolve_page_stream(url, custom_cookies=cookies)
        target_url = resolved['stream_url'] if resolved else url
        effective_referer = (resolved['referer'] if resolved else None) or referer
        auto_title = (resolved.get('title') if resolved else None)
        thumbnail = (resolved.get('thumbnail') if resolved else None)
        ua = user_agent or DEFAULT_UA

        if thumbnail and not jobs[job_id].get('thumbnail'):
            jobs[job_id]['thumbnail'] = thumbnail

        final_title = custom_title or auto_title or jobs[job_id].get('title') or 'Video'
        jobs[job_id]['title'] = final_title

        safe_name = re.sub(r'[\\/*?:"<>|]', "_", final_title)[:120]
        ext = "mp3" if format_id == 'audio_only' else "mp4"
        out_filename = f"{safe_name}.{ext}"
        out_path = os.path.join(get_download_dir(), out_filename)
        jobs[job_id]['filename'] = out_filename
        jobs[job_id]['out_path'] = out_path
        db.save_job(jobs[job_id])

        # Select target stream URL: prefer freshly resolved stream URL to prevent expired CDN tokens
        stream_to_download = target_url
        if resolved and resolved.get('formats') and format_id and format_id != 'best':
            # Match user-selected resolution if available
            matched = False
            for f in resolved.get('formats', []):
                if f.get('resolution') and (f['resolution'] in format_id or format_id == f.get('id')):
                    stream_to_download = f['id']
                    matched = True
                    break
            if not matched and format_id.startswith('http') and not any(k in format_id for k in ['token=', 'expires=', 'url=']):
                stream_to_download = format_id
        elif format_id and format_id.startswith('http') and not resolved:
            stream_to_download = format_id

        # Auto-inject referer for MissAV CDNs
        if any(k in stream_to_download for k in ['surrit.com', 'cloudwindow-route.com', 'sixyik.com', 'missav']):
            effective_referer = 'https://missav.ai/'

        # If it's an HLS (.m3u8) stream:
        if '.m3u8' in stream_to_download:
            # 1. Use high-speed 16-thread segmented downloader
            try:
                success = DownloadManager._download_segmented_hls(
                    job_id, stream_to_download, effective_referer, ua, out_path, format_id, cookies, start_time, end_time
                )
                if success:
                    return
            except Exception as e:
                if jobs[job_id]['status'] in ['cancelled', 'completed']:
                    return
                # Bubble up connection drops to auto-retry loop
                if any(x in str(e).lower() for x in ['connection', 'timeout', 'closed', 'reset']):
                    raise e

            # 2. Fallback to FFmpeg
            try:
                DownloadManager._download_hls_ffmpeg(
                    job_id, stream_to_download, effective_referer, ua, out_path, format_id, cookies, start_time, end_time
                )
                return
            except Exception:
                pass

        # Standard accelerated download via yt-dlp + aria2c
        DownloadManager._download_ytdlp(
            job_id, stream_to_download, format_id, effective_referer, ua, out_path, custom_title or auto_title, cookies, start_time, end_time
        )

    @staticmethod
    def _download_segmented_hls(job_id: str, m3u8_url: str, referer: str, user_agent: str, out_path: str, format_id: str = None, cookies: str = None, start_time: str = None, end_time: str = None) -> bool:
        """High-speed multi-threaded HLS fragment downloader (16 concurrent connections) with disk streaming and FFmpeg remuxing."""
        eff_referer = referer
        if any(k in m3u8_url for k in ['surrit.com', 'cloudwindow-route.com', 'sixyik.com', 'missav']) or not eff_referer:
            eff_referer = 'https://missav.ai/'

        headers = {
            'User-Agent': user_agent or DEFAULT_UA,
            'Referer': eff_referer
        }
        if eff_referer:
            try:
                parsed = urlparse(eff_referer)
                headers['Origin'] = f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                pass
        if cookies:
            headers['Cookie'] = cookies

        # 1. Fetch playlist or subplaylist
        target_m3u8 = m3u8_url
        if format_id and format_id.startswith('http'):
            target_m3u8 = format_id

        r = http_get(target_m3u8, headers=headers, timeout=8)
        if r.status_code != 200:
            return False

        lines = [l.strip() for l in r.text.splitlines() if l.strip()]

        # Master playlist resolution: pick highest resolution
        if '#EXT-X-STREAM-INF' in r.text:
            sub_url = None
            for i, l in enumerate(lines):
                if l.startswith('#EXT-X-STREAM-INF:'):
                    for j in range(i + 1, len(lines)):
                        cand = lines[j]
                        if cand and not cand.startswith('#'):
                            sub_url = urljoin(target_m3u8, cand)
                            break
                    if sub_url:
                        break
            if sub_url:
                target_m3u8 = sub_url
                r = http_get(target_m3u8, headers=headers, timeout=8)
                if r.status_code != 200:
                    return False
                lines = [l.strip() for l in r.text.splitlines() if l.strip()]

        # Extract fragment paths
        segments = []
        for l in lines:
            if not l.startswith('#'):
                frag_url = urljoin(target_m3u8, l)
                segments.append(frag_url)

        if not segments:
            return False

        jobs[job_id]['status'] = 'downloading'
        db.update_job_progress(job_id, status='downloading')

        tmp_dir = os.path.join(get_download_dir(), f".tmp_{job_id}")
        os.makedirs(tmp_dir, exist_ok=True)
        combined_ts = os.path.join(get_download_dir(), f"temp_{job_id}.ts")

        total_segs = len(segments)

        # Pre-scan already downloaded chunks from previous sessions for instant resume
        pre_downloaded = set()
        total_existing_bytes = 0
        if os.path.exists(tmp_dir):
            for fname in os.listdir(tmp_dir):
                if fname.endswith(".part"):
                    fpath = os.path.join(tmp_dir, fname)
                    try:
                        fsize = os.path.getsize(fpath)
                        if fsize > 1024:
                            fidx = int(fname.split('.')[0])
                            pre_downloaded.add(fidx)
                            total_existing_bytes += fsize
                    except Exception:
                        pass

        completed_segs = [len(pre_downloaded)]
        downloaded_bytes = [total_existing_bytes]
        seg_start_ts = time.time()
        last_db_sync = [time.time()]
        stop_flag = [False]

        if len(pre_downloaded) > 0:
            init_percent = round((len(pre_downloaded) / total_segs) * 98.0, 1)
            jobs[job_id]['percent'] = init_percent
            jobs[job_id]['speed'] = 'Resumed'
            db.update_job_progress(job_id, percent=init_percent, speed='Resumed')
            print(f"[DURABLE RESUME] Job {job_id}: Found {len(pre_downloaded)}/{total_segs} segments ({init_percent}%). Resuming...")

        def download_chunk(idx, chunk_url):
            if stop_flag[0] or jobs[job_id]['status'] == 'cancelled':
                return
            dest = os.path.join(tmp_dir, f"{idx:06d}.part")
            # If already downloaded from prior attempt
            if idx in pre_downloaded and os.path.exists(dest) and os.path.getsize(dest) > 1024:
                return

            for attempt in range(6):
                try:
                    while jobs[job_id]['status'] == 'paused':
                        time.sleep(0.5)
                        if jobs[job_id]['status'] == 'cancelled':
                            return

                    res = http_get(chunk_url, headers=headers, timeout=15)
                    if res.status_code == 200 and len(res.content) > 0:
                        with open(dest, 'wb') as f:
                            f.write(res.content)
                        downloaded_bytes[0] += len(res.content)
                        return
                except Exception:
                    time.sleep(1)

        try:
            missing_indices = [idx for idx in range(total_segs) if idx not in pre_downloaded]
            if missing_indices:
                with ThreadPoolExecutor(max_workers=10) as executor:
                    futures = {executor.submit(download_chunk, idx, segments[idx]): idx for idx in missing_indices}
                for f in as_completed(futures):
                    if jobs[job_id]['status'] == 'cancelled':
                        stop_flag[0] = True
                        break

                    completed_segs[0] += 1
                    percent = round((completed_segs[0] / total_segs) * 98.0, 1)
                    jobs[job_id]['percent'] = percent

                    elapsed = max(time.time() - seg_start_ts, 0.1)
                    jobs[job_id]['elapsed'] = format_seconds(elapsed)

                    spd = downloaded_bytes[0] / elapsed
                    if spd > 1024 * 1024:
                        jobs[job_id]['speed'] = f"{spd / (1024 * 1024):.2f} MB/s"
                    else:
                        jobs[job_id]['speed'] = f"{spd / 1024:.1f} KB/s"

                    rem_segs = total_segs - completed_segs[0]
                    seg_rate = completed_segs[0] / elapsed
                    if seg_rate > 0:
                        rem_secs = int(rem_segs / seg_rate)
                        jobs[job_id]['eta'] = format_seconds(rem_secs)
                    else:
                        jobs[job_id]['eta'] = '--:--'

                    jobs[job_id]['size_str'] = f"Frag {completed_segs[0]}/{total_segs} ({format_bytes(downloaded_bytes[0])})"

                    if time.time() - last_db_sync[0] > 1.0:
                        db.update_job_progress(
                            job_id,
                            status='downloading',
                            percent=jobs[job_id]['percent'],
                            speed=jobs[job_id]['speed'],
                            eta=jobs[job_id]['eta'],
                            size_str=jobs[job_id]['size_str'],
                            elapsed=jobs[job_id]['elapsed']
                        )
                        last_db_sync[0] = time.time()

            if jobs[job_id]['status'] == 'cancelled':
                shutil.rmtree(tmp_dir, ignore_errors=True)
                if os.path.exists(combined_ts):
                    os.remove(combined_ts)
                return True

            jobs[job_id]['status'] = 'converting'
            jobs[job_id]['percent'] = 98.5
            jobs[job_id]['speed'] = 'Remuxing...'
            db.update_job_progress(job_id, status='converting', percent=98.5, speed='Remuxing...')

            # Assemble segments sequentially
            with open(combined_ts, 'wb') as outfile:
                for idx in range(total_segs):
                    part_path = os.path.join(tmp_dir, f"{idx:06d}.part")
                    if os.path.exists(part_path):
                        with open(part_path, 'rb') as infile:
                            shutil.copyfileobj(infile, outfile)

            # Clean temporary parts directory
            shutil.rmtree(tmp_dir, ignore_errors=True)

            # Remux to MP4 (with portion trimming if specified)
            cmd = ['ffmpeg', '-y']
            start_s = time_to_seconds(start_time) if start_time else 0.0
            end_s = time_to_seconds(end_time) if end_time else 0.0
            if start_s > 0:
                cmd += ['-ss', str(start_s)]
            cmd += ['-i', combined_ts]
            if end_s > start_s and end_s > 0:
                cmd += ['-t', str(end_s - start_s)]

            if format_id == 'audio_only':
                cmd += ['-vn', '-acodec', 'libmp3lame', '-q:a', '2']
            else:
                cmd += ['-c', 'copy']
            cmd += [out_path]

            proc = subprocess.run(cmd, capture_output=True, text=True)
            if os.path.exists(combined_ts):
                try:
                    os.remove(combined_ts)
                except Exception:
                    pass

            if proc.returncode == 0 and os.path.exists(out_path):
                jobs[job_id]['status'] = 'completed'
                jobs[job_id]['percent'] = 100.0
                jobs[job_id]['speed'] = 'Done'
                jobs[job_id]['eta'] = '00:00'
                db.update_job_progress(job_id, status='completed', percent=100.0, speed='Done', eta='00:00')
                return True
            else:
                raise Exception("FFmpeg remuxing error: " + (proc.stderr or "")[:200])

        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            if os.path.exists(combined_ts):
                try:
                    os.remove(combined_ts)
                except Exception:
                    pass
            raise e

    @staticmethod
    def _download_hls_ffmpeg(job_id: str, m3u8_url: str, referer: str, user_agent: str, out_path: str, format_id: str, cookies: str = None, start_time: str = None, end_time: str = None):
        jobs[job_id]['status'] = 'downloading'
        db.update_job_progress(job_id, status='downloading')
        start_time_proc = time.time()
        jobs[job_id]['start_time'] = start_time_proc
        
        req_headers = {
            'User-Agent': user_agent or DEFAULT_UA,
            'Referer': referer or 'https://google.com/',
        }
        if referer:
            try:
                parsed = urlparse(referer)
                req_headers['Origin'] = f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                pass

        if cookies:
            req_headers['Cookie'] = cookies

        sub_m3u8 = m3u8_url
        total_duration = 0.0
        try:
            r = requests.get(m3u8_url, headers=req_headers, timeout=6)
            if r.status_code == 200:
                lines = [l.strip() for l in r.text.splitlines() if l.strip()]
                if '#EXT-X-STREAM-INF' in r.text:
                    for l in lines:
                        if not l.startswith('#'):
                            sub_m3u8 = urljoin(m3u8_url, l)
                            break
                    r_sub = requests.get(sub_m3u8, headers=req_headers, timeout=6)
                    if r_sub.status_code == 200:
                        total_duration = get_hls_duration(r_sub.text)
                else:
                    total_duration = get_hls_duration(r.text)
        except Exception:
            pass

        headers_str = f"Referer: {req_headers.get('Referer', '')}\r\nUser-Agent: {user_agent}\r\n"
        if 'Origin' in req_headers:
            headers_str += f"Origin: {req_headers['Origin']}\r\n"
        if cookies:
            headers_str += f"Cookie: {cookies}\r\n"

        cmd = [
            'ffmpeg',
            '-headers', headers_str,
            '-allowed_extensions', 'ALL',
            '-allowed_segment_extensions', 'ALL',
        ]

        start_s = time_to_seconds(start_time) if start_time else 0.0
        end_s = time_to_seconds(end_time) if end_time else 0.0
        if start_s > 0:
            cmd += ['-ss', str(start_s)]
        cmd += ['-i', sub_m3u8]
        if end_s > start_s and end_s > 0:
            cmd += ['-t', str(end_s - start_s)]
            total_duration = end_s - start_s
        elif total_duration > start_s and start_s > 0:
            total_duration = total_duration - start_s

        if format_id == 'audio_only':
            cmd += ['-vn', '-acodec', 'libmp3lame', '-q:a', '2']
        else:
            cmd += ['-c', 'copy', '-bsf:a', 'aac_adtstoasc']

        cmd += [
            '-progress', 'pipe:1',
            '-y',
            out_path
        ]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        jobs[job_id]['proc'] = proc
        
        last_db_sync = time.time()
        curr_speed_mult = 1.0

        for line in proc.stdout:
            if jobs[job_id]['status'] == 'cancelled':
                break
            line = line.strip()
            if line.startswith('speed='):
                spd = line.split('=')[1].strip()
                jobs[job_id]['speed'] = spd
                try:
                    curr_speed_mult = float(spd.replace('x', ''))
                except Exception:
                    pass
            elif line.startswith('out_time='):
                curr_time = line.split('=')[1].split('.')[0]
                curr_secs = time_to_seconds(curr_time)
                elapsed_sec = max(0.1, time.time() - start_time_proc)
                jobs[job_id]['elapsed'] = format_seconds(elapsed_sec)

                if total_duration > 0:
                    percent = min(round((curr_secs / total_duration) * 100, 1), 99.9)
                    jobs[job_id]['percent'] = percent
                    rem_secs = max(0.0, total_duration - curr_secs)
                    eff_speed = max(0.1, curr_speed_mult)
                    jobs[job_id]['eta'] = format_seconds(rem_secs / eff_speed)
                    jobs[job_id]['size_str'] = f"{curr_time} / {format_seconds(total_duration)}"
                else:
                    jobs[job_id]['eta'] = '--:--'
                    jobs[job_id]['size_str'] = f"Time: {curr_time}"

            elif line.startswith('total_size='):
                try:
                    bytes_val = int(line.split('=')[1].strip())
                    if total_duration <= 0:
                        jobs[job_id]['size_str'] = f"{format_bytes(bytes_val)}"
                except Exception:
                    pass

            # Sync to SQLite every 1.0 second
            if time.time() - last_db_sync > 1.0:
                db.update_job_progress(
                    job_id,
                    status=jobs[job_id]['status'],
                    percent=jobs[job_id]['percent'],
                    speed=jobs[job_id]['speed'],
                    eta=jobs[job_id]['eta'],
                    size_str=jobs[job_id]['size_str'],
                    elapsed=jobs[job_id]['elapsed']
                )
                last_db_sync = time.time()

        proc.wait()
        jobs[job_id]['proc'] = None

        if jobs[job_id]['status'] == 'cancelled':
            return

        if proc.returncode == 0 and os.path.exists(out_path):
            jobs[job_id]['status'] = 'completed'
            jobs[job_id]['percent'] = 100.0
            jobs[job_id]['speed'] = 'Done'
            jobs[job_id]['eta'] = '00:00'
            db.update_job_progress(job_id, status='completed', percent=100.0, speed='Done', eta='00:00')
        else:
            raise Exception("FFmpeg stream download failed.")

    @staticmethod
    def _download_ytdlp(job_id: str, url: str, format_id: str, referer: str, user_agent: str, out_path: str, title: str, cookies: str = None, start_time: str = None, end_time: str = None):
        headers = {}
        if referer:
            headers['Referer'] = referer
            try:
                parsed = urlparse(referer)
                headers['Origin'] = f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                pass

        if cookies:
            headers['Cookie'] = cookies

        headers['User-Agent'] = user_agent or DEFAULT_UA

        job_start_ts = jobs[job_id].get('job_start_ts') or time.time()
        jobs[job_id]['job_start_ts'] = job_start_ts
        last_db_sync = [time.time()]

        def hook(d):
            if jobs[job_id]['status'] == 'cancelled':
                raise Exception("Download cancelled by user.")
            if d['status'] == 'downloading':
                jobs[job_id]['status'] = 'downloading'
                downloaded = d.get('downloaded_bytes') or 0
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                frag_idx = d.get('fragment_index')
                frag_count = d.get('fragment_count')

                # 1. Percent calculation
                percent = 0.0
                if total > 0:
                    percent = round((downloaded / total) * 100, 1)
                elif frag_count and frag_idx:
                    percent = round((frag_idx / frag_count) * 100, 1)
                elif d.get('_percent_str'):
                    try:
                        clean_p = strip_ansi(d['_percent_str']).replace('%', '').strip()
                        percent = float(clean_p)
                    except Exception:
                        percent = jobs[job_id].get('percent', 0.0)
                else:
                    percent = jobs[job_id].get('percent', 0.0)
                jobs[job_id]['percent'] = min(max(percent, 0.0), 99.9)

                # 2. Speed calculation
                spd = d.get('speed')
                if spd:
                    if spd >= 1024 * 1024:
                        jobs[job_id]['speed'] = f"{spd / (1024 * 1024):.2f} MB/s"
                    elif spd >= 1024:
                        jobs[job_id]['speed'] = f"{spd / 1024:.1f} KB/s"
                    else:
                        jobs[job_id]['speed'] = f"{spd:.0f} B/s"
                elif d.get('_speed_str'):
                    jobs[job_id]['speed'] = strip_ansi(d['_speed_str']).strip()

                # 3. Elapsed Time
                elapsed_sec = max(0.1, time.time() - job_start_ts)
                jobs[job_id]['elapsed'] = format_seconds(elapsed_sec)

                # 4. ETA (Time Remaining)
                eta = d.get('eta')
                if eta is not None and eta >= 0:
                    jobs[job_id]['eta'] = format_seconds(eta)
                elif d.get('_eta_str'):
                    jobs[job_id]['eta'] = strip_ansi(d['_eta_str']).strip()
                elif total > 0 and spd and spd > 0:
                    rem_bytes = max(0, total - downloaded)
                    jobs[job_id]['eta'] = format_seconds(rem_bytes / spd)
                elif frag_count and frag_idx and frag_idx > 0:
                    rem_frags = frag_count - frag_idx
                    avg_frag_time = elapsed_sec / frag_idx
                    jobs[job_id]['eta'] = format_seconds(rem_frags * avg_frag_time)
                else:
                    jobs[job_id]['eta'] = jobs[job_id].get('eta', '--:--')

                # 5. Size / Fragments string
                if total > 0:
                    jobs[job_id]['size_str'] = f"{format_bytes(downloaded)} / {format_bytes(total)}"
                elif frag_count and frag_idx:
                    jobs[job_id]['size_str'] = f"Frag {frag_idx}/{frag_count} ({format_bytes(downloaded)})"
                elif downloaded > 0:
                    jobs[job_id]['size_str'] = format_bytes(downloaded)
                else:
                    jobs[job_id]['size_str'] = ""

                # Sync to SQLite every 1.0 second
                if time.time() - last_db_sync[0] > 1.0:
                    db.update_job_progress(
                        job_id,
                        status='downloading',
                        percent=jobs[job_id]['percent'],
                        speed=jobs[job_id]['speed'],
                        eta=jobs[job_id]['eta'],
                        size_str=jobs[job_id]['size_str'],
                        elapsed=jobs[job_id]['elapsed']
                    )
                    last_db_sync[0] = time.time()

            elif d['status'] == 'finished':
                jobs[job_id]['status'] = 'converting'
                jobs[job_id]['percent'] = 99.9
                jobs[job_id]['speed'] = 'Processing...'
                jobs[job_id]['eta'] = '00:00'
                db.update_job_progress(job_id, status='converting', percent=99.9, speed='Processing...', eta='00:00')

        d_dir = get_download_dir()
        out_pattern = f"{d_dir}/%(title)s [%(id)s].%(ext)s"
        if title:
            safe_name = re.sub(r'[\\/*?:"<>|]', "_", title)[:120]
            out_pattern = f"{d_dir}/{safe_name}.%(ext)s"

        ydl_opts = {
            'outtmpl': out_pattern,
            'progress_hooks': [hook],
            'http_headers': headers,
            'no_warnings': True,
            'quiet': True,
            'noprogress': True,
            'hls_prefer_native': True,  # Native HLS reports real-time fragment hooks
            'merge_output_format': 'mp4',
            'concurrent_fragment_downloads': 8, # Optimized 8-thread fragment downloader for low CPU/RAM
            'continuedl': True,  # Resume partial downloads
            'retries': 5,        # Yield to parent retry manager for fresh tokens on expiration
            'fragment_retries': 5,
        }

        if os.path.exists(COOKIE_FILE):
            ydl_opts['cookiefile'] = COOKIE_FILE

        # Handle portion trimming via download_ranges
        start_s = time_to_seconds(start_time) if start_time else 0.0
        end_s = time_to_seconds(end_time) if end_time else 0.0
        if start_s > 0 or end_s > 0:
            target_end = end_s if (end_s > start_s and end_s > 0) else None
            ydl_opts['download_ranges'] = yt_dlp.utils.download_range_func(None, [(start_s, target_end)])
            ydl_opts['force_keyframes_at_cuts'] = True

        if format_id == 'audio_only':
            ydl_opts['format'] = 'bestaudio/best'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        elif format_id and format_id.startswith('height_'):
            h_val = format_id.replace('height_', '')
            ydl_opts['format'] = f"bestvideo[height<={h_val}]+bestaudio/best[height<={h_val}]/best"
        elif format_id and format_id != 'best' and not format_id.startswith('http'):
            ydl_opts['format'] = f"{format_id}+bestaudio/best"
        else:
            ydl_opts['format'] = 'bestvideo+bestaudio/best'

        # Fast Node.js runtime for JavaScript evaluation if available
        node_path = r"C:\Program Files\nodejs\node.exe"
        if os.path.exists(node_path):
            ydl_opts['js_runtimes'] = {'node': {'path': node_path}}

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                final_title = title or info.get('title') or 'Video'
                jobs[job_id]['title'] = final_title
                jobs[job_id]['status'] = 'completed'
                jobs[job_id]['percent'] = 100.0
                jobs[job_id]['speed'] = 'Done'
                jobs[job_id]['eta'] = '00:00'
                jobs[job_id]['error'] = None
                db.save_job(jobs[job_id])
                print(f"\n[OmniStream] ✅ Download 100% complete: {final_title}")
        except Exception as e:
            if jobs[job_id]['status'] == 'cancelled':
                return
            err_msg = str(e)
            # If 416 or 572 (Range Not Satisfiable) occurs, verify if the downloaded file is already complete
            if any(k in err_msg for k in ['416', '572', 'Range Not Satisfiable']):
                part_file = f"{out_path}.part" if not out_path.endswith('.part') else out_path
                if not os.path.exists(part_file):
                    for f in glob.glob(f"{get_download_dir()}/*.part"):
                        if os.path.basename(out_path).split('.')[0][:20] in f:
                            part_file = f
                            break
                if os.path.exists(part_file) and os.path.getsize(part_file) > 10 * 1024 * 1024:
                    try:
                        res = subprocess.run(['ffmpeg', '-v', 'error', '-i', part_file, '-t', '1', '-f', 'null', '-'], capture_output=True, timeout=5)
                        if res.returncode == 0:
                            final_target = part_file[:-5] if part_file.endswith('.part') else part_file
                            if os.path.exists(final_target):
                                os.remove(final_target)
                            os.rename(part_file, final_target)
                            aria_file = f"{part_file}.aria2"
                            if os.path.exists(aria_file):
                                try:
                                    os.remove(aria_file)
                                except Exception:
                                    pass
                            jobs[job_id]['status'] = 'completed'
                            jobs[job_id]['percent'] = 100.0
                            jobs[job_id]['speed'] = 'Done'
                            jobs[job_id]['eta'] = '00:00'
                            jobs[job_id]['error'] = None
                            jobs[job_id]['out_path'] = final_target
                            jobs[job_id]['filename'] = os.path.basename(final_target)
                            db.save_job(jobs[job_id])
                            print(f"\n[OmniStream] ✅ Download 100% verified & complete: {os.path.basename(final_target)}")
                            return
                    except Exception:
                        pass
            raise e

# Initialize database and auto-resume on load
DownloadManager.init()
