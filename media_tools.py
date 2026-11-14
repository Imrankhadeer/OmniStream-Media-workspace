import os
import subprocess
import threading
import uuid
import time
import re
from pathlib import Path
from typing import List, Optional
import yt_dlp
from faster_whisper import WhisperModel

from downloader import get_download_dir

media_tasks = {}
active_task_ids = set()

def _prune_old_tasks():
    if len(media_tasks) > 60:
        completed = [tid for tid, t in media_tasks.items() if t.get('status') in ('completed', 'failed', 'cancelled')]
        for tid in completed[:len(media_tasks) - 50]:
            media_tasks.pop(tid, None)

# Pre-load or lazy load faster-whisper model
_whisper_model = None
_model_lock = threading.Lock()

def get_whisper_model():
    global _whisper_model
    with _model_lock:
        if _whisper_model is None:
            # 'tiny' or 'base' runs blazing fast on CPU with int8 quantization
            _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        return _whisper_model

def _format_srt_timestamp(seconds: float) -> str:
    millis = int((seconds - int(seconds)) * 1000)
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"

def _format_time_sec(t_str: str) -> float:
    """Parse 00:01:23 or 83 or 01:23 into seconds."""
    if not t_str:
        return 0.0
    parts = str(t_str).strip().split(':')
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        else:
            return float(parts[0])
    except Exception:
        return 0.0

def start_transcription_task(file_path: Optional[str] = None, url: Optional[str] = None, 
                             language: str = "auto", output_fmt: str = "srt",
                             start_time: Optional[str] = None, end_time: Optional[str] = None) -> str:
    _prune_old_tasks()
    task_id = "trans-" + str(uuid.uuid4())[:8]
    media_tasks[task_id] = {
        "id": task_id,
        "type": "transcription",
        "status": "queued",
        "progress": 0.0,
        "label": os.path.basename(file_path).replace('_upload_', '') if file_path else (url or "Audio source"),
        "error": None,
        "result_path": None,
        "result_filename": None,
        "download_url": None,
        "start_time": time.time()
    }

    def _worker():
        task = media_tasks[task_id]
        task["status"] = "processing"
        temp_audio = None
        trimmed_audio = None
        source_title = None
        try:
            d_dir = get_download_dir()
            
            # 1. Resolve source audio
            if url:
                task["label"] = f"Extracting audio from {url[:40]}..."
                temp_base = os.path.join(d_dir, f"_temp_trans_{task_id}")
                
                # Check if it's a web URL (YouTube, Vimeo, etc.) or direct stream
                if re.match(r'^https?://(www\.)?(youtube\.com|youtu\.be|tiktok\.com|twitter\.com|x\.com|instagram\.com|facebook\.com|reddit\.com|vimeo\.com|dailymotion\.com)', url, re.I):
                    ydl_opts = {
                        'format': 'bestaudio/best',
                        'outtmpl': f"{temp_base}_%(id)s.%(ext)s",
                        'postprocessors': [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192',
                        }],
                        'quiet': True,
                        'no_warnings': True
                    }
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        source_title = info.get('title')
                        raw_fn = ydl.prepare_filename(info)
                        temp_audio = raw_fn.rsplit('.', 1)[0] + '.mp3'
                else:
                    temp_audio = f"{temp_base}.mp3"
                    cmd = [
                        "ffmpeg", "-y", "-i", url,
                        "-vn", "-ac", "1", "-ar", "16000", temp_audio
                    ]
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                
                source_audio = temp_audio
            elif file_path and os.path.exists(file_path):
                source_audio = file_path
                source_title = Path(file_path).stem
                if source_title.startswith('_upload_'):
                    source_title = source_title.replace('_upload_', '')
            else:
                raise ValueError("No valid audio source or file provided.")

            # Check if portion trimming is requested
            start_s = _format_time_sec(start_time)
            end_s = _format_time_sec(end_time)
            
            if start_s > 0 or end_s > 0:
                trimmed_audio = os.path.join(d_dir, f"_temp_trimmed_{task_id}.wav")
                ffmpeg_trim = ["ffmpeg", "-y"]
                if start_s > 0:
                    ffmpeg_trim.extend(["-ss", str(start_s)])
                ffmpeg_trim.extend(["-i", source_audio])
                if end_s > start_s and end_s > 0:
                    ffmpeg_trim.extend(["-t", str(end_s - start_s)])
                ffmpeg_trim.extend(["-ac", "1", "-ar", "16000", trimmed_audio])
                subprocess.run(ffmpeg_trim, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                input_for_whisper = trimmed_audio
            else:
                input_for_whisper = source_audio

            task["progress"] = 25.0
            task["label"] = f"Whisper AI transcribing..."

            # 2. Transcribe with faster-whisper
            model = get_whisper_model()
            lang_param = None if language in ["auto", None, ""] else ("en" if language.lower() == "english" else ("hi" if language.lower() == "hindi" else ("te" if language.lower() == "telugu" else language)))
            
            segments, info = model.transcribe(
                input_for_whisper,
                language=lang_param,
                beam_size=5,
                vad_filter=True, # Built-in voice activity detection
                vad_parameters=dict(min_silence_duration_ms=500)
            )

            srt_entries = []
            base_offset = start_s
            for idx, seg in enumerate(segments):
                text_clean = seg.text.strip()
                if text_clean:
                    s_start = base_offset + seg.start
                    s_end = base_offset + seg.end
                    srt_entries.append((len(srt_entries) + 1, s_start, s_end, text_clean))
                task["progress"] = min(95.0, 30.0 + idx * 3.0)

            # Generate SRT file
            base_name = re.sub(r'[\\/*?:"<>|]', "_", source_title or "subtitles")[:60]
            out_srt_name = f"{base_name}.srt"
            out_srt_path = os.path.join(d_dir, out_srt_name)
            
            with open(out_srt_path, "w", encoding="utf-8") as srt_f:
                if not srt_entries:
                    srt_f.write(f"1\n00:00:00,000 --> 00:00:05,000\n[No vocal speech detected in audio]\n\n")
                else:
                    for num, s_start, s_end, text in srt_entries:
                        srt_f.write(f"{num}\n{_format_srt_timestamp(s_start)} --> {_format_srt_timestamp(s_end)}\n{text}\n\n")
            
            task["status"] = "completed"
            task["progress"] = 100.0
            task["result_path"] = out_srt_path
            task["result_filename"] = out_srt_name
            task["download_url"] = f"/api/file/{out_srt_name}"
        except Exception as e:
            task["status"] = "failed"
            task["error"] = str(e)
        finally:
            if temp_audio and os.path.exists(temp_audio):
                try: os.remove(temp_audio)
                except Exception: pass
            if trimmed_audio and os.path.exists(trimmed_audio):
                try: os.remove(trimmed_audio)
                except Exception: pass
            if file_path and os.path.exists(file_path) and os.path.basename(file_path).startswith('_upload_'):
                try: os.remove(file_path)
                except Exception: pass

    threading.Thread(target=_worker, daemon=True).start()
    return task_id


def start_compression_task(file_paths: List[str], profile: str = "Balanced", compression_level: Optional[int] = None) -> str:
    _prune_old_tasks()
    task_id = "comp-" + str(uuid.uuid4())[:8]
    media_tasks[task_id] = {
        "id": task_id,
        "type": "compression",
        "status": "queued",
        "progress": 0.0,
        "label": f"Compressing {len(file_paths)} file(s)",
        "profile": profile,
        "error": None,
        "results": [],
        "download_urls": [],
        "start_time": time.time()
    }

    def _worker():
        task = media_tasks[task_id]
        task["status"] = "processing"
        d_dir = get_download_dir()
        
        # Determine CRF and Bitrate based on continuous slider (0 = max quality, 100 = max compression)
        # or legacy profile strings
        if compression_level is not None:
            cl = max(0, min(100, int(compression_level)))
            # Video CRF: 18 (ultra high quality) to 34 (maximum compression / smallest size)
            crf_val = round(18 + (cl / 100.0) * 16)
            crf = str(crf_val)
            preset = "slow" if cl > 65 else ("medium" if cl > 30 else "fast")
            # Audio bitrate: 256k down to 64k
            audio_kb = round(256 - (cl / 100.0) * 192)
            audio_br = f"{audio_kb}k"
            # Image quality scale: 95 down to 45
            img_q_val = round(95 - (cl / 100.0) * 50)
            img_q = str(img_q_val)
            tag_suffix = f"c{cl}"
        else:
            crf = "26"
            preset = "medium"
            audio_br = "128k"
            img_q = "75"
            tag_suffix = profile.lower()[:4]
            if "smaller" in profile.lower():
                crf = "31"
                preset = "slow"
                audio_br = "96k"
                img_q = "55"
            elif "higher" in profile.lower() or "quality" in profile.lower():
                crf = "21"
                preset = "fast"
                audio_br = "192k"
                img_q = "88"

        results = []
        download_urls = []
        total_files = len(file_paths)
        try:
            for idx, fpath in enumerate(file_paths):
                if not os.path.exists(fpath):
                    continue
                p = Path(fpath)
                ext = p.suffix.lower().replace('.', '')
                stem = p.stem.replace('_up_comp_', '').replace('_upload_', '')
                out_name = f"{stem}_compressed_{tag_suffix}.{ext}"
                out_path = os.path.join(d_dir, out_name)

                # Check if file is image, audio, or video
                if ext in ['png', 'jpg', 'jpeg', 'webp', 'bmp']:
                    # Image compression via ffmpeg
                    if ext == 'png':
                        cmd = ["ffmpeg", "-y", "-i", fpath, "-compression_level", "9", out_path]
                    else:
                        q_scale = str(max(2, min(31, round(2 + ((100 - (compression_level or 50)) / 100.0) * 20))))
                        cmd = ["ffmpeg", "-y", "-i", fpath, "-qscale:v", q_scale, out_path]
                elif ext in ['mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg', 'opus']:
                    # Audio compression
                    cmd = ["ffmpeg", "-y", "-i", fpath, "-c:a", "libmp3lame", "-b:a", audio_br, out_path]
                else:
                    # Video compression
                    cmd = [
                        "ffmpeg", "-y", "-i", fpath,
                        "-c:v", "libx264", "-crf", crf, "-preset", preset,
                        "-c:a", "aac", "-b:a", audio_br,
                        out_path
                    ]

                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                results.append(out_name)
                download_urls.append(f"/api/file/{out_name}")
                task["progress"] = round(((idx + 1) / total_files) * 100, 1)

            task["status"] = "completed"
            task["progress"] = 100.0
            task["results"] = results
            task["download_urls"] = download_urls
        except Exception as e:
            task["status"] = "failed"
            task["error"] = str(e)
        finally:
            for fpath in file_paths:
                if os.path.exists(fpath) and os.path.basename(fpath).startswith(('_up_comp_', '_up_conv_', '_upload_')):
                    try: os.remove(fpath)
                    except Exception: pass

    threading.Thread(target=_worker, daemon=True).start()
    return task_id


def start_conversion_task(file_paths: List[str], target_format: str = "mp4", 
                          quality: str = "balanced", resolution: str = "original",
                          audio_handling: str = "keep") -> str:
    _prune_old_tasks()
    task_id = "conv-" + str(uuid.uuid4())[:8]
    media_tasks[task_id] = {
        "id": task_id,
        "type": "conversion",
        "status": "queued",
        "progress": 0.0,
        "label": f"Converting {len(file_paths)} file(s) to {target_format.upper()}",
        "error": None,
        "results": [],
        "download_urls": [],
        "start_time": time.time()
    }

    def _worker():
        task = media_tasks[task_id]
        task["status"] = "processing"
        d_dir = get_download_dir()
        target_fmt = target_format.lower().replace(".", "")

        is_audio_target = target_fmt in ["mp3", "wav", "flac", "m4a", "opus"]
        results = []
        download_urls = []
        total_files = len(file_paths)

        try:
            for idx, fpath in enumerate(file_paths):
                if not os.path.exists(fpath):
                    continue
                p = Path(fpath)
                stem = p.stem.replace('_up_conv_', '').replace('_upload_', '')
                out_name = f"{stem}_converted.{target_fmt}"
                out_path = os.path.join(d_dir, out_name)

                cmd = ["ffmpeg", "-y", "-i", fpath]

                # Resolution
                scale_filter = None
                if resolution == "1080p":
                    scale_filter = "scale=-2:1080"
                elif resolution == "720p":
                    scale_filter = "scale=-2:720"
                elif resolution == "480p":
                    scale_filter = "scale=-2:480"

                if is_audio_target:
                    cmd.append("-vn")
                    if target_fmt == "mp3":
                        cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k"])
                    elif target_fmt == "wav":
                        cmd.extend(["-c:a", "pcm_s16le"])
                    elif target_fmt == "flac":
                        cmd.extend(["-c:a", "flac"])
                    elif target_fmt == "m4a":
                        cmd.extend(["-c:a", "aac", "-b:a", "256k"])
                    elif target_fmt == "opus":
                        cmd.extend(["-c:a", "libopus", "-b:a", "128k"])
                elif target_fmt == "gif":
                    vf = "fps=15,scale=480:-1:flags=lanczos"
                    if scale_filter:
                        vf = f"fps=15,{scale_filter}:flags=lanczos"
                    cmd.extend(["-vf", vf])
                else:
                    # Video container: mp4, mkv, webm, mov
                    if scale_filter:
                        cmd.extend(["-vf", scale_filter])
                    
                    # Video codec & quality
                    crf = "23"
                    if quality == "high": crf = "18"
                    elif quality == "compact": crf = "28"

                    if target_fmt == "webm":
                        cmd.extend(["-c:v", "libvpx-vp9", "-crf", crf, "-b:v", "0", "-c:a", "libopus"])
                    else:
                        cmd.extend(["-c:v", "libx264", "-crf", crf, "-preset", "medium"])

                    # Audio handling
                    if audio_handling == "strip":
                        cmd.append("-an")
                    elif audio_handling == "normalize":
                        cmd.extend(["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"])

                cmd.append(out_path)
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                results.append(out_name)
                download_urls.append(f"/api/file/{out_name}")
                task["progress"] = round(((idx + 1) / total_files) * 100, 1)

            task["status"] = "completed"
            task["progress"] = 100.0
            task["results"] = results
            task["download_urls"] = download_urls
        except Exception as e:
            task["status"] = "failed"
            task["error"] = str(e)
        finally:
            for fpath in file_paths:
                if os.path.exists(fpath) and os.path.basename(fpath).startswith(('_up_comp_', '_up_conv_', '_upload_')):
                    try: os.remove(fpath)
                    except Exception: pass

    threading.Thread(target=_worker, daemon=True).start()
    return task_id
