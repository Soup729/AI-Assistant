import asyncio
import hashlib
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import httpx
import numpy as np

from app.storage.config import config_manager
from app.storage.db import db_manager
from app.utils.logger import logger
from app.utils.runtime_paths import get_user_data_dir


class RAGService:
    def __init__(self):
        self._state_lock = threading.RLock()
        self._index_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._enabled = False
        self._notes_dir = ""
        self._embedding_api_url = "https://api.openai.com/v1"
        self._embedding_api_key = ""
        self._embedding_model = "text-embedding-3-small"

        self._vector_index_path = self._resolve_index_path()
        self._vector_map: Dict[int, np.ndarray] = {}
        self._rowids = np.array([], dtype=np.int64)
        self._vectors = np.empty((0, 0), dtype=np.float32)
        self._status = "未启用"

        self._poll_interval_seconds = 30
        self._max_chunk_chars = 1200
        self._chunk_overlap_chars = 120
        self._embedding_batch_size = 16
        self._http_timeout = 30.0
        self._rrf_k = 60
        self._min_vector_score = 0.42
        self._max_keyword_tokens = 10

        self.reload_config()
        self._load_vector_index()

    def _resolve_index_path(self) -> Path:
        default_path = get_user_data_dir() / "rag" / "vector_index.npz"
        try:
            default_path.parent.mkdir(parents=True, exist_ok=True)
            return default_path
        except OSError:
            fallback = Path(tempfile.gettempdir()) / "ClipMindAI" / "rag" / "vector_index.npz"
            fallback.parent.mkdir(parents=True, exist_ok=True)
            return fallback

    def reload_config(self):
        cfg = config_manager.config
        with self._state_lock:
            self._enabled = bool(getattr(cfg, "enable_rag", False))
            self._notes_dir = str(getattr(cfg, "rag_notes_dir", "") or "").strip()
            self._embedding_api_url = str(
                getattr(cfg, "rag_embedding_api_url", "https://api.openai.com/v1") or "https://api.openai.com/v1"
            ).strip()
            self._embedding_api_key = str(getattr(cfg, "rag_embedding_api_key", "") or "").strip()
            self._embedding_model = str(getattr(cfg, "rag_embedding_model", "text-embedding-3-small") or "").strip()
            if not self._embedding_model:
                self._embedding_model = "text-embedding-3-small"

            if not self._is_valid_api_key_for_header(self._embedding_api_key):
                # Keep empty here; _resolve_embedding_api_key() will fallback to active model key.
                self._embedding_api_key = ""

        if self._enabled:
            self.start()
            self.trigger_reindex()
        else:
            self.stop()
            with self._state_lock:
                self._status = "未启用"

    def start(self):
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.set()
            self._thread = threading.Thread(target=self._run_index_loop, name="RAGIndexer", daemon=True)
            self._thread.start()
            self._status = "索引线程已启动"

    def stop(self):
        with self._state_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
            self._wake_event.set()

        thread.join(timeout=2.0)
        with self._state_lock:
            self._thread = None

    def trigger_reindex(self):
        self._wake_event.set()

    def is_enabled(self) -> bool:
        with self._state_lock:
            return self._enabled

    def is_config_ready(self) -> bool:
        with self._state_lock:
            if not self._enabled:
                return False
            notes_ready = bool(self._notes_dir) and Path(self._notes_dir).exists()
            embedding_ready = bool(self._resolve_embedding_api_key() and self._embedding_model and self._embedding_api_url)
            return notes_ready and embedding_ready

    def get_status(self) -> str:
        with self._state_lock:
            return self._status

    def _resolve_embedding_api_key(self) -> str:
        """
        1) Prefer dedicated RAG embedding key.
        2) Fallback to active LLM key when RAG key is missing/invalid.
        """
        rag_key = (self._embedding_api_key or "").strip()
        if self._is_valid_api_key_for_header(rag_key):
            return rag_key

        fallback_key = (config_manager.get_active_model_profile().api_key or "").strip()
        if self._is_valid_api_key_for_header(fallback_key):
            return fallback_key
        return ""

    def _is_valid_api_key_for_header(self, key: str) -> bool:
        if not key:
            return False
        if "\n" in key or "\r" in key:
            return False
        # Corrupted values are often pasted logs or traces.
        lowered = key.lower()
        if "traceback" in lowered or "| error" in lowered:
            return False
        try:
            key.encode("ascii")
        except UnicodeEncodeError:
            return False
        return True

    async def asearch(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        text = (query or "").strip()
        if not text or not self.is_config_ready():
            return []

        try:
            vector_limit = max(6, top_k * 4)
            keyword_limit = max(6, top_k * 4)
            keyword_query = self._build_fts_query(text)

            vector_task = asyncio.create_task(self._asearch_vector(text, vector_limit))
            keyword_task = asyncio.create_task(
                asyncio.to_thread(self._search_keyword, keyword_query, keyword_limit)
            )
            vector_hits, keyword_hits = await asyncio.gather(vector_task, keyword_task)
            fused = self._rrf_fuse(vector_hits, keyword_hits, top_k=max(top_k * 2, top_k))
            return self._filter_relevant_hits(text, fused, top_k=top_k)
        except Exception as e:
            logger.warning(f"RAG 异步检索失败，已降级普通对话: {e}")
            return []

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        text = (query or "").strip()
        if not text or not self.is_config_ready():
            return []

        try:
            vector_limit = max(6, top_k * 4)
            keyword_limit = max(6, top_k * 4)
            keyword_query = self._build_fts_query(text)

            with ThreadPoolExecutor(max_workers=2) as pool:
                future_vector = pool.submit(self._search_vector, text, vector_limit)
                future_keyword = pool.submit(self._search_keyword, keyword_query, keyword_limit)
                vector_hits = future_vector.result()
                keyword_hits = future_keyword.result()

            fused = self._rrf_fuse(vector_hits, keyword_hits, top_k=max(top_k * 2, top_k))
            return self._filter_relevant_hits(text, fused, top_k=top_k)
        except Exception as e:
            logger.warning(f"RAG 检索失败，已降级普通对话: {e}")
            return []

    def build_context(self, hits: Sequence[Dict[str, Any]]) -> str:
        if not hits:
            return ""

        lines: List[str] = []
        for idx, item in enumerate(hits, start=1):
            doc_name = str(item.get("doc_name", "") or "")
            heading = str(item.get("heading_path", "") or "")
            text = str(item.get("chunk_text", "") or "").strip()
            if not text:
                continue
            lines.append(f"[{idx}] 来源: {doc_name}")
            if heading:
                lines.append(f"标题路径: {heading}")
            lines.append(f"内容: {text}")
            lines.append("")
        return "\n".join(lines).strip()

    def collect_sources(self, hits: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
        output: List[Dict[str, str]] = []
        seen = set()
        for item in hits:
            doc_path = str(item.get("doc_path", "") or "")
            if not doc_path or doc_path in seen:
                continue
            seen.add(doc_path)
            output.append({"doc_path": doc_path, "doc_name": str(item.get("doc_name", "") or Path(doc_path).name)})
        return output

    def _run_index_loop(self):
        while not self._stop_event.is_set():
            if not self._enabled:
                self._wake_event.wait(timeout=1.0)
                self._wake_event.clear()
                continue

            if not self.is_config_ready():
                with self._state_lock:
                    self._status = "RAG 已启用，但配置不完整"
                self._wake_event.wait(timeout=5.0)
                self._wake_event.clear()
                continue

            try:
                self._sync_notes_index()
                with self._state_lock:
                    self._status = f"就绪（{int(self._rowids.shape[0])} 段）"
            except Exception as e:
                logger.error(f"RAG 后台索引失败: {e}")
                with self._state_lock:
                    self._status = f"索引失败: {e}"

            self._wake_event.wait(timeout=self._poll_interval_seconds)
            self._wake_event.clear()

    def _sync_notes_index(self):
        notes_root = Path(self._notes_dir).resolve()
        if not notes_root.exists():
            return

        fs_docs: Dict[str, Tuple[float, int]] = {}
        for path in notes_root.rglob("*.md"):
            if not path.is_file():
                continue
            stat = path.stat()
            fs_docs[str(path.resolve())] = (float(stat.st_mtime), int(stat.st_size))

        db_states = db_manager.get_rag_document_states()
        removed = [doc_path for doc_path in db_states.keys() if doc_path not in fs_docs]
        for doc_path in removed:
            stale_ids = db_manager.remove_rag_document(doc_path)
            self._remove_vectors(stale_ids)

        changed = [
            doc_path
            for doc_path, state in fs_docs.items()
            if doc_path not in db_states or db_states[doc_path] != state
        ]
        if not self._vector_map and fs_docs:
            changed = list(fs_docs.keys())

        for doc_path in changed:
            mtime, file_size = fs_docs[doc_path]
            path = Path(doc_path)
            markdown = path.read_text(encoding="utf-8", errors="ignore")
            chunks = self._chunk_markdown(markdown, doc_path)
            if not chunks:
                stale_ids = db_manager.remove_rag_document(doc_path)
                self._remove_vectors(stale_ids)
                continue

            embeddings = self._embed_texts([chunk["chunk_text"] for chunk in chunks])
            if embeddings.shape[0] != len(chunks):
                raise RuntimeError("embedding 返回数量异常")

            stale_ids, new_ids = db_manager.replace_rag_document(
                doc_path=doc_path,
                doc_name=path.name,
                mtime=mtime,
                file_size=file_size,
                chunks=chunks,
            )
            self._remove_vectors(stale_ids)
            self._upsert_vectors(new_ids, embeddings)

        self._prune_stale_vectors()

    def _remove_vectors(self, row_ids: Iterable[int]):
        stale = {int(item) for item in row_ids}
        if not stale:
            return
        with self._index_lock:
            for row_id in stale:
                self._vector_map.pop(row_id, None)
            self._rebuild_index_arrays_locked()
            self._save_vector_index_locked()

    def _prune_stale_vectors(self):
        valid_ids = set(db_manager.get_all_rag_chunk_ids())
        with self._index_lock:
            stale_ids = [row_id for row_id in self._vector_map.keys() if row_id not in valid_ids]
            if not stale_ids:
                return
            for row_id in stale_ids:
                self._vector_map.pop(row_id, None)
            self._rebuild_index_arrays_locked()
            self._save_vector_index_locked()

    def _upsert_vectors(self, row_ids: Sequence[int], vectors: np.ndarray):
        if not row_ids:
            return
        normalized = self._normalize_rows(vectors)
        with self._index_lock:
            for idx, row_id in enumerate(row_ids):
                self._vector_map[int(row_id)] = normalized[idx]
            self._rebuild_index_arrays_locked()
            self._save_vector_index_locked()

    def _rebuild_index_arrays_locked(self):
        if not self._vector_map:
            self._rowids = np.array([], dtype=np.int64)
            self._vectors = np.empty((0, 0), dtype=np.float32)
            return
        items = sorted(self._vector_map.items(), key=lambda item: item[0])
        self._rowids = np.array([item[0] for item in items], dtype=np.int64)
        self._vectors = np.vstack([item[1] for item in items]).astype(np.float32, copy=False)

    def _save_vector_index_locked(self):
        np.savez_compressed(self._vector_index_path, rowids=self._rowids, vectors=self._vectors)

    def _load_vector_index(self):
        if not self._vector_index_path.exists():
            return
        try:
            loaded = np.load(self._vector_index_path, allow_pickle=False)
            rowids = np.asarray(loaded.get("rowids", np.array([], dtype=np.int64)), dtype=np.int64)
            vectors = np.asarray(loaded.get("vectors", np.empty((0, 0), dtype=np.float32)), dtype=np.float32)
            if rowids.ndim != 1:
                rowids = rowids.reshape(-1)
            if vectors.ndim == 1 and rowids.size > 0:
                vectors = vectors.reshape(1, -1)
            if vectors.size and rowids.size != vectors.shape[0]:
                raise ValueError("rowids 与 vectors 数量不一致")

            with self._index_lock:
                self._rowids = rowids
                self._vectors = vectors
                self._vector_map = {int(rowids[i]): vectors[i].astype(np.float32, copy=False) for i in range(rowids.size)}
        except Exception as e:
            logger.warning(f"加载本地 RAG 向量索引失败，将自动重建: {e}")
            with self._index_lock:
                self._vector_map = {}
                self._rowids = np.array([], dtype=np.int64)
                self._vectors = np.empty((0, 0), dtype=np.float32)

    def _chunk_markdown(self, markdown: str, doc_path: str) -> List[Dict[str, Any]]:
        heading_stack: List[str] = []
        paragraph_lines: List[str] = []
        output: List[Dict[str, Any]] = []
        ordinal = 0

        def flush_paragraph():
            nonlocal ordinal
            if not paragraph_lines:
                return
            raw_text = "\n".join(paragraph_lines).strip()
            paragraph_lines.clear()
            if not raw_text:
                return
            heading_path = " / ".join(heading_stack)
            for piece in self._split_text(raw_text):
                piece = piece.strip()
                if not piece:
                    continue
                chunk_hash = hashlib.sha1(f"{doc_path}|{heading_path}|{piece}".encode("utf-8")).hexdigest()
                output.append(
                    {
                        "heading_path": heading_path,
                        "chunk_text": piece,
                        "chunk_hash": chunk_hash,
                        "ordinal": ordinal,
                    }
                )
                ordinal += 1

        for line in markdown.splitlines():
            match = re.match(r"^(#{1,6})\s+(.*)$", line)
            if match:
                flush_paragraph()
                level = len(match.group(1))
                title = match.group(2).strip()
                if title:
                    heading_stack = heading_stack[: max(0, level - 1)]
                    heading_stack.append(title)
                continue

            if not line.strip():
                flush_paragraph()
                continue

            paragraph_lines.append(line.rstrip())

        flush_paragraph()
        return output

    def _split_text(self, text: str) -> List[str]:
        if len(text) <= self._max_chunk_chars:
            return [text]

        pieces: List[str] = []
        start = 0
        max_chars = self._max_chunk_chars
        overlap = self._chunk_overlap_chars
        text_len = len(text)

        while start < text_len:
            end = min(text_len, start + max_chars)
            if end < text_len:
                search_start = start + int(max_chars * 0.6)
                split_pos = text.rfind("\n", search_start, end)
                if split_pos <= start:
                    split_pos = text.rfind("。", search_start, end)
                if split_pos <= start:
                    split_pos = text.rfind(".", search_start, end)
                if split_pos > start:
                    end = split_pos + 1

            piece = text[start:end].strip()
            if piece:
                pieces.append(piece)
            if end >= text_len:
                break
            start = max(0, end - overlap)

        return pieces

    def _embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)

        endpoint = self._build_embedding_endpoint(self._embedding_api_url)
        api_key = self._resolve_embedding_api_key()
        if not api_key:
            raise RuntimeError("RAG Embedding API Key 无效，请在设置中重新填写或检查主模型 Key。")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        vectors: List[np.ndarray] = []
        with httpx.Client(timeout=self._http_timeout, follow_redirects=True) as client:
            for batch in self._batched(texts, self._embedding_batch_size):
                payload = {"model": self._embedding_model, "input": list(batch)}
                response = client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                items = data.get("data", [])
                if len(items) != len(batch):
                    raise RuntimeError("embedding 接口返回数量异常")

                for item in sorted(items, key=lambda x: int(x.get("index", 0))):
                    vectors.append(np.asarray(item.get("embedding", []), dtype=np.float32))

        if not vectors:
            return np.empty((0, 0), dtype=np.float32)
        matrix = np.vstack(vectors).astype(np.float32, copy=False)
        return self._normalize_rows(matrix)

    async def _aembed_texts(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)

        endpoint = self._build_embedding_endpoint(self._embedding_api_url)
        api_key = self._resolve_embedding_api_key()
        if not api_key:
            raise RuntimeError("RAG Embedding API Key 无效，请在设置中重新填写或检查主模型 Key。")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        vectors: List[np.ndarray] = []

        async with httpx.AsyncClient(timeout=self._http_timeout, follow_redirects=True) as client:
            for batch in self._batched(texts, self._embedding_batch_size):
                payload = {"model": self._embedding_model, "input": list(batch)}
                response = await client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                items = data.get("data", [])
                if len(items) != len(batch):
                    raise RuntimeError("embedding 接口返回数量异常")

                for item in sorted(items, key=lambda x: int(x.get("index", 0))):
                    vectors.append(np.asarray(item.get("embedding", []), dtype=np.float32))

        if not vectors:
            return np.empty((0, 0), dtype=np.float32)
        matrix = np.vstack(vectors).astype(np.float32, copy=False)
        return self._normalize_rows(matrix)

    async def _asearch_vector(self, query: str, limit: int) -> List[Dict[str, Any]]:
        with self._index_lock:
            if self._rowids.size == 0 or self._vectors.size == 0:
                return []
            rowids = self._rowids.copy()
            vectors = self._vectors.copy()

        q_vec = await self._aembed_texts([query])
        if q_vec.size == 0:
            return []
        q = q_vec[0]

        scores = vectors @ q
        top_n = min(int(limit), int(scores.shape[0]))
        if top_n <= 0:
            return []

        if top_n == scores.shape[0]:
            top_indices = np.argsort(-scores)
        else:
            candidate = np.argpartition(-scores, top_n - 1)[:top_n]
            top_indices = candidate[np.argsort(-scores[candidate])]

        top_pairs = [(int(rowids[idx]), float(scores[idx])) for idx in top_indices]
        top_ids = [item[0] for item in top_pairs]
        rows = await asyncio.to_thread(db_manager.get_rag_chunks_by_ids, top_ids)
        row_map = {int(item["id"]): item for item in rows}

        hits: List[Dict[str, Any]] = []
        for rank, (chunk_id, score) in enumerate(top_pairs, start=1):
            row = row_map.get(chunk_id)
            if row is None:
                continue
            item = dict(row)
            item["rank"] = rank
            item["vector_score"] = score
            hits.append(item)
        return hits

    def _search_vector(self, query: str, limit: int) -> List[Dict[str, Any]]:
        with self._index_lock:
            if self._rowids.size == 0 or self._vectors.size == 0:
                return []
            rowids = self._rowids.copy()
            vectors = self._vectors.copy()

        q_vec = self._embed_texts([query])
        if q_vec.size == 0:
            return []
        q = q_vec[0]

        scores = vectors @ q
        top_n = min(int(limit), int(scores.shape[0]))
        if top_n <= 0:
            return []

        if top_n == scores.shape[0]:
            top_indices = np.argsort(-scores)
        else:
            candidate = np.argpartition(-scores, top_n - 1)[:top_n]
            top_indices = candidate[np.argsort(-scores[candidate])]

        top_pairs = [(int(rowids[idx]), float(scores[idx])) for idx in top_indices]
        top_ids = [item[0] for item in top_pairs]
        rows = db_manager.get_rag_chunks_by_ids(top_ids)
        row_map = {int(item["id"]): item for item in rows}

        hits: List[Dict[str, Any]] = []
        for rank, (chunk_id, score) in enumerate(top_pairs, start=1):
            row = row_map.get(chunk_id)
            if row is None:
                continue
            item = dict(row)
            item["rank"] = rank
            item["vector_score"] = score
            hits.append(item)
        return hits

    def _search_keyword(self, query: str, limit: int) -> List[Dict[str, Any]]:
        rows = db_manager.search_rag_keywords(query, limit=limit)
        hits = []
        for rank, row in enumerate(rows, start=1):
            item = dict(row)
            item["rank"] = rank
            hits.append(item)
        return hits

    def _rrf_fuse(
        self,
        vector_hits: Sequence[Dict[str, Any]],
        keyword_hits: Sequence[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        if not vector_hits and not keyword_hits:
            return []

        scores: Dict[int, float] = {}
        payload: Dict[int, Dict[str, Any]] = {}

        for rank, item in enumerate(vector_hits, start=1):
            cid = int(item.get("id", 0))
            if cid <= 0:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (self._rrf_k + rank)
            payload[cid] = dict(item)

        for rank, item in enumerate(keyword_hits, start=1):
            cid = int(item.get("id", 0))
            if cid <= 0:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (self._rrf_k + rank)
            payload[cid] = dict(item)

        ordered = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)[: int(top_k)]
        merged: List[Dict[str, Any]] = []
        for cid in ordered:
            row = payload.get(cid)
            if row is None:
                continue
            row["rrf_score"] = float(scores[cid])
            merged.append(row)
        return merged

    def _build_fts_query(self, text: str) -> str:
        tokens = re.findall(r"[\u4e00-\u9fff]{1,}|[A-Za-z0-9_]{2,}", text)
        if not tokens:
            return text
        return " OR ".join(f'"{token}"' for token in tokens[: self._max_keyword_tokens])

    def _extract_query_tokens(self, text: str) -> List[str]:
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", text or "")
        if not tokens:
            return []

        stop_words = {
            "什么",
            "怎么",
            "如何",
            "一下",
            "一个",
            "可以",
            "请问",
            "问题",
            "这个",
            "那个",
            "what",
            "how",
            "why",
            "when",
            "where",
            "who",
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
        }
        cleaned: List[str] = []
        for token in tokens[: self._max_keyword_tokens]:
            norm = token.lower()
            if norm in stop_words:
                continue
            cleaned.append(token)
        return cleaned

    def _keyword_overlap(self, query_tokens: Sequence[str], chunk_text: str) -> int:
        if not query_tokens or not chunk_text:
            return 0

        lowered_chunk = chunk_text.lower()
        overlap = 0
        for token in query_tokens:
            if re.match(r"^[A-Za-z0-9_]+$", token):
                if token.lower() in lowered_chunk:
                    overlap += 1
            else:
                if token in chunk_text:
                    overlap += 1
        return overlap

    def _as_float(self, value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    def _is_relevant_hit(self, query_tokens: Sequence[str], hit: Dict[str, Any]) -> bool:
        chunk_text = str(hit.get("chunk_text", "") or "")
        if not chunk_text.strip():
            return False

        overlap = self._keyword_overlap(query_tokens, chunk_text)
        if overlap >= 2:
            return True
        if overlap >= 1 and len(query_tokens) <= 4:
            return True

        vector_score = self._as_float(hit.get("vector_score"))
        if vector_score is not None and vector_score >= self._min_vector_score:
            return True

        return False

    def _filter_relevant_hits(self, query: str, hits: Sequence[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
        if not hits:
            return []

        query_tokens = self._extract_query_tokens(query)
        selected: List[Dict[str, Any]] = []
        for item in hits:
            if self._is_relevant_hit(query_tokens, item):
                selected.append(item)
            if len(selected) >= int(top_k):
                break
        return selected

    def _build_embedding_endpoint(self, base_url: str) -> str:
        base = (base_url or "").strip().rstrip("/")
        if not base:
            return "https://api.openai.com/v1/embeddings"
        if base.endswith("/embeddings"):
            return base
        if base.endswith("/v1"):
            return f"{base}/embeddings"
        if "/v1/" in base:
            return base
        if "openai" in base.lower():
            return f"{base}/v1/embeddings"
        return f"{base}/embeddings"

    def _normalize_rows(self, matrix: np.ndarray) -> np.ndarray:
        if matrix.size == 0:
            return matrix.astype(np.float32, copy=False)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-12)
        return (matrix / norms).astype(np.float32, copy=False)

    def _batched(self, items: Sequence[str], size: int):
        current: List[str] = []
        for item in items:
            current.append(item)
            if len(current) >= size:
                yield current
                current = []
        if current:
            yield current


rag_service = RAGService()
