"""
db.py — SQLite persistence for Sentinel Earn (standalone).
"""
import sqlite3
import os
from typing import Optional, List, Dict
from contextlib import contextmanager

from sentinel_earn.config import get_data_dir as _config_data_dir


def get_data_dir() -> str:
    return str(_config_data_dir())


_DATA_DIR = get_data_dir()
DB_PATH = os.path.join(_DATA_DIR, "sentinel_earn.db")


def _ensure_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def get_conn():
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist."""
    _ensure_dir()
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS opportunities (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                source           TEXT    NOT NULL,
                title            TEXT    NOT NULL,
                repo_url         TEXT    DEFAULT '',
                issue_url        TEXT    UNIQUE NOT NULL,
                bounty_amount    REAL    DEFAULT 0,
                currency         TEXT    DEFAULT 'USD',
                complexity_score REAL    DEFAULT 5,
                status           TEXT    DEFAULT 'new',
                created_at       TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS submissions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id   INTEGER NOT NULL,
                pr_url           TEXT,
                status           TEXT    DEFAULT 'pending',
                submitted_at     TEXT    DEFAULT (datetime('now')),
                merged_at        TEXT,
                payout_confirmed INTEGER DEFAULT 0,
                earnings         REAL    DEFAULT 0,
                FOREIGN KEY (opportunity_id) REFERENCES opportunities(id)
            );

            CREATE TABLE IF NOT EXISTS agent_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id   INTEGER,
                event            TEXT    NOT NULL,
                detail           TEXT    DEFAULT '',
                timestamp        TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS patch_queue (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id   INTEGER NOT NULL,
                status           TEXT    DEFAULT 'pending',
                patch_json       TEXT    DEFAULT '',
                test_output      TEXT    DEFAULT '',
                branch_name      TEXT    DEFAULT '',
                created_at       TEXT    DEFAULT (datetime('now')),
                updated_at       TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (opportunity_id) REFERENCES opportunities(id)
            );
        """)


# ─── Opportunities CRUD ───────────────────────────────────────────────────────

def insert_opportunity(source: str, title: str, repo_url: str, issue_url: str,
                       bounty_amount: float = 0, currency: str = "USD",
                       complexity_score: float = 5) -> Optional[int]:
    """Insert a new opportunity. Returns id or None on duplicate."""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO opportunities
                   (source, title, repo_url, issue_url, bounty_amount, currency, complexity_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (source, title, repo_url or "", issue_url,
                 bounty_amount, currency, complexity_score)
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None  # Duplicate issue_url — skip silently


def cleanup_garbage_opportunities() -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            DELETE FROM opportunities
             WHERE bounty_amount <= 0
                OR lower(repo_url || ' ' || issue_url) LIKE '%bountyscout%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%bounty-board%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%bounties%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%securebananalabs%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%bug-bounty%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%marketplace-service-template%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%socialfi%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%airdrop%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%quest%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%nips%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%rustchain-bounties%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%rustchain%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%cognitive-os%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%bignuten%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%moorcheh-ai/memanto%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%conversejs/converse.js/issues/2481%'
                OR lower(repo_url || ' ' || issue_url) LIKE '%/x/y%'
                OR lower(title) LIKE '%bounty alert%'
                OR lower(title) LIKE '%artifact%'
                OR lower(title) LIKE '%test bounty%'
                OR lower(title) LIKE '%new issue for a bounty%'
                OR lower(title) LIKE '%low handing fruit%'
                OR lower(title) LIKE '%pixel art%'
                OR lower(title) LIKE '%technical poem%'
                OR lower(title) LIKE '%grandma%'
                OR lower(title) LIKE '%good first issues, bounties%'
            """
        )
        return cur.rowcount


def get_opportunity(opp_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM opportunities WHERE id = ?", (opp_id,)).fetchone()
        return dict(row) if row else None


def get_top_opportunity() -> Optional[Dict]:
    """Highest-scored unstarted opportunity with complexity <= 5."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM opportunities
               WHERE status IN ('new', 'approved') AND complexity_score <= 5
               ORDER BY bounty_amount DESC, complexity_score ASC
               LIMIT 1"""
        ).fetchone()
        return dict(row) if row else None


def update_opportunity_status(opp_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE opportunities SET status = ? WHERE id = ?", (status, opp_id))


def list_opportunities(status: Optional[str] = None, limit: int = 100) -> List[Dict]:
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM opportunities WHERE status = ? ORDER BY bounty_amount DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM opportunities ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def count_opportunities_by_status() -> Dict[str, int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM opportunities GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}


# ─── Submissions CRUD ─────────────────────────────────────────────────────────

def insert_submission(opportunity_id: int, pr_url: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO submissions (opportunity_id, pr_url) VALUES (?, ?)",
            (opportunity_id, pr_url)
        )
        return cur.lastrowid


def get_submission(sub_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        return dict(row) if row else None


def get_submission_by_opportunity(opp_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM submissions WHERE opportunity_id = ? ORDER BY submitted_at DESC LIMIT 1",
            (opp_id,)
        ).fetchone()
        return dict(row) if row else None


def list_submissions(status: Optional[str] = None) -> List[Dict]:
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                """SELECT s.*, o.title, o.bounty_amount, o.repo_url, o.issue_url
                   FROM submissions s JOIN opportunities o ON s.opportunity_id = o.id
                   WHERE s.status = ? ORDER BY s.submitted_at DESC""",
                (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT s.*, o.title, o.bounty_amount, o.repo_url, o.issue_url
                   FROM submissions s JOIN opportunities o ON s.opportunity_id = o.id
                   ORDER BY s.submitted_at DESC"""
            ).fetchall()
        return [dict(r) for r in rows]


def update_submission_status(sub_id: int, status: str,
                              merged_at: Optional[str] = None,
                              earnings: Optional[float] = None):
    with get_conn() as conn:
        if merged_at and earnings is not None:
            conn.execute(
                "UPDATE submissions SET status=?, merged_at=?, payout_confirmed=1, earnings=? WHERE id=?",
                (status, merged_at, earnings, sub_id)
            )
        elif merged_at:
            conn.execute(
                "UPDATE submissions SET status=?, merged_at=? WHERE id=?",
                (status, merged_at, sub_id)
            )
        else:
            conn.execute("UPDATE submissions SET status=? WHERE id=?", (status, sub_id))


def list_pending_submissions() -> List[Dict]:
    """All submissions still waiting for PR outcome."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.*, o.issue_url, o.repo_url, o.bounty_amount
               FROM submissions s JOIN opportunities o ON s.opportunity_id = o.id
               WHERE s.status IN ('pending', 'open') AND s.pr_url IS NOT NULL"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_earnings_summary() -> Dict:
    with get_conn() as conn:
        confirmed = conn.execute(
            "SELECT COALESCE(SUM(earnings), 0) as total FROM submissions WHERE payout_confirmed = 1"
        ).fetchone()["total"]

        pending_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM submissions WHERE status IN ('pending', 'open')"
        ).fetchone()["cnt"]

        merged_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM submissions WHERE status = 'merged'"
        ).fetchone()["cnt"]

        total_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM submissions"
        ).fetchone()["cnt"]

        pending_value = conn.execute(
            """SELECT COALESCE(SUM(o.bounty_amount), 0) as total
               FROM submissions s JOIN opportunities o ON s.opportunity_id = o.id
               WHERE s.status IN ('pending', 'open')"""
        ).fetchone()["total"]

        merge_rate = round(merged_count / total_count * 100, 1) if total_count > 0 else 0

        return {
            "confirmed_earnings": confirmed,
            "pending_count": pending_count,
            "pending_value": pending_value,
            "merged_count": merged_count,
            "total_submissions": total_count,
            "merge_rate": merge_rate,
        }


# ─── Agent Log CRUD ───────────────────────────────────────────────────────────

def log_event(event: str, detail: str = "", opportunity_id: Optional[int] = None):
    """Insert a log entry. Never raises — log failures are silent."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO agent_log (opportunity_id, event, detail) VALUES (?, ?, ?)",
                (opportunity_id, event, str(detail)[:2000])
            )
    except Exception as exc:
        print(f"[LOG ERROR] {exc}")


def get_recent_logs(limit: int = 50) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_logs_for_opportunity(opp_id: int) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_log WHERE opportunity_id = ? ORDER BY timestamp",
            (opp_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_events(limit: int = 50) -> List[Dict]:
    """Get recent events from agent_log (alias for get_recent_logs)."""
    return get_recent_logs(limit)


def create_forge_task(prompt: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO forge_tasks (prompt) VALUES (?)",
            (prompt,),
        )
        return cur.lastrowid


def update_forge_task(task_id: int, status: str, output_path: str = "",
                      result_json: str = "", error: str = ""):
    with get_conn() as conn:
        conn.execute(
            """UPDATE forge_tasks
               SET status=?, output_path=?, result_json=?, error=?, updated_at=datetime('now')
               WHERE id=?""",
            (status, output_path, result_json, error, task_id),
        )


def get_forge_task(task_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM forge_tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None


def list_forge_tasks(status: Optional[str] = None, limit: int = 100) -> List[Dict]:
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM forge_tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM forge_tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
