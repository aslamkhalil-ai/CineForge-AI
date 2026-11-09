
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent / "cineforge.db"


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            idea TEXT NOT NULL,
            genre TEXT NOT NULL,
            duration TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Draft',
            script TEXT DEFAULT '',
            scenes TEXT DEFAULT '',
            error TEXT DEFAULT '',
            voice_path TEXT DEFAULT '',
            subtitle_path TEXT DEFAULT '',
            visual_video_path TEXT DEFAULT '',
            final_video_path TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """)

        # Safely migrate existing databases
        columns = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(projects)").fetchall()
        ]

        if "voice_path" not in columns:
            conn.execute(
                "ALTER TABLE projects ADD COLUMN voice_path TEXT DEFAULT ''"
            )
        if "subtitle_path" not in columns:
            conn.execute(
                "ALTER TABLE projects ADD COLUMN subtitle_path TEXT DEFAULT ''"
            )
        if "visual_video_path" not in columns:
            conn.execute(
                "ALTER TABLE projects ADD COLUMN visual_video_path TEXT DEFAULT ''"
            )
        if "final_video_path" not in columns:
            conn.execute(
                "ALTER TABLE projects ADD COLUMN final_video_path TEXT DEFAULT ''"
            )


def create_project(title, idea, genre, duration):
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO projects
            (title, idea, genre, duration, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                title,
                idea,
                genre,
                str(duration),
                "Generating script",
                datetime.now().strftime("%Y-%m-%d %H:%M")
            )
        )
        return cur.lastrowid


def get_projects():
    with connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM projects ORDER BY id DESC"
            ).fetchall()
        ]


def get_project(project_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id=?",
            (project_id,)
        ).fetchone()

        return dict(row) if row else None


def update_project(project_id, **fields):
    allowed = {
        "status",
        "script",
        "scenes",
        "error",
        "voice_path",
        "subtitle_path",
        "visual_video_path",
        "final_video_path"
    }

    fields = {
        key: value
        for key, value in fields.items()
        if key in allowed
    }

    if not fields:
        return

    assignments = ", ".join(
        f"{key}=?" for key in fields
    )

    with connect() as conn:
        conn.execute(
            f"UPDATE projects SET {assignments} WHERE id=?",
            (*fields.values(), project_id)
        )
