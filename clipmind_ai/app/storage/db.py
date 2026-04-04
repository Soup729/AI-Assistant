import sqlite3
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

class DatabaseManager:
    def __init__(self, db_path: str = "app_data.db"):
        self.db_path = db_path
        self._init_db()
    
    def _get_connection(self):
        return sqlite3.connect(self.db_path)
    
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
