import sqlite3
import os
import time
from typing import Dict, List, Optional

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "downloads.db"))
_db_initialized = False

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database tables, WAL mode, and indexes for high-throughput durable task persistence."""
    global _db_initialized
    if _db_initialized:
        return
    with get_connection() as conn:
        # High concurrency & performance PRAGMAs
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                title TEXT,
                thumbnail TEXT,
                format_id TEXT,
                referer TEXT,
                cookies TEXT,
                out_path TEXT,
                filename TEXT,
                status TEXT,
                percent REAL DEFAULT 0.0,
                speed TEXT DEFAULT '0 KB/s',
                eta TEXT DEFAULT '--:--',
                size_str TEXT DEFAULT '',
                elapsed TEXT DEFAULT '00:00',
                error TEXT,
                retries INTEGER DEFAULT 0,
                created_at REAL,
                updated_at REAL
            )
        """)
        # Ensure new columns exist for existing databases
        cursor = conn.execute("PRAGMA table_info(jobs)")
        existing_cols = {row["name"] for row in cursor.fetchall()}
        if "size_str" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN size_str TEXT DEFAULT ''")
        if "elapsed" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN elapsed TEXT DEFAULT '00:00'")

        # Index for ultra-fast polling & status filtering
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.commit()
    _db_initialized = True

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Retrieve persistent configuration value by key."""
    init_db()
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

def set_setting(key: str, value: str):
    """Set persistent configuration value by key."""
    init_db()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, str(value)))
        conn.commit()

def save_job(job_data: dict):
    now = time.time()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO jobs (
                id, url, title, thumbnail, format_id, referer, cookies,
                out_path, filename, status, percent, speed, eta, size_str, elapsed, error, retries, created_at, updated_at
            ) VALUES (
                :id, :url, :title, :thumbnail, :format_id, :referer, :cookies,
                :out_path, :filename, :status, :percent, :speed, :eta, :size_str, :elapsed, :error, :retries, :created_at, :updated_at
            )
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                thumbnail = COALESCE(excluded.thumbnail, jobs.thumbnail),
                status = excluded.status,
                percent = excluded.percent,
                speed = excluded.speed,
                eta = excluded.eta,
                size_str = excluded.size_str,
                elapsed = excluded.elapsed,
                filename = COALESCE(excluded.filename, jobs.filename),
                out_path = COALESCE(excluded.out_path, jobs.out_path),
                error = excluded.error,
                retries = excluded.retries,
                updated_at = excluded.updated_at
        """, {
            'id': job_data['id'],
            'url': job_data.get('url', ''),
            'title': job_data.get('title', 'Video'),
            'thumbnail': job_data.get('thumbnail'),
            'format_id': job_data.get('format_id', 'best'),
            'referer': job_data.get('referer'),
            'cookies': job_data.get('cookies'),
            'out_path': job_data.get('out_path'),
            'filename': job_data.get('filename'),
            'status': job_data.get('status', 'starting'),
            'percent': job_data.get('percent', 0.0),
            'speed': job_data.get('speed', '0 KB/s'),
            'eta': job_data.get('eta', '--:--'),
            'size_str': job_data.get('size_str', ''),
            'elapsed': job_data.get('elapsed', '00:00'),
            'error': job_data.get('error'),
            'retries': job_data.get('retries', 0),
            'created_at': job_data.get('created_at', now),
            'updated_at': now
        })
        conn.commit()

def update_job_progress(job_id: str, status: str = None, percent: float = None, speed: str = None, eta: str = None, size_str: str = None, elapsed: str = None, error: str = None):
    now = time.time()
    query = "UPDATE jobs SET updated_at = ?"
    params = [now]
    
    if status is not None:
        query += ", status = ?"
        params.append(status)
    if percent is not None:
        query += ", percent = ?"
        params.append(percent)
    if speed is not None:
        query += ", speed = ?"
        params.append(speed)
    if eta is not None:
        query += ", eta = ?"
        params.append(eta)
    if size_str is not None:
        query += ", size_str = ?"
        params.append(size_str)
    if elapsed is not None:
        query += ", elapsed = ?"
        params.append(elapsed)
    if error is not None:
        query += ", error = ?"
        params.append(error)

    query += " WHERE id = ?"
    params.append(job_id)

    with get_connection() as conn:
        conn.execute(query, params)
        conn.commit()

def get_job(job_id: str) -> Optional[dict]:
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        return dict(row) if row else None

def get_all_jobs() -> List[dict]:
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM jobs ORDER BY updated_at DESC")
        return [dict(row) for row in cur.fetchall()]

def delete_job(job_id: str):
    with get_connection() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()

def recover_interrupted_jobs() -> List[dict]:
    """Find any jobs that were running when the server shut down or crashed."""
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM jobs WHERE status IN ('downloading', 'starting', 'retrying', 'converting', 'interrupted')")
        interrupted = [dict(row) for row in cur.fetchall()]
        return interrupted

# Auto-initialize on import
init_db()
