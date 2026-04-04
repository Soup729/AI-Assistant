import json
import threading
from io import BytesIO
from typing import Any, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import requests
from PIL import Image, ImageOps

from app.storage.config import config_manager
from app.utils.logger import logger

try:
    from rapidocr_onnxruntime import RapidOCR as RapidOCRBackend
except ImportError:  # pragma: no cover - optional dependency in local dev
    try:
        from rapidocr import RapidOCR as RapidOCRBackend
    except ImportError:  # pragma: no cover - optional dependency in local dev
        RapidOCRBackend = None


LOCAL_MIN_TEXT_LENGTH_FOR_CLOUD = 16
LOCAL_MIN_AVG_SCORE_FOR_CLOUD = 0.85
DEFAULT_CLOUD_IMAGE_FIELD = "image_file"
DEFAULT_CLOUD_TIMEOUT = 30

ENGINE_LABELS = {
    "rapid": "RapidOCR",
    "cloud": "云端 OCR API",
    "hybrid": "混合 OCR",
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
            if self._has_cloud_config():
                return "就绪(云端 OCR API)"
            return "未配置(云端 OCR API)"
        if engine == "hybrid":
            if self._has_cloud_config():
                return "就绪(混合 OCR)" if initialized else "未初始化(混合 OCR)"
            return "就绪(混合 OCR)" if initialized else "未初始化(混合 OCR)"
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
        """将 QImage / PIL / numpy 稳定转换为 RGB numpy 数组。"""
        from PySide6.QtGui import QImage
        from PySide6.QtCore import QBuffer, QIODevice

        if isinstance(img_data, QImage):
            qbuffer = QBuffer()
            qbuffer.open(QIODevice.WriteOnly)
            img_data.save(qbuffer, "PNG")
            pil_image = Image.open(BytesIO(qbuffer.data().data())).convert("RGB")
            return np.array(pil_image)

        if isinstance(img_data, Image.Image):
            return np.array(img_data.convert("RGB"))

        if isinstance(img_data, np.ndarray):
            if img_data.ndim == 2:
                return np.stack([img_data] * 3, axis=-1)
            if img_data.ndim == 3 and img_data.shape[2] == 4:
                return img_data[:, :, :3]
            return img_data

        raise ValueError("不支持的图像格式")

    def _preprocess_image(self, img_np: np.ndarray) -> np.ndarray:
        """对截图做轻量预处理，提升桌面截图识别稳定性。"""
        pil_image = Image.fromarray(img_np).convert("RGB")
        pil_image = ImageOps.autocontrast(pil_image)

        width, height = pil_image.size
        short_side = min(width, height)
        if short_side < 900:
            scale = min(2.0, 900 / max(short_side, 1))
            pil_image = pil_image.resize(
                (int(width * scale), int(height * scale)),
                Image.Resampling.LANCZOS,
            )

        return np.ascontiguousarray(np.array(pil_image))

    def _ensure_rapid_engine(self):
        if RapidOCRBackend is None:
            raise RuntimeError("未安装 RapidOCR 相关依赖，无法进行本地 OCR 识别")

        with self._rapid_lock:
            if self._rapid_engine is not None:
                return self._rapid_engine

            logger.info("初始化 RapidOCR 引擎")
            self._rapid_engine = RapidOCRBackend()
            return self._rapid_engine

    def _get_rapid_result(self, img_np: np.ndarray) -> Tuple[str, List[float]]:
        engine = self._ensure_rapid_engine()
        result = engine(self._preprocess_image(img_np))
        texts, scores = self._extract_rapid_output(result)
        return self._join_texts(texts), scores

    def _extract_rapid_output(self, result) -> Tuple[List[str], List[float]]:
        texts: List[str] = []
        scores: List[float] = []

        txts = getattr(result, "txts", None)
        result_scores = getattr(result, "scores", None)
        if txts is not None:
            if isinstance(txts, (str, bytes)):
                cleaned = self._clean_text(txts)
                if cleaned:
                    texts.append(cleaned)
                return texts, scores

            for index, item in enumerate(txts):
                cleaned = self._clean_text(item)
                if cleaned:
                    texts.append(cleaned)
                    score = self._extract_score(result_scores, index)
                    if score is not None:
                        scores.append(score)
            if texts:
                return texts, scores

        visited: set[int] = set()

        def visit(node):
            if node is None:
                return
            if isinstance(node, np.ndarray):
                return
            node_id = id(node)
            if node_id in visited:
                return
            visited.add(node_id)

            if isinstance(node, (str, bytes)):
                cleaned = self._clean_text(node)
                if cleaned:
                    texts.append(cleaned)
                return

            if isinstance(node, dict):
                if "txts" in node:
                    visit(node.get("txts"))
                    return
                if "text" in node and isinstance(node.get("text"), (str, bytes)):
                    cleaned = self._clean_text(node.get("text"))
                    if cleaned:
                        texts.append(cleaned)
                        score = node.get("score") or node.get("confidence")
                        if isinstance(score, (int, float)):
                            scores.append(float(score))
                    return
                if "result" in node:
                    visit(node.get("result"))
                if "data" in node:
                    visit(node.get("data"))
                for key, value in node.items():
                    if key.lower() in {"score", "confidence", "conf"}:
                        continue
                    visit(value)
                return

            if isinstance(node, (list, tuple)):
                if len(node) >= 2 and isinstance(node[1], (str, bytes)):
                    cleaned = self._clean_text(node[1])
                    if cleaned:
                        texts.append(cleaned)
                        if len(node) >= 3 and isinstance(node[2], (int, float)):
                            scores.append(float(node[2]))
                    return
                if len(node) == 2 and isinstance(node[0], (str, bytes)) and isinstance(node[1], (int, float)):
                    cleaned = self._clean_text(node[0])
                    if cleaned:
                        texts.append(cleaned)
                        scores.append(float(node[1]))
                    return
                for item in node:
                    visit(item)
                return

            if hasattr(node, "txts"):
                visit(getattr(node, "txts"))
                return
            if hasattr(node, "text"):
                visit(getattr(node, "text"))
                return
            if hasattr(node, "result"):
                visit(getattr(node, "result"))
                return
            if hasattr(node, "__dict__"):
                for value in node.__dict__.values():
                    visit(value)

        visit(result)
        return texts, scores

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
            try:
                text = text.decode("utf-8", errors="ignore")
            except Exception:
                text = str(text)
        return " ".join(str(text).replace("\r", " ").replace("\n", " ").split()).strip()

    def _join_texts(self, texts: Iterable[str]) -> str:
        cleaned = [self._clean_text(text) for text in texts]
        cleaned = [text for text in cleaned if text]
        return "\n".join(cleaned).strip()

    def _compact_text(self, text: str) -> str:
        return "".join(text.split())

    def _should_enhance_with_cloud(self, local_text: str, scores: Sequence[float]) -> bool:
        if not self._cloud_api_url():
            return False

        compact_length = len(self._compact_text(local_text))
        if compact_length < LOCAL_MIN_TEXT_LENGTH_FOR_CLOUD:
            return True

        if scores:
            avg_score = sum(scores) / max(len(scores), 1)
            if avg_score < LOCAL_MIN_AVG_SCORE_FOR_CLOUD:
                return True

        return False

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
        return getattr(config_manager.config, "ocr_cloud_api_url", "").strip()

    def _cloud_api_key(self) -> str:
        return getattr(config_manager.config, "ocr_cloud_api_key", "").strip()

    def _cloud_image_field(self) -> str:
        return (getattr(config_manager.config, "ocr_cloud_image_field", "") or DEFAULT_CLOUD_IMAGE_FIELD).strip() or DEFAULT_CLOUD_IMAGE_FIELD

    def _cloud_text_path(self) -> str:
        return getattr(config_manager.config, "ocr_cloud_text_path", "").strip()

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
                index = int(part)
                if 0 <= index < len(current):
                    current = current[index]
                    continue
                return None
            return None
        return current

    def _extract_cloud_text(self, payload: Any) -> str:
        text_path = self._cloud_text_path()
        if text_path:
            value = self._extract_by_path(payload, text_path)
            text = self._stringify_payload_text(value)
            if text:
                return text

        return self._stringify_payload_text(payload)

    def _stringify_payload_text(self, payload: Any) -> str:
        if payload is None:
            return ""

        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8", errors="ignore")
            except Exception:
                payload = str(payload)

        if isinstance(payload, str):
            return self._clean_text(payload)

        if isinstance(payload, (int, float)):
            return self._clean_text(payload)

        if isinstance(payload, dict):
            preferred_keys = (
                "text",
                "txts",
                "result",
                "data",
                "lines",
                "ocr_result",
                "content",
            )
            for key in preferred_keys:
                if key in payload:
                    text = self._stringify_payload_text(payload.get(key))
                    if text:
                        return text

            collected: List[str] = []
            for value in payload.values():
                text = self._stringify_payload_text(value)
                if text:
                    collected.append(text)
            return self._join_texts(collected)

        if isinstance(payload, (list, tuple)):
            collected: List[str] = []
            for item in payload:
                text = self._stringify_payload_text(item)
                if text:
                    collected.append(text)
            return self._join_texts(collected)

        if hasattr(payload, "txts"):
            return self._stringify_payload_text(getattr(payload, "txts"))
        if hasattr(payload, "text"):
            return self._stringify_payload_text(getattr(payload, "text"))
        if hasattr(payload, "result"):
            return self._stringify_payload_text(getattr(payload, "result"))

        return self._clean_text(payload)

    def _run_cloud_ocr(self, img_np: np.ndarray) -> str:
        url = self._cloud_api_url()
        if not url:
            raise RuntimeError("未配置云端 OCR API 地址")

        headers = {
            "Accept": "application/json, text/plain;q=0.9, */*;q=0.8",
        }
        api_key = self._cloud_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        files = {
            self._cloud_image_field(): ("screenshot.png", self._image_to_png_bytes(img_np), "image/png")
        }

        logger.info(f"调用云端 OCR API: {url}")
        response = requests.post(url, headers=headers, files=files, timeout=self._cloud_timeout())
        response.raise_for_status()

        payload: Any
        try:
            payload = response.json()
        except ValueError:
            payload = response.text

        return self._extract_cloud_text(payload)

    def _run_local_ocr(self, img_np: np.ndarray) -> Tuple[str, List[float]]:
        engine = self._ensure_rapid_engine()
        result = engine(self._preprocess_image(img_np))
        texts, scores = self._extract_rapid_output(result)
        return self._join_texts(texts), scores

    def preload(self):
        try:
            self.set_mode(getattr(config_manager.config, "ocr_engine", "rapid"))
            if self._engine == "cloud":
                if self._has_cloud_config():
                    self._set_status("就绪(云端 OCR API)")
                return

            self._ensure_rapid_engine()
            self._set_status(self._build_status(initialized=True))
        except Exception as e:
            logger.warning(f"OCR 引擎预加载失败: {e}")
            if self._engine == "cloud" and self._has_cloud_config():
                self._set_status("就绪(云端 OCR API)")
            else:
                self._set_status(self._build_status(initialized=False))

    def invalidate_cache(self):
        with self._rapid_lock:
            self._rapid_engine = None
        self._update_status_after_mode_change()

    def recognize_text(self, img_data) -> str:
        """
        对传入的图像数据进行文字识别。
        img_data 可以是 QImage、PIL Image 或 numpy 数组。
        """
        self.set_mode(getattr(config_manager.config, "ocr_engine", "rapid"))

        try:
            img_np = self._to_numpy_rgb(img_data)
            self._set_status(f"识别中({self._engine_label()})")

            if self._engine == "cloud":
                text = self._run_cloud_ocr(img_np)
                self._set_status(self._build_status(initialized=True if self._has_cloud_config() else False))
                logger.info(f"云端 OCR 识别完成，获取文字长度: {len(text)}")
                return text

            local_text = ""
            scores: List[float] = []
            try:
                local_text, scores = self._run_local_ocr(img_np)
            except Exception as local_error:
                if self._engine != "hybrid" or not self._has_cloud_config():
                    raise
                logger.warning(f"本地 OCR 失败，尝试云端 OCR 兜底: {local_error}")
                cloud_text = self._run_cloud_ocr(img_np)
                self._set_status(self._build_status(initialized=True))
                logger.info(f"云端 OCR 兜底完成，获取文字长度: {len(cloud_text)}")
                return cloud_text

            if self._engine == "hybrid" and self._should_enhance_with_cloud(local_text, scores):
                try:
                    cloud_text = self._run_cloud_ocr(img_np)
                    if cloud_text.strip():
                        local_text = self._merge_texts(local_text, cloud_text)
                except Exception as cloud_error:
                    logger.warning(f"云端 OCR 增强失败，保留本地结果: {cloud_error}")

            self._set_status(self._build_status(initialized=True))
            logger.info(f"RapidOCR 识别完成，获取文字长度: {len(local_text)}")
            return local_text
        except Exception as e:
            self._set_status(f"识别失败({self._engine_label()})")
            logger.error(f"OCR 识别过程中出错: {e}")
            return f"Error: OCR 识别失败 ({str(e)})"


ocr_service = OCRService()
