import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from app.utils.logger import logger
from app.utils.runtime_paths import get_project_root, get_user_data_dir


class DatabaseManager:
    def __init__(self, db_path: str | None = None):
        target_path = Path(db_path) if db_path else (get_user_data_dir() / "app_data.db")
        if db_path is None:
            self._migrate_legacy_db(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(target_path)
        self._fts_enabled = False
        try:
            self._init_db()
        except sqlite3.OperationalError as e:
            logger.warning(f"数据库初始化失败，切换到临时目录: {e}")
            fallback_path = self._fallback_db_path()
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = str(fallback_path)
            self._init_db()

    def _migrate_legacy_db(self, target_path: Path):
        if target_path.exists():
            return

        legacy_paths = [
            get_project_root() / "app_data.db",
            Path.cwd() / "app_data.db",
        ]
        for source_path in legacy_paths:
            if not source_path.exists():
                continue
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target_path)
                return
            except OSError:
                continue

    def _fallback_db_path(self) -> Path:
        return Path(tempfile.gettempdir()) / "ClipMindAI" / "app_data.db"

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.OperationalError:
            pass
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT,
                    content TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    session_id TEXT
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    category TEXT,
                    system_prompt TEXT,
                    user_prompt_template TEXT,
                    enable_search BOOLEAN DEFAULT 0
                )
                """
            )

            self._insert_default_templates(cursor)
            self._init_rag_tables(cursor)
            conn.commit()

    def _insert_default_templates(self, cursor):
        defaults = [
            (
                "通用问答",
                "Assistant",
                "你是一个全能助手，请用简洁明了的语言回答我的问题。",
                "{text}",
                0,
            ),
            (
                "解释说明",
                "Explainer",
                "你是一个知识广博的解释专家，请解释下面这段文字的含义、背景及重点。",
                "请解释以下内容：\n{text}",
                0,
            ),
            (
                "中英翻译",
                "Translator",
                "你是一个资深的翻译家，请将以下文本准确、自然地翻译成目标语言（默认为中文）。",
                "请翻译以下内容：\n{text}",
                0,
            ),
            (
                "文本润色",
                "Polisher",
                "你是一个资深的文字编辑，请在保持原意的基础上对以下文本进行润色，提高表达的流畅度、美感和逻辑性。",
                "请润色以下内容：\n{text}",
                0,
            ),
        ]
        cursor.executemany(
            """
            INSERT OR IGNORE INTO templates (name, category, system_prompt, user_prompt_template, enable_search)
            VALUES (?, ?, ?, ?, ?)
            """,
            defaults,
        )

    def _init_rag_tables(self, cursor):
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_documents (
                doc_path TEXT PRIMARY KEY,
                doc_name TEXT NOT NULL,
                mtime REAL NOT NULL,
                file_size INTEGER NOT NULL,
                indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_path TEXT NOT NULL,
                doc_name TEXT NOT NULL,
                heading_path TEXT DEFAULT '',
                chunk_text TEXT NOT NULL,
                chunk_hash TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                FOREIGN KEY (doc_path) REFERENCES rag_documents(doc_path) ON DELETE CASCADE
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_doc_path ON rag_chunks(doc_path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_hash ON rag_chunks(chunk_hash)")

        try:
            cursor.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts
                USING fts5(chunk_text, content='rag_chunks', content_rowid='id')
                """
            )
            cursor.execute(
                """
                CREATE TRIGGER IF NOT EXISTS rag_chunks_ai AFTER INSERT ON rag_chunks BEGIN
                    INSERT INTO rag_chunks_fts(rowid, chunk_text) VALUES (new.id, new.chunk_text);
                END
                """
            )
            cursor.execute(
                """
                CREATE TRIGGER IF NOT EXISTS rag_chunks_ad AFTER DELETE ON rag_chunks BEGIN
                    INSERT INTO rag_chunks_fts(rag_chunks_fts, rowid, chunk_text)
                    VALUES('delete', old.id, old.chunk_text);
                END
                """
            )
            cursor.execute(
                """
                CREATE TRIGGER IF NOT EXISTS rag_chunks_au AFTER UPDATE ON rag_chunks BEGIN
                    INSERT INTO rag_chunks_fts(rag_chunks_fts, rowid, chunk_text)
                    VALUES('delete', old.id, old.chunk_text);
                    INSERT INTO rag_chunks_fts(rowid, chunk_text) VALUES (new.id, new.chunk_text);
                END
                """
            )
            self._fts_enabled = True
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS5 初始化失败，RAG 关键词检索将回退到 LIKE: {e}")
            self._fts_enabled = False

    def add_history(self, role: str, content: str, session_id: str):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO history (role, content, session_id) VALUES (?, ?, ?)",
                (role, content, session_id),
            )

    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT role, content, timestamp FROM history WHERE session_id = ? ORDER BY timestamp ASC",
                (session_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_templates(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM templates")
            return [dict(row) for row in cursor.fetchall()]

    def is_rag_fts_enabled(self) -> bool:
        return self._fts_enabled

    def get_rag_document_states(self) -> Dict[str, Tuple[float, int]]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT doc_path, mtime, file_size FROM rag_documents")
            return {row[0]: (float(row[1]), int(row[2])) for row in cursor.fetchall()}

    def remove_rag_document(self, doc_path: str) -> List[int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM rag_chunks WHERE doc_path = ?", (doc_path,))
            stale_ids = [int(row[0]) for row in cursor.fetchall()]
            cursor.execute("DELETE FROM rag_chunks WHERE doc_path = ?", (doc_path,))
            cursor.execute("DELETE FROM rag_documents WHERE doc_path = ?", (doc_path,))
            conn.commit()
            return stale_ids

    def replace_rag_document(
        self,
        doc_path: str,
        doc_name: str,
        mtime: float,
        file_size: int,
        chunks: Iterable[Dict[str, Any]],
    ) -> Tuple[List[int], List[int]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT id FROM rag_chunks WHERE doc_path = ?", (doc_path,))
            stale_ids = [int(row[0]) for row in cursor.fetchall()]
            cursor.execute("DELETE FROM rag_chunks WHERE doc_path = ?", (doc_path,))

            cursor.execute(
                """
                INSERT INTO rag_documents(doc_path, doc_name, mtime, file_size, indexed_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(doc_path) DO UPDATE SET
                    doc_name=excluded.doc_name,
                    mtime=excluded.mtime,
                    file_size=excluded.file_size,
                    indexed_at=CURRENT_TIMESTAMP
                """,
                (doc_path, doc_name, float(mtime), int(file_size)),
            )

            new_ids: List[int] = []
            for item in chunks:
                cursor.execute(
                    """
                    INSERT INTO rag_chunks(doc_path, doc_name, heading_path, chunk_text, chunk_hash, ordinal)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_path,
                        doc_name,
                        str(item.get("heading_path", "") or ""),
                        str(item.get("chunk_text", "") or ""),
                        str(item.get("chunk_hash", "") or ""),
                        int(item.get("ordinal", 0) or 0),
                    ),
                )
                new_ids.append(int(cursor.lastrowid))

            conn.commit()
            return stale_ids, new_ids

    def get_rag_chunks_by_ids(self, row_ids: Iterable[int]) -> List[Dict[str, Any]]:
        unique_ids = [int(item) for item in dict.fromkeys(row_ids)]
        if not unique_ids:
            return []

        placeholders = ",".join("?" for _ in unique_ids)
        sql = f"""
            SELECT id, doc_path, doc_name, heading_path, chunk_text, ordinal
            FROM rag_chunks
            WHERE id IN ({placeholders})
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, unique_ids).fetchall()

        row_map = {int(row["id"]): dict(row) for row in rows}
        ordered_rows: List[Dict[str, Any]] = []
        for row_id in unique_ids:
            row = row_map.get(int(row_id))
            if row is not None:
                ordered_rows.append(row)
        return ordered_rows

    def get_all_rag_chunk_ids(self) -> List[int]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT id FROM rag_chunks")
            return [int(row[0]) for row in cursor.fetchall()]

    def search_rag_keywords(self, match_query: str, limit: int = 8) -> List[Dict[str, Any]]:
        text_query = (match_query or "").strip()
        if not text_query:
            return []

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            if self._fts_enabled:
                try:
                    rows = conn.execute(
                        """
                        SELECT
                            c.id,
                            c.doc_path,
                            c.doc_name,
                            c.heading_path,
                            c.chunk_text,
                            bm25(rag_chunks_fts) AS score
                        FROM rag_chunks_fts
                        JOIN rag_chunks c ON c.id = rag_chunks_fts.rowid
                        WHERE rag_chunks_fts MATCH ?
                        ORDER BY score ASC
                        LIMIT ?
                        """,
                        (text_query, int(limit)),
                    ).fetchall()
                    return [dict(row) for row in rows]
                except sqlite3.OperationalError:
                    pass

            rows = conn.execute(
                """
                SELECT id, doc_path, doc_name, heading_path, chunk_text, 0.0 AS score
                FROM rag_chunks
                WHERE chunk_text LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (f"%{text_query}%", int(limit)),
            ).fetchall()
            return [dict(row) for row in rows]


db_manager = DatabaseManager()
