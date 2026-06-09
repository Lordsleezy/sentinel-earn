"""
queue_manager.py — Persistent Task Queue for SentinelAI (Phase 7)
SQLite-backed queue with retry handling, priorities, and crash recovery
"""
import sqlite3
from typing import Optional, List, Dict, Any
from datetime import datetime
from contextlib import contextmanager
import json
import os

from sentinel_earn.db import get_conn, _ensure_dir


# ─── Queue Schema ─────────────────────────────────────────────────────────────

def init_queue_tables():
    """Create task queue tables if they don't exist."""
    _ensure_dir()
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS task_queue (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type        TEXT    NOT NULL,  -- 'scan', 'execute', 'check_pr'
                opportunity_id   INTEGER,
                priority         INTEGER DEFAULT 5,  -- 1=highest, 10=lowest
                status           TEXT    DEFAULT 'pending',  -- pending, running, completed, failed, cancelled
                worker_id        TEXT,
                retry_count      INTEGER DEFAULT 0,
                max_retries      INTEGER DEFAULT 3,
                task_data        TEXT,  -- JSON
                created_at       TEXT    DEFAULT (datetime('now')),
                started_at       TEXT,
                completed_at     TEXT,
                error_message    TEXT,
                FOREIGN KEY (opportunity_id) REFERENCES opportunities(id)
            );

            CREATE INDEX IF NOT EXISTS idx_queue_status ON task_queue(status);
            CREATE INDEX IF NOT EXISTS idx_queue_priority ON task_queue(priority, created_at);
            CREATE INDEX IF NOT EXISTS idx_queue_worker ON task_queue(worker_id);
        """)


# ─── Queue Operations ─────────────────────────────────────────────────────────

def enqueue_task(task_type: str, priority: int = 5, opportunity_id: Optional[int] = None,
                 task_data: Optional[Dict] = None, max_retries: int = 3) -> int:
    """Add a task to the queue. Returns task_id."""
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO task_queue (task_type, opportunity_id, priority, task_data, max_retries)
               VALUES (?, ?, ?, ?, ?)""",
            (task_type, opportunity_id, priority, json.dumps(task_data or {}), max_retries)
        )
        return cur.lastrowid


def dequeue_task(worker_id: str, task_types: Optional[List[str]] = None) -> Optional[Dict]:
    """Get next pending task for worker. Returns task dict or None."""
    with get_conn() as conn:
        # Build query based on task types filter
        if task_types:
            placeholders = ','.join('?' * len(task_types))
            query = f"""
                SELECT * FROM task_queue
                WHERE status = 'pending' AND task_type IN ({placeholders})
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
            """
            row = conn.execute(query, task_types).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM task_queue
                   WHERE status = 'pending'
                   ORDER BY priority ASC, created_at ASC
                   LIMIT 1"""
            ).fetchone()
        
        if not row:
            return None
        
        task = dict(row)
        
        # Mark as running and assign to worker
        conn.execute(
            """UPDATE task_queue
               SET status = 'running', worker_id = ?, started_at = datetime('now')
               WHERE id = ?""",
            (worker_id, task['id'])
        )
        
        # Parse JSON data
        try:
            task['task_data'] = json.loads(task['task_data']) if task['task_data'] else {}
        except:
            task['task_data'] = {}
        
        return task


def complete_task(task_id: int, success: bool = True, error_message: str = ""):
    """Mark task as completed or failed."""
    status = 'completed' if success else 'failed'
    with get_conn() as conn:
        conn.execute(
            """UPDATE task_queue
               SET status = ?, completed_at = datetime('now'), error_message = ?
               WHERE id = ?""",
            (status, error_message, task_id)
        )


def retry_task(task_id: int, error_message: str = "") -> bool:
    """Retry a failed task if retries remain. Returns True if retried, False if max retries reached."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT retry_count, max_retries FROM task_queue WHERE id = ?",
            (task_id,)
        ).fetchone()
        
        if not row:
            return False
        
        retry_count = row['retry_count']
        max_retries = row['max_retries']
        
        if retry_count >= max_retries:
            # Max retries reached, mark as failed
            conn.execute(
                """UPDATE task_queue
                   SET status = 'failed', error_message = ?, completed_at = datetime('now')
                   WHERE id = ?""",
                (f"Max retries ({max_retries}) reached. Last error: {error_message}", task_id)
            )
            return False
        else:
            # Increment retry count and reset to pending
            conn.execute(
                """UPDATE task_queue
                   SET status = 'pending', retry_count = retry_count + 1,
                       worker_id = NULL, started_at = NULL, error_message = ?
                   WHERE id = ?""",
                (error_message, task_id)
            )
            return True


def cancel_task(task_id: int):
    """Cancel a pending or running task."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE task_queue
               SET status = 'cancelled', completed_at = datetime('now')
               WHERE id = ? AND status IN ('pending', 'running')""",
            (task_id,)
        )


def get_task(task_id: int) -> Optional[Dict]:
    """Get task by ID."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM task_queue WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        task = dict(row)
        try:
            task['task_data'] = json.loads(task['task_data']) if task['task_data'] else {}
        except:
            task['task_data'] = {}
        return task


def list_tasks(status: Optional[str] = None, limit: int = 100) -> List[Dict]:
    """List tasks, optionally filtered by status."""
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM task_queue WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM task_queue ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        
        tasks = []
        for row in rows:
            task = dict(row)
            try:
                task['task_data'] = json.loads(task['task_data']) if task['task_data'] else {}
            except:
                task['task_data'] = {}
            tasks.append(task)
        return tasks


def get_queue_stats() -> Dict:
    """Get queue statistics."""
    with get_conn() as conn:
        stats = {}
        
        # Count by status
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM task_queue GROUP BY status"
        ).fetchall()
        for row in rows:
            stats[f"{row['status']}_count"] = row['cnt']
        
        # Total tasks
        total = conn.execute("SELECT COUNT(*) as cnt FROM task_queue").fetchone()
        stats['total_tasks'] = total['cnt']
        
        # Pending by priority
        pending_high = conn.execute(
            "SELECT COUNT(*) as cnt FROM task_queue WHERE status = 'pending' AND priority <= 3"
        ).fetchone()
        stats['pending_high_priority'] = pending_high['cnt']
        
        # Running tasks
        running = conn.execute(
            "SELECT COUNT(*) as cnt FROM task_queue WHERE status = 'running'"
        ).fetchone()
        stats['running_count'] = running['cnt']
        
        # Failed tasks
        failed = conn.execute(
            "SELECT COUNT(*) as cnt FROM task_queue WHERE status = 'failed'"
        ).fetchone()
        stats['failed_count'] = failed['cnt']
        
        return stats


def get_stale_tasks(timeout_minutes: int = 30) -> List[Dict]:
    """Get tasks that have been running for too long (likely hung)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM task_queue
               WHERE status = 'running'
               AND started_at < datetime('now', '-' || ? || ' minutes')""",
            (timeout_minutes,)
        ).fetchall()
        
        tasks = []
        for row in rows:
            task = dict(row)
            try:
                task['task_data'] = json.loads(task['task_data']) if task['task_data'] else {}
            except:
                task['task_data'] = {}
            tasks.append(task)
        return tasks


def reset_stale_tasks(timeout_minutes: int = 30) -> int:
    """Reset stale running tasks back to pending. Returns count of reset tasks."""
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE task_queue
               SET status = 'pending', worker_id = NULL, started_at = NULL,
                   error_message = 'Reset due to timeout'
               WHERE status = 'running'
               AND started_at < datetime('now', '-' || ? || ' minutes')""",
            (timeout_minutes,)
        )
        return cur.rowcount


def cleanup_old_tasks(days: int = 30) -> int:
    """Delete completed/failed tasks older than specified days. Returns count deleted."""
    with get_conn() as conn:
        cur = conn.execute(
            """DELETE FROM task_queue
               WHERE status IN ('completed', 'failed', 'cancelled')
               AND completed_at < datetime('now', '-' || ? || ' days')""",
            (days,)
        )
        return cur.rowcount


def get_worker_tasks(worker_id: str) -> List[Dict]:
    """Get all tasks assigned to a worker."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM task_queue WHERE worker_id = ? ORDER BY started_at DESC",
            (worker_id,)
        ).fetchall()
        
        tasks = []
        for row in rows:
            task = dict(row)
            try:
                task['task_data'] = json.loads(task['task_data']) if task['task_data'] else {}
            except:
                task['task_data'] = {}
            tasks.append(task)
        return tasks


def clear_worker_tasks(worker_id: str):
    """Reset all running tasks for a worker back to pending (for worker restart)."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE task_queue
               SET status = 'pending', worker_id = NULL, started_at = NULL,
                   error_message = 'Worker restarted'
               WHERE worker_id = ? AND status = 'running'""",
            (worker_id,)
        )


def get_queue_depth() -> int:
    """Get count of pending tasks."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM task_queue WHERE status = 'pending'"
        ).fetchone()
        return row['cnt']


def is_queue_full(max_size: int = 500) -> bool:
    """Check if queue has reached max size."""
    return get_queue_depth() >= max_size


# ─── Initialization ───────────────────────────────────────────────────────────

def initialize_queue():
    """Initialize the task queue system."""
    init_queue_tables()
    return True
