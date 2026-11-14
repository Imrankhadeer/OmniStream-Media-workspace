import os
import sys
import glob
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import db
import cookies_mgr
import crawler
import media_tools
from downloader import (
    DownloadManager, jobs, DOWNLOAD_DIR, COOKIE_FILE, ARIA2_PATH,
    get_download_dir, set_download_dir, DEFAULT_DOWNLOAD_DIR
)
from resolver import clean_url, format_friendly_error

app = FastAPI(title="OmniStream - Video & Stream Downloader")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def on_startup():
    """Auto-initialize DownloadManager and auto-resume any interrupted background downloads."""
    DownloadManager.init()

STATIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

class ExtractRequest(BaseModel):
    url: str
    referer: Optional[str] = None
    user_agent: Optional[str] = None
    cookies: Optional[str] = None

class DownloadRequest(BaseModel):
    url: str
    format_id: Optional[str] = "best"
    referer: Optional[str] = None
    user_agent: Optional[str] = None
    custom_title: Optional[str] = None
    thumbnail: Optional[str] = None
    cookies: Optional[str] = None
    cookie_platform: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None

class BulkItemRequest(BaseModel):
    url: str
    custom_title: Optional[str] = None
    thumbnail: Optional[str] = None
    format_id: Optional[str] = "best"
    referer: Optional[str] = None
    cookies: Optional[str] = None

class BulkDownloadRequest(BaseModel):
    items: List[BulkItemRequest]

class SettingsUpdateRequest(BaseModel):
    download_dir: str

class CookieSaveRequest(BaseModel):
    cookies: str
    persist: Optional[bool] = False

class SessionCookieRequest(BaseModel):
    cookies: str
    persist: Optional[bool] = False

class CrawlRequest(BaseModel):
    source: Optional[str] = "auto"
    query: str
    sort: Optional[str] = "hot"
    limit: Optional[int] = 25
    custom_cookies: Optional[str] = None

class TranscribeRequest(BaseModel):
    url: Optional[str] = None
    language: Optional[str] = "auto"
    output_fmt: Optional[str] = "srt"
    start_time: Optional[str] = None
    end_time: Optional[str] = None

class CompressRequest(BaseModel):
    filenames: Optional[List[str]] = None
    profile: Optional[str] = "Balanced"

class ConvertRequest(BaseModel):
    filenames: Optional[List[str]] = None
    target_format: Optional[str] = "mp4"
    quality: Optional[str] = "balanced"
    resolution: Optional[str] = "original"
    audio_handling: Optional[str] = "keep"

@app.get("/")
async def serve_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return HTMLResponse("<h1>Downloader UI Loading...</h1>")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    fav_file = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.exists(fav_file):
        return FileResponse(fav_file)
    from fastapi.responses import Response
    # Minimal 1x1 transparent icon byte response to cleanly satisfy browser requests
    return Response(content=b"", media_type="image/x-icon")

import asyncio

@app.post("/api/extract")
async def extract_info(req: ExtractRequest):
    if not req.url:
        raise HTTPException(status_code=400, detail="URL is required")
    cleaned = clean_url(req.url)
    try:
        # Offload blocking network extraction to threadpool to keep FastAPI event loop responsive
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(
            None,
            DownloadManager.get_info,
            cleaned,
            req.referer,
            req.user_agent,
            req.cookies
        )
        return info
    except Exception as e:
        return {"error": format_friendly_error(str(e), cleaned)}

@app.post("/api/download")
async def start_download(req: DownloadRequest):
    if not req.url:
        raise HTTPException(status_code=400, detail="URL is required")
    cleaned = clean_url(req.url)
    job_id = DownloadManager.start_download(
        url=cleaned,
        format_id=req.format_id or "best",
        referer=req.referer,
        user_agent=req.user_agent,
        custom_title=req.custom_title,
        thumbnail=req.thumbnail,
        cookies=req.cookies,
        start_time=req.start_time,
        end_time=req.end_time
    )
    return {"job_id": job_id, "status": "started", "cleaned_url": cleaned}

@app.post("/api/bulk_download")
async def start_bulk_download(req: BulkDownloadRequest):
    if not req.items:
        raise HTTPException(status_code=400, detail="No items to download")
    job_ids = []
    for item in req.items:
        if not item.url:
            continue
        cleaned = clean_url(item.url)
        try:
            job_id = DownloadManager.start_download(
                url=cleaned,
                format_id=item.format_id or "best",
                referer=item.referer,
                custom_title=item.custom_title,
                thumbnail=item.thumbnail,
                cookies=item.cookies
            )
            job_ids.append(job_id)
        except Exception as e:
            print(f"[Bulk] Error starting download for {item.url}: {e}")
    return {"job_ids": job_ids, "queued_count": len(job_ids)}

@app.get("/api/cookies")
async def get_cookies_info():
    return cookies_mgr.get_cookies_status()

@app.post("/api/session_cookies")
async def set_session_cookies_endpoint(req: SessionCookieRequest):
    if not req.cookies or not req.cookies.strip():
        raise HTTPException(status_code=400, detail="Cookie content cannot be empty")
    try:
        return cookies_mgr.set_session_cookies(req.cookies, persist_to_disk=bool(req.persist))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/session_cookies")
async def clear_session_cookies_endpoint():
    return cookies_mgr.clear_session_cookies()

@app.post("/api/cookies")
async def save_cookies(req: Optional[CookieSaveRequest] = None, request: Request = None):
    content = ""
    persist = True
    if req and req.cookies:
        content = req.cookies
        persist = bool(req.persist)
    elif request:
        try:
            body = await request.body()
            content = body.decode("utf-8", errors="ignore")
        except Exception:
            pass
    if not content or not content.strip():
        raise HTTPException(status_code=400, detail="No cookie content provided")
    res = cookies_mgr.set_session_cookies(content, persist_to_disk=persist)
    return res

@app.post("/api/upload_cookies")
async def upload_cookie_file(file: UploadFile = File(...)):
    content = (await file.read()).decode("utf-8", errors="ignore")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    res = cookies_mgr.set_session_cookies(content, persist_to_disk=True)
    return res

@app.delete("/api/cookies")
async def delete_cookies():
    res = cookies_mgr.clear_session_cookies()
    return {"status": "deleted", **res}

@app.post("/api/crawl")
async def crawl_media(req: CrawlRequest):
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="Query or URL is required")
    q = req.query.strip()
    source = (req.source or "auto").lower()

    if source == "redgifs" or "redgifs.com" in q.lower() or (q.startswith("@") and not "r/" in q):
        return crawler.crawl_redgifs(q, count=req.limit or 24, order=req.sort or "trending")
    else:
        return crawler.crawl_reddit(q, limit=req.limit or 25, sort=req.sort or "hot", custom_cookies=req.custom_cookies)

# ── Extracted Endpoints from D:\Programing\Downloader-app ─────────────────────

import html
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_proxy_session = requests.Session()
_adapter = HTTPAdapter(
    pool_connections=10,
    pool_maxsize=15,
    max_retries=Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
)
_proxy_session.mount("https://", _adapter)
_proxy_session.mount("http://", _adapter)

@app.get('/proxy')
def proxy_media(url: str):
    """High-speed pooled CORS & hotlink proxy with browser caching for Reddit & RedGIFs thumbnails."""
    try:
        clean = html.unescape(url).replace('&amp;', '&')
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
        }
        if any(d in clean for d in ('reddit.com', 'redditmedia.com', 'redd.it', 'preview.redd.it')):
            headers['Referer'] = 'https://www.reddit.com/'
        else:
            headers['Referer'] = 'https://www.redgifs.com/'

        r = _proxy_session.get(clean, headers=headers, stream=True, timeout=12)
        r.raise_for_status()

        content_type = r.headers.get('Content-Type')
        if not content_type or 'text' in content_type:
            if clean.endswith('.png'): content_type = 'image/png'
            elif clean.endswith('.webp'): content_type = 'image/webp'
            elif clean.endswith('.gif'): content_type = 'image/gif'
            else: content_type = 'image/jpeg'

        return StreamingResponse(
            r.iter_content(32 * 1024),
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=604800, immutable",
                "Access-Control-Allow-Origin": "*"
            }
        )
    except Exception as e:
        raise HTTPException(502, f"Proxy error: {e}")

@app.get('/api/reddit/user_content')
def api_reddit_user_content(category: str = 'saved', username: str = '', session_cookie: str = None, limit: int = 100, sort: str = 'new'):
    target = f"https://www.reddit.com/user/{username.strip()}/{category.strip()}/"
    res = crawler.crawl_reddit(target, limit=limit, sort=sort, custom_cookies=session_cookie)
    return {'videos': res.get('items', []), 'count': len(res.get('items', [])), 'error': res.get('error')}

@app.get('/api/reddit/saved')
def api_reddit_saved(username: str, session_cookie: str = None, limit: int = 100):
    target = f"https://www.reddit.com/user/{username.strip()}/saved/"
    res = crawler.crawl_reddit(target, limit=limit, sort='new', custom_cookies=session_cookie)
    return {'videos': res.get('items', []), 'count': len(res.get('items', [])), 'error': res.get('error')}

@app.get('/api/reddit/fetch')
def api_reddit_fetch(url: str, session_cookie: str = None):
    res = crawler.crawl_reddit(url, limit=100, custom_cookies=session_cookie)
    return {'videos': res.get('items', []), 'count': len(res.get('items', [])), 'error': res.get('error')}

@app.get('/api/fetch')
def api_fetch_redgifs(url: str):
    res = crawler.crawl_redgifs(url, count=80)
    return {'type': 'gifs', 'videos': res.get('items', []), 'count': len(res.get('items', [])), 'error': res.get('error')}

@app.get('/api/default-folder')
def api_default_folder():
    return {'folder': get_download_dir()}

@app.get('/api/validate-folder')
def api_validate_folder(path: str):
    p = path.strip()
    try:
        os.makedirs(p, exist_ok=True)
        test = os.path.join(p, '.write_test')
        with open(test, 'w') as f:
            f.write('ok')
        os.remove(test)
        return {'ok': True, 'path': p}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

@app.post("/api/job/{job_id}/pause")
async def pause_job(job_id: str):
    success = DownloadManager.pause_job(job_id)
    return {"status": "paused" if success else "failed"}

@app.post("/api/job/{job_id}/resume")
async def resume_job(job_id: str):
    success = DownloadManager.resume_job(job_id)
    return {"status": "resumed" if success else "failed"}

@app.post("/api/job/{job_id}/retry")
async def retry_job(job_id: str):
    success = DownloadManager.retry_job(job_id)
    return {"status": "retrying" if success else "failed"}

@app.post("/api/job/{job_id}/cancel")
async def cancel_job(job_id: str):
    if job_id in media_tools.media_tasks:
        media_tools.media_tasks[job_id]["status"] = "cancelled"
        media_tools.media_tasks[job_id]["error"] = "Task cancelled by user."
        return {"status": "cancelled"}
    success = DownloadManager.cancel_job(job_id)
    return {"status": "cancelled" if success else "failed"}

@app.delete("/api/job/{job_id}")
async def remove_job(job_id: str):
    if job_id in media_tools.media_tasks:
        del media_tools.media_tasks[job_id]
        return {"status": "deleted"}
    db.delete_job(job_id)
    if job_id in jobs:
        del jobs[job_id]
    return {"status": "deleted"}

@app.get("/api/status/{job_id}")
async def get_job_status(job_id: str):
    data = jobs.get(job_id) or db.get_job(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="Job not found")
    data = dict(data)
    data.pop('proc', None)
    return data

@app.get("/api/jobs")
async def get_all_jobs():
    # Return persisted download jobs from database
    results = db.get_all_jobs()
    for item in results:
        # Merge live memory speed/percent/eta/size_str/elapsed if currently active
        if item['id'] in jobs:
            item['percent'] = jobs[item['id']].get('percent', item['percent'])
            item['speed'] = jobs[item['id']].get('speed', item['speed'])
            item['eta'] = jobs[item['id']].get('eta', item['eta'])
            item['size_str'] = jobs[item['id']].get('size_str', item.get('size_str', ''))
            item['elapsed'] = jobs[item['id']].get('elapsed', item.get('elapsed', ''))
            item['status'] = jobs[item['id']].get('status', item['status'])
        item['task_type'] = 'download'
        item.pop('proc', None)

    # Also include background media tasks (transcriptions, compressions, conversions)
    for tid, tinfo in media_tools.media_tasks.items():
        results.append({
            "id": tid,
            "url": "",
            "title": tinfo.get("label") or f"{tinfo.get('type', 'Media').capitalize()} Task",
            "thumbnail": None,
            "format_id": tinfo.get("type"),
            "status": tinfo.get("status", "processing"),
            "percent": float(tinfo.get("progress", 0.0)),
            "speed": tinfo.get("type", "tool").capitalize(),
            "eta": "--:--",
            "size_str": f"Tool: {tinfo.get('type')}",
            "elapsed": "",
            "error": tinfo.get("error"),
            "task_type": tinfo.get("type"),
            "download_url": tinfo.get("download_url") or (tinfo.get("download_urls")[0] if tinfo.get("download_urls") else None)
        })

    return results

@app.get("/api/system_status")
async def get_system_status():
    cookie_info = cookies_mgr.get_cookies_status()
    has_aria2 = os.path.exists(ARIA2_PATH)
    return {
        "aria2_active": has_aria2,
        "cookies_detected": cookie_info["exists"],
        "cookie_count": cookie_info["count"],
        "cookie_domains": cookie_info["domains"],
        "has_reddit": cookie_info["has_reddit"],
        "has_redgifs": cookie_info["has_redgifs"]
    }

@app.get("/api/settings")
async def get_settings():
    cur_dir = get_download_dir()
    home = os.path.expanduser("~")
    user_dl = os.path.join(home, "Downloads")
    desktop = os.path.join(home, "Desktop")
    return {
        "download_dir": cur_dir,
        "default_dir": DEFAULT_DOWNLOAD_DIR,
        "user_downloads": user_dl if os.path.exists(user_dl) else DEFAULT_DOWNLOAD_DIR,
        "desktop": desktop if os.path.exists(desktop) else DEFAULT_DOWNLOAD_DIR
    }

@app.post("/api/settings")
async def update_settings(req: SettingsUpdateRequest):
    try:
        new_dir = set_download_dir(req.download_dir)
        return {
            "status": "saved",
            "download_dir": new_dir
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid folder path: {str(e)}")

@app.post("/api/browse_folder")
async def browse_folder():
    """Trigger native desktop folder picker dialog."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        chosen = filedialog.askdirectory(initialdir=get_download_dir(), title="Select Download Folder")
        root.destroy()
        if chosen:
            clean = set_download_dir(chosen)
            return {"status": "selected", "download_dir": clean}
        return {"status": "cancelled", "download_dir": get_download_dir()}
    except Exception as e:
        return {"status": "error", "message": str(e), "download_dir": get_download_dir()}

@app.get("/api/files")
async def list_files():
    files = []
    d_dir = get_download_dir()
    for p in Path(d_dir).glob("*"):
        # Hide temp working files from the gallery
        if (p.is_file() and 
            not p.name.endswith(".part") and 
            not p.name.endswith(".ytdl") and 
            not p.name.endswith(".aria2") and 
            not p.name.startswith("_temp_") and 
            not p.name.startswith("_chk_")):
            stat = p.stat()
            size_mb = round(stat.st_size / (1024 * 1024), 2)
            clean_name = p.name
            if clean_name.startswith('_upload_'):
                clean_name = clean_name.replace('_upload_', '')
            elif clean_name.startswith('_up_comp_'):
                clean_name = clean_name.replace('_up_comp_', '')
            elif clean_name.startswith('_up_conv_'):
                clean_name = clean_name.replace('_up_conv_', '')
            
            ext = p.suffix.lower().replace('.', '')
            file_category = 'other'
            if ext in ['mp4', 'mkv', 'webm', 'mov', 'avi', 'ts']:
                file_category = 'video'
            elif ext in ['mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg', 'opus']:
                file_category = 'audio'
            elif ext in ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']:
                file_category = 'image'
            elif ext in ['srt', 'vtt', 'sub', 'txt']:
                file_category = 'subtitle'

            files.append({
                "name": p.name,
                "display_name": clean_name,
                "ext": ext,
                "category": file_category,
                "size_mb": size_mb,
                "created": stat.st_mtime,
                "path": f"/api/file/{p.name}"
            })
    files.sort(key=lambda x: x["created"], reverse=True)
    return files

@app.get("/api/file/{filename}")
async def stream_file(filename: str):
    file_path = os.path.join(get_download_dir(), filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, filename=filename)

@app.post("/api/open_folder")
async def open_folder():
    d_dir = get_download_dir()
    try:
        if sys.platform == "win32":
            os.startfile(d_dir)
        return {"status": "ok", "path": d_dir}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.delete("/api/file/{filename}")
async def delete_file(filename: str):
    file_path = os.path.join(get_download_dir(), filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="File not found")

@app.post("/api/transcribe")
async def transcribe_media(
    url: Optional[str] = None,
    language: Optional[str] = "auto",
    output_fmt: Optional[str] = "srt",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    file: Optional[UploadFile] = File(None)
):
    saved_file_path = None
    if file and file.filename:
        d_dir = get_download_dir()
        saved_file_path = os.path.join(d_dir, f"_upload_{file.filename}")
        with open(saved_file_path, "wb") as out_f:
            content = await file.read()
            out_f.write(content)

    task_id = media_tools.start_transcription_task(
        file_path=saved_file_path,
        url=url,
        language=language or "auto",
        output_fmt=output_fmt or "srt",
        start_time=start_time,
        end_time=end_time
    )
    return {"task_id": task_id, "status": "started"}

@app.post("/api/compress")
async def compress_media(
    profile: Optional[str] = "Balanced",
    level: Optional[int] = None,
    files: Optional[List[UploadFile]] = File(None)
):
    saved_paths = []
    d_dir = get_download_dir()
    if files:
        for f in files:
            if f.filename:
                path = os.path.join(d_dir, f"_up_comp_{f.filename}")
                with open(path, "wb") as out_f:
                    out_f.write(await f.read())
                saved_paths.append(path)

    if not saved_paths:
        raise HTTPException(status_code=400, detail="No files uploaded to compress")

    task_id = media_tools.start_compression_task(saved_paths, profile=profile or "Balanced", compression_level=level)
    return {"task_id": task_id, "status": "started", "file_count": len(saved_paths)}

@app.post("/api/convert")
async def convert_media(
    target_format: Optional[str] = "mp4",
    quality: Optional[str] = "balanced",
    resolution: Optional[str] = "original",
    audio_handling: Optional[str] = "keep",
    files: Optional[List[UploadFile]] = File(None)
):
    saved_paths = []
    d_dir = get_download_dir()
    if files:
        for f in files:
            if f.filename:
                path = os.path.join(d_dir, f"_up_conv_{f.filename}")
                with open(path, "wb") as out_f:
                    out_f.write(await f.read())
                saved_paths.append(path)

    if not saved_paths:
        raise HTTPException(status_code=400, detail="No files uploaded to convert")

    task_id = media_tools.start_conversion_task(
        file_paths=saved_paths,
        target_format=target_format or "mp4",
        quality=quality or "balanced",
        resolution=resolution or "original",
        audio_handling=audio_handling or "keep"
    )
    return {"task_id": task_id, "status": "started", "file_count": len(saved_paths)}

@app.get("/api/media_task/{task_id}")
async def get_media_task_status(task_id: str):
    task = media_tools.media_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task
