import os
import sqlite3
import shutil
import time
import tempfile
from pathlib import Path
from typing import List, Dict, Any

from app.utils.runtime_paths import APP_NAME, get_project_root, get_user_data_dir

class DatabaseManager:
    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else get_user_data_dir() / "app_data.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_db()
        try:
            self._init_db()
        except sqlite3.OperationalError as exc:
            if "readonly database" not in str(exc).lower():
                raise
            self._recover_from_readonly_db()
    
    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _migrate_legacy_db(self):
        legacy_paths = [
            get_project_root() / "app_data.db",
            Path.cwd() / "app_data.db",
        ]
        if self.db_path.exists():
            return
        for legacy_db_path in legacy_paths:
            if legacy_db_path == self.db_path or not legacy_db_path.exists():
                continue
            try:
                shutil.copyfile(legacy_db_path, self.db_path)
                try:
                    self.db_path.chmod(0o666)
                except OSError:
                    pass
                return
            except OSError:
                continue

    def _recover_from_readonly_db(self):
        fallback_names = [
            f"{self.db_path.stem}_runtime_{os.getpid()}{self.db_path.suffix}",
            f"{self.db_path.stem}_runtime_{int(time.time() * 1000)}{self.db_path.suffix}",
            f"{self.db_path.stem}_recovered{self.db_path.suffix}",
        ]
        fallback_roots = [
            self.db_path.parent,
            Path(tempfile.gettempdir()) / APP_NAME,
        ]

        for fallback_root in fallback_roots:
            fallback_root.mkdir(parents=True, exist_ok=True)
            for fallback_name in fallback_names:
                fallback_db_path = fallback_root / fallback_name
                try:
                    if self.db_path.exists():
                        try:
                            shutil.copyfile(self.db_path, fallback_db_path)
                        except OSError:
                            fallback_db_path.touch(exist_ok=True)
                    else:
                        fallback_db_path.touch(exist_ok=True)

                    try:
                        fallback_db_path.chmod(0o666)
                    except OSError:
                        pass

                    self.db_path = fallback_db_path
                    self._init_db()
                    return
                except OSError:
                    continue

        raise sqlite3.OperationalError("unable to recover writable database path")
    
    def _init_db(self):
        """初始化数据库表"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 历史记录表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT,
                    content TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    session_id TEXT
                )
            ''')
            
            # Prompt 模板表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    category TEXT,
                    system_prompt TEXT,
                    user_prompt_template TEXT,
                    enable_search BOOLEAN DEFAULT 0
                )
            ''')
            
            # 插入默认模板 (如果不存在)
            self._insert_default_templates(cursor)
            
            conn.commit()
    
    def _insert_default_templates(self, cursor):
        defaults = [
            ("通用问答", "Assistant", "你是一个全能助手，请用简洁明了的语言回答我的问题。", "{text}", 0),
            ("解释说明", "Explainer", "你是一个知识广博的解释专家，请解释下面这段文字的含义、背景及重点。", "请解释以下内容：\n{text}", 0),
            ("中英翻译", "Translator", "你是一个资深的翻译家，请将以下文本准确、自然地翻译成目标语言（默认为中文）。", "请翻译以下内容：\n{text}", 0),
            ("文本润色", "Polisher", "你是一个资深的文字编辑，请在保持原意的基础上对以下文本进行润色，提高表达的流畅度、美感和逻辑性。", "请润色以下内容：\n{text}", 0),
        ]
        cursor.executemany('''
            INSERT OR IGNORE INTO templates (name, category, system_prompt, user_prompt_template, enable_search)
            VALUES (?, ?, ?, ?, ?)
        ''', defaults)
    
    def add_history(self, role: str, content: str, session_id: str):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO history (role, content, session_id) VALUES (?, ?, ?)",
                (role, content, session_id)
            )
    
    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT role, content, timestamp FROM history WHERE session_id = ? ORDER BY timestamp ASC",
                (session_id,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_templates(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM templates")
            return [dict(row) for row in cursor.fetchall()]

db_manager = DatabaseManager()
