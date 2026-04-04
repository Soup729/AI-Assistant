from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
import threading
import numpy as np
from PIL import Image, ImageOps
from PySide6.QtGui import QImage
from PySide6.QtCore import QBuffer, QIODevice
from app.utils.logger import logger
from app.storage.config import config

class OCRService:
    def __init__(self):
        self.ocr = None
        self._initialized = False
        self._init_lock = threading.Lock()
        self._status = "未初始化"
        self._mode = getattr(config, "ocr_mode", "fast")

    def get_status(self) -> str:
        return self._status

    def get_mode(self) -> str:
        return self._mode

    def _get_runtime_root(self) -> Path:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS)
        return Path(__file__).resolve().parents[2]

    def _get_project_model_dir(self, model_name: str):
        model_dir = self._get_runtime_root() / "assets" / "models" / model_name
        return str(model_dir) if model_dir.is_dir() else None

    def _get_user_model_dir(self, model_name: str):
        model_dir = (
            Path.home()
            / ".paddlex"
            / "official_models"
            / model_name
        )
        return str(model_dir) if model_dir.is_dir() else None

    def _resolve_model_dir(self, model_name: str):
        return self._get_project_model_dir(model_name) or self._get_user_model_dir(model_name)

    def set_mode(self, mode: str):
        mode = (mode or "fast").strip().lower()
        if mode not in {"fast", "accurate"}:
            mode = "fast"
        if self._mode == mode:
            return
        self._mode = mode
        self.ocr = None
        self._initialized = False
        self._status = f"未初始化({self._mode})"
        logger.info(f"OCR 模式已切换为: {self._mode}")

    def _init_paddle(self):
        """延迟初始化 PaddleOCR，避免启动时耗时过长"""
        if self._initialized:
            return True

        with self._init_lock:
            if self._initialized:
                return True

            try:
                self._status = f"初始化中({self._mode})"
                # 关闭每次初始化前的联网探测，避免无意义等待。
                os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
                os.environ["FLAGS_use_mkldnn"] = "0"

                from paddleocr import PaddleOCR
                import logging

                logging.getLogger("ppocr").setLevel(logging.ERROR)

                if self._mode == "accurate":
                    det_model_name = "PP-OCRv5_server_det"
                    rec_model_name = "PP-OCRv5_server_rec"
                    det_limit_side_len = 1280
                    cpu_threads = 6
                else:
                    det_model_name = "PP-OCRv5_mobile_det"
                    rec_model_name = "PP-OCRv5_mobile_rec"
                    det_limit_side_len = 960
                    cpu_threads = 4

                det_dir = self._resolve_model_dir(det_model_name)
                rec_dir = self._resolve_model_dir(rec_model_name)
                logger.info(
                    f"OCR runtime root: {self._get_runtime_root()} | det_dir: {det_dir} | rec_dir: {rec_dir}"
                )

                # 高精度模式如果项目内/本地都没有 server 模型，则自动回退到已打包的 mobile 模型。
                if self._mode == "accurate" and (not det_dir or not rec_dir):
                    logger.warning("未找到高精度 OCR 模型，已回退到 mobile 模型")
                    det_model_name = "PP-OCRv5_mobile_det"
                    rec_model_name = "PP-OCRv5_mobile_rec"
                    det_limit_side_len = 960
                    cpu_threads = 4
                    det_dir = self._resolve_model_dir(det_model_name)
                    rec_dir = self._resolve_model_dir(rec_model_name)

                # PaddleOCR 3.x 默认会加载 server 模型和额外文档预处理，桌面截图场景既慢又不稳定。
                # 这里改为官方兼容的移动端 OCR 模型，并关闭文档纠偏/去畸变/行方向模块。
                self.ocr = PaddleOCR(
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    text_detection_model_name=det_model_name,
                    text_detection_model_dir=det_dir,
                    text_recognition_model_name=rec_model_name,
                    text_recognition_model_dir=rec_dir,
                    enable_mkldnn=False,
                    cpu_threads=cpu_threads,
                    text_det_limit_side_len=det_limit_side_len,
                )
                self._initialized = True
                self._status = f"就绪({self._mode})"
                logger.info("PaddleOCR 初始化成功")
                return True
            except Exception as e:
                self._status = f"初始化失败({self._mode})"
                logger.error(f"PaddleOCR 初始化失败: {e}")
                return False

    def preload(self):
        """提供给外部在后台预加载的方法"""
        self._init_paddle()

    def _to_numpy_rgb(self, img_data):
        """将 QImage/PIL/numpy 稳定转换为 RGB numpy 数组。"""
        if isinstance(img_data, QImage):
            # 直接读取 QImage.bits() 很容易受到 stride/通道格式影响，导致颜色错乱或识别错误。
            # 统一转 PNG 后再由 PIL 解码最稳定。
            qbuffer = QBuffer()
            qbuffer.open(QIODevice.WriteOnly)
            img_data.save(qbuffer, "PNG")
            pil_image = Image.open(BytesIO(qbuffer.data().data())).convert("RGB")
            return np.array(pil_image)

        if isinstance(img_data, Image.Image):
            pil_image = img_data.convert("RGB")
            return np.array(pil_image)

        if isinstance(img_data, np.ndarray):
            if img_data.ndim == 2:
                return np.stack([img_data] * 3, axis=-1)
            if img_data.shape[2] == 4:
                return img_data[:, :, :3]
            return img_data

        raise ValueError("不支持的图像格式")

    def _preprocess_image(self, img_np):
        """对截图做轻量预处理，提升中英文混合识别稳定性。"""
        pil_image = Image.fromarray(img_np).convert("RGB")

        # 自动拉伸对比度，桌面截图对白底黑字和灰底字更友好。
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

    def _predict_texts(self, img_np):
        """按 PaddleOCR 3.x 的稳定路径进行推理并提取文本。"""
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                temp_path = f.name
            Image.fromarray(img_np).save(temp_path)

            result = self.ocr.predict(temp_path)
            texts = []
            if isinstance(result, list):
                for item in result:
                    if isinstance(item, dict):
                        rec_texts = item.get("rec_texts") or []
                        for text in rec_texts:
                            text = str(text).strip()
                            if text:
                                texts.append(text)
            return texts
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def recognize_text(self, img_data) -> str:
        """
        对传入的图像数据进行文字识别。
        img_data: 可以是 QImage, PIL Image, 或 numpy 数组。
        """
        if not self._init_paddle():
            return "Error: OCR 初始化失败，请检查是否正确安装了 paddleocr 和 paddlepaddle。"

        try:
            self._status = f"识别中({self._mode})"
            img_np = self._to_numpy_rgb(img_data)
            img_np = self._preprocess_image(img_np)

            logger.info("开始执行 PaddleOCR 识别...")
            texts = self._predict_texts(img_np)

            final_text = "\n".join(texts)
            self._status = f"就绪({self._mode})"
            logger.info(f"OCR 识别完成，获取文字长度: {len(final_text)}")
            return final_text
            
        except Exception as e:
            self._status = f"识别失败({self._mode})"
            logger.error(f"OCR 识别过程中出错: {e}")
            return f"Error: OCR 识别失败 ({str(e)})"

ocr_service = OCRService()
