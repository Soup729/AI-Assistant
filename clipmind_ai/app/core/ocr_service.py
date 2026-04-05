import asyncio
import gc
import os
import re
import threading
from io import BytesIO
from typing import Any, Iterable, List, Optional, Sequence, Tuple

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np
import requests
from PIL import Image, ImageOps

from app.storage.config import config_manager
from app.utils.logger import logger

try:
    from rapidocr_onnxruntime import RapidOCR as RapidOCRBackend
except ImportError:  # pragma: no cover
    try:
        from rapidocr import RapidOCR as RapidOCRBackend
    except ImportError:  # pragma: no cover
        RapidOCRBackend = None


LOCAL_MIN_TEXT_LENGTH_FOR_CLOUD = 16
LOCAL_MIN_AVG_SCORE_FOR_CLOUD = 0.85
DEFAULT_CLOUD_IMAGE_FIELD = "image_file"
DEFAULT_CLOUD_TIMEOUT = 30
OCR_MAX_PIXELS = 2_100_000
OCR_RETRY_SCALES = (1.0, 0.82, 0.68, 0.55)

SENTENCE_END_RE = re.compile(r"[。！？!?；;：:，,、…)\]\}》」』”’\"']$")
BULLET_LINE_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)、]|[一二三四五六七八九十]+[.)、])\s+")
HEADING_LINE_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S+")
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
CODE_FENCE_RE = re.compile(r"^\s*```")
CONTINUATION_RE = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff(（“\"'‘【\[]")

ENGINE_LABELS = {
    "rapid": "RapidOCR",
    "cloud": "Cloud OCR API",
    "hybrid": "Hybrid OCR",
}


class OCRService:
    def __init__(self):
        self._status_lock = threading.Lock()
        self._engine_lock = threading.Lock()
        self._rapid_lock = threading.Lock()
        self._rapid_engine = None
        self._engine = self._normalize_engine(getattr(config_manager.config, "ocr_engine", "rapid"))
        self._status = self._build_status(initialized=False)

    def _normalize_engine(self, engine: str) -> str:
        value = (engine or "rapid").strip().lower()
        if value in {"fast", "accurate"}:
            return "rapid"
        if value in {"rapid", "cloud", "hybrid"}:
            return value
        return "rapid"

    def _engine_label(self, engine: Optional[str] = None) -> str:
        return ENGINE_LABELS.get(engine or self._engine, "RapidOCR")

    def _has_cloud_config(self) -> bool:
        return bool(self._cloud_api_url())

    def _build_status(self, initialized: bool) -> str:
        engine = self._engine
        label = self._engine_label(engine)
        if engine == "cloud":
            return "就绪(Cloud OCR API)" if self._has_cloud_config() else "未配置(Cloud OCR API)"
        if engine == "hybrid":
            return "就绪(Hybrid OCR)" if initialized else "未初始化(Hybrid OCR)"
        return f"{'就绪' if initialized else '未初始化'}({label})"

    def _set_status(self, status: str):
        with self._status_lock:
            self._status = status

    def get_status(self) -> str:
        with self._status_lock:
            return self._status

    def get_mode(self) -> str:
        with self._engine_lock:
            return self._engine

    def set_mode(self, mode: str):
        normalized = self._normalize_engine(mode)
        with self._engine_lock:
            self._engine = normalized
        self._update_status_after_mode_change()

    def _update_status_after_mode_change(self):
        if self._engine == "cloud":
            self._set_status(self._build_status(initialized=False))
            return
        with self._rapid_lock:
            initialized = self._rapid_engine is not None
        self._set_status(self._build_status(initialized=initialized))

    def _to_numpy_rgb(self, img_data):
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage

        if isinstance(img_data, QImage):
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            img_data.save(buffer, "PNG")
            pil = Image.open(BytesIO(buffer.data().data())).convert("RGB")
            return np.array(pil)

        if isinstance(img_data, Image.Image):
            return np.array(img_data.convert("RGB"))

        if isinstance(img_data, np.ndarray):
            if img_data.ndim == 2:
                return np.stack([img_data] * 3, axis=-1)
            if img_data.ndim == 3 and img_data.shape[2] == 4:
                return img_data[:, :, :3]
            return img_data

        raise ValueError("不支持的图像格式")

    def _preprocess_image(self, img_np: np.ndarray, scale_factor: float = 1.0) -> np.ndarray:
        pil = Image.fromarray(img_np).convert("RGB")
        pil = ImageOps.autocontrast(pil)
        width, height = pil.size
        short_side = min(width, height)

        upscale = 1.0
        if short_side < 720:
            upscale = min(1.5, 720 / max(short_side, 1))
        scale = max(0.25, float(scale_factor)) * upscale

        target_w = max(1, int(width * scale))
        target_h = max(1, int(height * scale))
        if target_w != width or target_h != height:
            pil = pil.resize((target_w, target_h), Image.Resampling.LANCZOS)

        cur_w, cur_h = pil.size
        pixels = cur_w * cur_h
        if pixels > OCR_MAX_PIXELS:
            ratio = (OCR_MAX_PIXELS / float(pixels)) ** 0.5
            cur_w = max(1, int(cur_w * ratio))
            cur_h = max(1, int(cur_h * ratio))
            pil = pil.resize((cur_w, cur_h), Image.Resampling.LANCZOS)

        return np.ascontiguousarray(np.array(pil))

    def _ensure_rapid_engine(self):
        if RapidOCRBackend is None:
            raise RuntimeError("未安装 RapidOCR 依赖，无法进行本地 OCR")

        with self._rapid_lock:
            if self._rapid_engine is not None:
                return self._rapid_engine
            logger.info("初始化 RapidOCR 引擎")
            self._rapid_engine = RapidOCRBackend()
            return self._rapid_engine

    def _is_bad_allocation_error(self, error: Exception) -> bool:
        text = str(error).lower()
        return "bad allocation" in text or "std::bad_alloc" in text

    def _extract_score(self, scores, index: int) -> Optional[float]:
        if scores is None:
            return None
        try:
            score = scores[index]
        except Exception:
            return None
        if isinstance(score, (int, float)):
            return float(score)
        return None

    def _clean_text(self, text: Any) -> str:
        if text is None:
            return ""
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="ignore")
        return " ".join(str(text).replace("\r", " ").split()).strip()

    def _join_texts(self, texts: Iterable[str]) -> str:
        cleaned = [self._clean_text(item) for item in texts]
        return "\n".join([item for item in cleaned if item]).strip()

    def _append_rapid_text(self, texts: List[str], scores: List[float], text: Any, score: Any = None):
        cleaned = self._clean_text(text)
        if not cleaned:
            return
        texts.append(cleaned)
        if isinstance(score, (int, float)):
            scores.append(float(score))

    def _walk_rapid_payload(self, payload: Any, texts: List[str], scores: List[float]):
        if payload is None:
            return

        if isinstance(payload, (bytes, str)):
            self._append_rapid_text(texts, scores, payload)
            return

        if isinstance(payload, (int, float)):
            # 过滤耗时等纯数字字段，避免被当成 OCR 文本。
            return

        if isinstance(payload, dict):
            for key in ("text", "txt", "content", "label", "result"):
                if key in payload:
                    self._walk_rapid_payload(payload.get(key), texts, scores)
            return

        if isinstance(payload, tuple) and len(payload) == 2 and isinstance(payload[0], (list, tuple)):
            # rapidocr 常见返回: (ocr_result, elapsed_list)
            self._walk_rapid_payload(payload[0], texts, scores)
            return

        if isinstance(payload, (list, tuple)):
            # 识别行结构: [bbox, text, score] / (bbox, text, score)
            if len(payload) >= 2 and isinstance(payload[1], (str, bytes)):
                score = payload[2] if len(payload) >= 3 else None
                self._append_rapid_text(texts, scores, payload[1], score)
                return

            for item in payload:
                self._walk_rapid_payload(item, texts, scores)

    def _extract_rapid_output(self, result) -> Tuple[List[str], List[float]]:
        texts: List[str] = []
        scores: List[float] = []

        txts = getattr(result, "txts", None)
        result_scores = getattr(result, "scores", None)
        if txts is not None:
            if isinstance(txts, (str, bytes)):
                one = self._clean_text(txts)
                return ([one] if one else []), []
            for idx, item in enumerate(txts):
                if isinstance(item, (str, bytes)):
                    score = self._extract_score(result_scores, idx)
                    self._append_rapid_text(texts, scores, item, score)
                    continue
                self._walk_rapid_payload(item, texts, scores)
            if texts:
                return texts, scores

        self._walk_rapid_payload(result, texts, scores)

        return texts, scores

    def _run_local_ocr(self, img_np: np.ndarray) -> Tuple[str, List[float]]:
        last_error: Optional[Exception] = None
        engine = self._ensure_rapid_engine()

        for idx, scale in enumerate(OCR_RETRY_SCALES, start=1):
            try:
                prepared = self._preprocess_image(img_np, scale_factor=scale)
                result = engine(prepared)
                texts, scores = self._extract_rapid_output(result)
                return self._join_texts(texts), scores
            except Exception as e:
                last_error = e
                if not self._is_bad_allocation_error(e):
                    raise

                logger.warning(
                    f"RapidOCR 内存不足，尝试降分辨率重试 ({idx}/{len(OCR_RETRY_SCALES)}, scale={scale:.2f})"
                )
                gc.collect()
                if idx == 1:
                    with self._rapid_lock:
                        self._rapid_engine = None
                    engine = self._ensure_rapid_engine()

        if last_error is not None:
            raise RuntimeError(f"RapidOCR 推理内存不足: {last_error}") from last_error
        return "", []

    def _compact_text(self, text: str) -> str:
        return "".join(text.split())

    def _should_enhance_with_cloud(self, local_text: str, scores: Sequence[float]) -> bool:
        if not self._has_cloud_config():
            return False
        if len(self._compact_text(local_text)) < LOCAL_MIN_TEXT_LENGTH_FOR_CLOUD:
            return True
        if scores:
            avg = sum(scores) / max(len(scores), 1)
            if avg < LOCAL_MIN_AVG_SCORE_FOR_CLOUD:
                return True
        return False

    def _needs_space_join(self, left: str, right: str) -> bool:
        if not left or not right:
            return False
        if re.search(r"[A-Za-z0-9]$", left) and re.match(r"^[A-Za-z0-9]", right):
            return True
        if re.search(r"[A-Za-z0-9]$", left) and re.match(r"^[\u4e00-\u9fff]", right):
            return True
        if re.search(r"[\u4e00-\u9fff]$", left) and re.match(r"^[A-Za-z0-9]", right):
            return True
        return False

    def _keep_hard_line_break(self, current: str, next_line: str) -> bool:
        if HEADING_LINE_RE.match(current) or HEADING_LINE_RE.match(next_line):
            return True
        if BULLET_LINE_RE.match(current) or BULLET_LINE_RE.match(next_line):
            return True
        if TABLE_LINE_RE.match(current) or TABLE_LINE_RE.match(next_line):
            return True
        if CODE_FENCE_RE.match(current) or CODE_FENCE_RE.match(next_line):
            return True
        return False

    def _smart_stitch_paragraph(self, paragraph: str) -> str:
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        if len(lines) <= 1:
            return lines[0] if lines else ""

        merged = lines[0]
        for next_line in lines[1:]:
            current = merged.rstrip()
            if not current:
                merged = next_line
                continue

            if self._keep_hard_line_break(current, next_line):
                merged += "\n" + next_line
                continue

            if current.endswith("-") and re.match(r"^[A-Za-z0-9]", next_line):
                merged = current[:-1] + next_line.lstrip()
                continue

            if not SENTENCE_END_RE.search(current) and CONTINUATION_RE.match(next_line):
                joiner = " " if self._needs_space_join(current, next_line) else ""
                merged = current + joiner + next_line.lstrip()
                continue

            merged += "\n" + next_line

        return merged

    def _smart_stitch_text(self, text: str) -> str:
        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        blocks = [item for item in re.split(r"\n\s*\n", normalized) if item.strip()]
        stitched = [self._smart_stitch_paragraph(block) for block in blocks]
        stitched = [item.strip() for item in stitched if item and item.strip()]
        compacted = "\n\n".join(stitched).strip()
        compacted = re.sub(r"[ \t]+\n", "\n", compacted)
        compacted = re.sub(r"\n{3,}", "\n\n", compacted)
        return compacted

    def _merge_texts(self, primary: str, secondary: str) -> str:
        lines: List[str] = []
        seen: set[str] = set()
        for block in (primary, secondary):
            for line in block.splitlines():
                cleaned = self._clean_text(line)
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    lines.append(cleaned)
        return "\n".join(lines).strip()

    def _cloud_api_url(self) -> str:
        return (getattr(config_manager.config, "ocr_cloud_api_url", "") or "").strip()

    def _cloud_api_key(self) -> str:
        return (getattr(config_manager.config, "ocr_cloud_api_key", "") or "").strip()

    def _cloud_image_field(self) -> str:
        field = (getattr(config_manager.config, "ocr_cloud_image_field", "") or "").strip()
        return field or DEFAULT_CLOUD_IMAGE_FIELD

    def _cloud_text_path(self) -> str:
        return (getattr(config_manager.config, "ocr_cloud_text_path", "") or "").strip()

    def _cloud_timeout(self) -> int:
        try:
            timeout = int(getattr(config_manager.config, "ocr_cloud_timeout", DEFAULT_CLOUD_TIMEOUT))
        except Exception:
            timeout = DEFAULT_CLOUD_TIMEOUT
        return max(5, timeout)

    def _image_to_png_bytes(self, img_np: np.ndarray) -> bytes:
        buffer = BytesIO()
        Image.fromarray(img_np).save(buffer, format="PNG")
        return buffer.getvalue()

    def _extract_by_path(self, payload: Any, path: str) -> Any:
        current = payload
        for part in path.split("."):
            part = part.strip()
            if not part:
                continue
            if isinstance(current, dict):
                current = current.get(part)
                continue
            if isinstance(current, (list, tuple)) and part.isdigit():
                idx = int(part)
                if 0 <= idx < len(current):
                    current = current[idx]
                    continue
                return None
            return None
        return current

    def _stringify_payload_text(self, payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="ignore")
        if isinstance(payload, str):
            return self._clean_text(payload)
        if isinstance(payload, (int, float)):
            return self._clean_text(payload)
        if isinstance(payload, dict):
            for key in ("text", "txts", "result", "data", "lines", "content"):
                if key in payload:
                    text = self._stringify_payload_text(payload.get(key))
                    if text:
                        return text
            joined = [self._stringify_payload_text(v) for v in payload.values()]
            return self._join_texts(joined)
        if isinstance(payload, (list, tuple)):
            joined = [self._stringify_payload_text(v) for v in payload]
            return self._join_texts(joined)
        return self._clean_text(payload)

    def _extract_cloud_text(self, payload: Any) -> str:
        text_path = self._cloud_text_path()
        if text_path:
            value = self._extract_by_path(payload, text_path)
            text = self._stringify_payload_text(value)
            if text:
                return text
        return self._stringify_payload_text(payload)

    def _run_cloud_ocr(self, img_np: np.ndarray) -> str:
        url = self._cloud_api_url()
        if not url:
            raise RuntimeError("未配置云端 OCR API 地址")

        headers = {"Accept": "application/json, text/plain;q=0.9, */*;q=0.8"}
        key = self._cloud_api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"

        files = {
            self._cloud_image_field(): ("screenshot.png", self._image_to_png_bytes(img_np), "image/png"),
        }

        response = requests.post(url, headers=headers, files=files, timeout=self._cloud_timeout())
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        return self._extract_cloud_text(payload)

    def preload(self):
        try:
            self.set_mode(getattr(config_manager.config, "ocr_engine", "rapid"))
            if self._engine == "cloud":
                self._set_status(self._build_status(initialized=False))
                return
            self._ensure_rapid_engine()
            self._set_status(self._build_status(initialized=True))
        except Exception as e:
            logger.warning(f"OCR 引擎预加载失败: {e}")
            self._set_status(self._build_status(initialized=False))

    def invalidate_cache(self):
        with self._rapid_lock:
            self._rapid_engine = None
        self._update_status_after_mode_change()

    def recognize_text(self, img_data) -> str:
        self.set_mode(getattr(config_manager.config, "ocr_engine", "rapid"))
        img_np = None
        try:
            img_np = self._to_numpy_rgb(img_data)
            self._set_status(f"识别中({self._engine_label()})")

            if self._engine == "cloud":
                text = self._smart_stitch_text(self._run_cloud_ocr(img_np))
                self._set_status(self._build_status(initialized=True))
                return text

            local_text, scores = self._run_local_ocr(img_np)
            if self._engine == "hybrid" and self._should_enhance_with_cloud(local_text, scores):
                try:
                    cloud_text = self._run_cloud_ocr(img_np)
                    if cloud_text.strip():
                        local_text = self._merge_texts(local_text, cloud_text)
                except Exception as cloud_error:
                    logger.warning(f"云端 OCR 增强失败，保留本地结果: {cloud_error}")

            local_text = self._smart_stitch_text(local_text)
            self._set_status(self._build_status(initialized=True))
            return local_text
        except Exception as e:
            if img_np is not None and self._is_bad_allocation_error(e) and self._has_cloud_config():
                logger.warning(f"OCR 本地推理内存不足，尝试云端兜底: {e}")
                try:
                    self._set_status("内存不足，尝试云端 OCR...")
                    cloud_text = self._smart_stitch_text(self._run_cloud_ocr(img_np))
                    self._set_status(self._build_status(initialized=True))
                    return cloud_text
                except Exception as cloud_error:
                    logger.error(f"OCR 云端兜底失败: {cloud_error}")

            self._set_status(f"识别失败({self._engine_label()})")
            logger.error(f"OCR 识别失败: {e}")
            if self._is_bad_allocation_error(e):
                return "Error: OCR 识别失败（内存不足，建议缩小截图区域后重试）"
            return f"Error: OCR 识别失败 ({str(e)})"

    async def arecognize_text(self, img_data) -> str:
        return await asyncio.to_thread(self.recognize_text, img_data)


ocr_service = OCRService()
