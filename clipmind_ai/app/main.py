import asyncio
import html
import os
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

try:
    import win32gui
except ImportError:  # pragma: no cover - Windows-only dependency
    win32gui = None

from PySide6.QtCore import QObject, QElapsedTimer, QTimer, Signal, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

try:
    from qasync import QEventLoop
except ImportError:  # pragma: no cover - optional during local dev
    QEventLoop = None


current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))
parent_dir = current_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

from app.core.clipboard_service import clipboard_service
from app.core.content_extractor import content_extractor
from app.core.hotkey_manager import HotkeyThread
from app.core.llm_client import llm_client
from app.core.ocr_service import ocr_service
from app.core.prompt_engine import prompt_engine
from app.core.rag_service import rag_service
from app.core.search_service import search_service
from app.core.speech_service import speech_service
from app.storage.config import config_manager
from app.storage.db import db_manager
from app.ui.main_window import MainWindow
from app.ui.overlay_window import screenshot_service
from app.ui.settings_window import SettingsWindow
from app.utils.logger import logger


class AppController(QObject):
    ocr_result_signal = Signal(str)
    ocr_status_signal = Signal(str)
    ai_chunk_signal = Signal(str)
    ai_status_signal = Signal(str)
    ai_finished_signal = Signal(bool, str)
    ai_request_started_signal = Signal(str)
    speech_finished_signal = Signal(bool, str)
    speech_partial_signal = Signal(str)
    rag_animation_signal = Signal(bool)
    speech_status_signal = Signal(str)
    rag_status_signal = Signal(str)
    search_status_signal = Signal(str)

    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self._loop = loop

        self.main_window = MainWindow()
        self.settings_window = SettingsWindow()
        self.hotkey_thread = HotkeyThread()

        self.session_id = str(uuid.uuid4())
        self._last_foreground_hwnd: Optional[int] = None
        self._is_shutting_down = False
        self._pending_tasks: set[asyncio.Task] = set()
        self._ai_task: Optional[asyncio.Task] = None

        self._ai_wait_timer = QTimer(self)
        self._ai_wait_timer.setInterval(1000)
        self._ai_wait_timer.timeout.connect(self._update_ai_wait_status)
        self._ai_wait_elapsed = QElapsedTimer()
        self._ai_response_started = False

        self._rag_anim_timer = QTimer(self)
        self._rag_anim_timer.setInterval(260)
        self._rag_anim_timer.timeout.connect(self._tick_rag_animation)
        self._rag_anim_frames = [
            "正在翻阅笔记",
            "正在翻阅笔记.",
            "正在翻阅笔记..",
            "正在翻阅笔记...",
        ]
        self._rag_anim_index = 0
        self._rag_is_running = False

        self._speech_wait_timer = QTimer(self)
        self._speech_wait_timer.setInterval(1000)
        self._speech_wait_timer.timeout.connect(self._update_speech_wait_status)
        self._speech_wait_elapsed = QElapsedTimer()
        self._speech_recording = False
        self._speech_processing = False
        self._speech_input_prefix = ""

        self._current_user_input = ""
        self._current_template_name = ""
        self._current_rag_sources: list[dict[str, str]] = []
        self._source_link_map: dict[str, str] = {}

        self._setup_connections()
        self._load_templates()
        self._load_models()
        self.main_window.set_ocr_status(ocr_service.get_status())
        self.main_window.set_response_status("待发送")

    def _setup_connections(self):
        self.hotkey_thread.trigger_main_signal.connect(self.toggle_main_window)
        self.hotkey_thread.trigger_selection_signal.connect(self.handle_selection_read)
        self.hotkey_thread.trigger_screenshot_signal.connect(self.handle_screenshot)
        self.hotkey_thread.trigger_speech_signal.connect(self.toggle_speech_recording)
        self.hotkey_thread.trigger_paste_signal.connect(self.paste_result)

        self.main_window.btn_settings.clicked.connect(self.show_settings_window)
        self.main_window.btn_send.clicked.connect(self.handle_send_request)
        self.main_window.btn_copy.clicked.connect(self.copy_result)
        self.main_window.request_exit_signal.connect(self.shutdown)
        self.main_window.model_changed_signal.connect(self.on_model_changed)
        self.main_window.source_link_signal.connect(self._on_source_link_clicked)

        self.settings_window.config_updated.connect(self.on_config_updated)

        self.ocr_result_signal.connect(self.on_ocr_result_ready)
        self.ocr_status_signal.connect(self.main_window.set_ocr_status)

        self.ai_request_started_signal.connect(self._on_ai_request_started)
        self.ai_chunk_signal.connect(self._on_ai_chunk_received)
        self.ai_chunk_signal.connect(self.main_window.append_output)
        self.ai_status_signal.connect(self.main_window.set_response_status)
        self.ai_finished_signal.connect(self._on_ai_finished)

        self.speech_partial_signal.connect(self._on_speech_partial)
        self.speech_finished_signal.connect(self._on_speech_finished)

        self.rag_animation_signal.connect(self._set_rag_animation)
        self.speech_status_signal.connect(self.main_window.set_speech_status)
        self.rag_status_signal.connect(self.main_window.set_rag_status)
        self.search_status_signal.connect(self.main_window.set_search_status)

    def _create_task(self, coro, task_name: str) -> Optional[asyncio.Task]:
        if self._is_shutting_down:
            return None

        task = self._loop.create_task(coro)
        self._pending_tasks.add(task)

        def _done_callback(done_task: asyncio.Task):
            self._pending_tasks.discard(done_task)
            if done_task.cancelled():
                return
            with suppress(asyncio.CancelledError):
                exc = done_task.exception()
                if exc:
                    logger.error(f"{task_name} 任务失败: {exc}")

        task.add_done_callback(_done_callback)
        return task

    def _load_templates(self):
        names = prompt_engine.get_template_names()
        self.main_window.combo_template.clear()
        self.main_window.combo_template.addItems(names)

    def _load_models(self):
        profiles = config_manager.get_model_profiles()
        active_profile = config_manager.get_active_model_profile().model_copy(deep=True)
        self.main_window.set_model_profiles(profiles, active_profile.id if active_profile else "")

    def _capture_foreground_window(self):
        if win32gui is None:
            self._last_foreground_hwnd = None
            return None

        try:
            hwnd = win32gui.GetForegroundWindow()
            our_windows = {int(self.main_window.winId()), int(self.settings_window.winId())}
            if hwnd in our_windows:
                return self._last_foreground_hwnd
            self._last_foreground_hwnd = hwnd if hwnd else None
            return self._last_foreground_hwnd
        except Exception as e:
            logger.warning(f"获取前台窗口失败: {e}")
            self._last_foreground_hwnd = None
            return None

    def _is_valid_external_window(self, hwnd: Optional[int]) -> bool:
        if win32gui is None or not hwnd:
            return False
        try:
            if not win32gui.IsWindow(hwnd):
                return False
            if hwnd in {int(self.main_window.winId()), int(self.settings_window.winId())}:
                return False
            return True
        except Exception:
            return False

    def _show_main_window(self):
        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()

    @Slot()
    def show_settings_window(self):
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    @Slot()
    def toggle_main_window(self):
        if self.main_window.isVisible():
            self.main_window.hide()
            return
        self._capture_foreground_window()
        self._show_main_window()

    @Slot(str)
    def on_model_changed(self, model_id: str):
        if not model_id:
            return
        if config_manager.set_active_model(model_id):
            profile = config_manager.get_active_model_profile()
            logger.info(f"当前模型已切换: {profile.display_name} ({profile.model_name})")
        self._load_models()

    @Slot()
    def handle_selection_read(self):
        self._capture_foreground_window()
        self._create_task(self._handle_selection_read_async(), "读取选中文本")

    async def _handle_selection_read_async(self):
        text = await asyncio.to_thread(clipboard_service.read_selected_text)
        if text:
            self.main_window.set_input(text)

    @Slot()
    def handle_screenshot(self):
        self._capture_foreground_window()
        self.main_window.hide()
        QTimer.singleShot(180, lambda: screenshot_service.start_selection(
            self._on_screenshot_captured,
            cancel_callback=self._on_screenshot_cancelled
        ))

    @Slot()
    def toggle_speech_recording(self):
        self._create_task(self._toggle_speech_recording_async(), "语音录音切换")

    async def _toggle_speech_recording_async(self):
        if self._is_shutting_down:
            return

        if self._speech_processing:
            self.main_window.set_response_status("语音识别进行中，请稍候")
            return

        self._show_main_window()
        if not self._speech_recording:
            if not speech_service.has_model():
                self.main_window.set_response_status("请先在设置中配置语音模型目录")
                return

            self._speech_input_prefix = self.main_window.input_text.toPlainText()
            started, message = await asyncio.to_thread(
                speech_service.start_recording,
                self.speech_partial_signal.emit,
            )
            if not started:
                self.main_window.set_response_status(message)
                return

            self._speech_recording = True
            self._speech_wait_elapsed.start()
            self._speech_wait_timer.start()
            self.main_window.set_response_status("正在录音（系统音频 + 麦克风），再按一次可结束并识别")
            return

        self._speech_recording = False
        self._speech_processing = True
        self.main_window.set_response_status("录音结束，正在离线识别...")
        await self._run_speech_task_async()

    @Slot(object)
    def _on_screenshot_captured(self, screenshot):
        self.main_window.show()
        self.main_window.set_input("正在执行 OCR 识别，请稍候...")
        self.ocr_status_signal.emit("识别中")
        self._create_task(self._run_ocr_task_async(screenshot), "OCR 识别")

    @Slot()
    def _on_screenshot_cancelled(self):
        self.main_window.show()

    async def _run_ocr_task_async(self, screenshot):
        OCR_TIMEOUT = 10  # OCR识别超时时间（秒）
        try:
            self.ocr_status_signal.emit("识别中")
            text = await asyncio.wait_for(
                ocr_service.arecognize_text(screenshot),
                timeout=OCR_TIMEOUT
            )
            self.ocr_status_signal.emit(self._status_to_text(ocr_service.get_status()))
            if text.startswith("Error:"):
                logger.warning(text)
                self.ocr_status_signal.emit("有异常")
                self.main_window.set_response_status(text)
                return
            if text:
                self.ocr_result_signal.emit(text)
            else:
                self.ocr_status_signal.emit("已就绪")
        except asyncio.TimeoutError:
            logger.warning("OCR 识别超时")
            self.ocr_status_signal.emit("有异常")
            self.main_window.set_response_status("OCR 识别超时，请重试")
        except Exception as e:
            logger.error(f"OCR 任务失败: {e}")
            self.ocr_status_signal.emit("有异常")

    def _preload_ocr_task(self):
        ocr_service.preload()
        self.ocr_status_signal.emit(self._status_to_text(ocr_service.get_status()))

    async def _run_speech_task_async(self):
        try:
            success, payload = await asyncio.to_thread(speech_service.stop_and_transcribe)
            self.speech_finished_signal.emit(success, payload)
        except Exception as e:
            logger.error(f"语音识别任务失败: {e}")
            self.speech_finished_signal.emit(False, str(e))

    @Slot(str)
    def on_ocr_result_ready(self, text):
        self.main_window.set_input(text)
        self._show_main_window()

    @Slot()
    def handle_send_request(self):
        if self._is_shutting_down:
            return

        user_input = self.main_window.input_text.toPlainText().strip()
        template_name = self.main_window.combo_template.currentText()
        active_profile = config_manager.get_active_model_profile()

        if not user_input:
            self.main_window.set_response_status("请先输入内容")
            return

        if not active_profile.api_key:
            self.main_window.set_response_status(f"请先为模型“{active_profile.display_name}”配置 API Key")
            return

        if self._ai_task and not self._ai_task.done():
            self.main_window.set_response_status("上一个请求还在处理中，请稍候")
            return

        self._current_user_input = user_input
        self._current_template_name = template_name
        self._current_rag_sources = []
        self._source_link_map = {}
        self.main_window.output_text.clear()
        self.main_window.btn_send.setEnabled(False)
        self._ai_response_started = False
        self._ai_wait_timer.stop()

        rag_active = rag_service.is_config_ready()
        self.rag_animation_signal.emit(rag_active)
        if not rag_active:
            self.ai_status_signal.emit(f"正在请求模型 {active_profile.display_name} ...")

        task = self._create_task(
            self._run_ai_task_async(template_name, user_input, active_profile),
            "AI 请求",
        )
        self._ai_task = task

    async def _build_rag_context_async(self, user_input: str) -> tuple[str, list[dict[str, str]]]:
        if not rag_service.is_config_ready():
            return "", []
        try:
            rag_hits = await rag_service.asearch(user_input, top_k=3)
            rag_context = rag_service.build_context(rag_hits)
            sources = rag_service.collect_sources(rag_hits)
            return rag_context, sources
        except Exception as e:
            logger.warning(f"RAG 检索失败，已降级普通对话: {e}")
            return "", []

    async def _build_web_context_async(self, template_name: str, user_input: str) -> str:
        if not prompt_engine.is_search_enabled(template_name):
            return ""
        try:
            search_results = await search_service.asearch(user_input)
            urls = [res.get("url", "") for res in search_results if res.get("url")]
            if not urls:
                return ""
            return await content_extractor.aget_summarized_context(urls)
        except Exception as e:
            logger.warning(f"联网检索失败，已跳过: {e}")
            return ""

    async def _run_ai_task_async(self, template_name: str, user_input: str, active_profile):
        rag_task: asyncio.Task | None = None
        web_task: asyncio.Task | None = None
        try:
            rag_task = self._loop.create_task(self._build_rag_context_async(user_input))
            web_task = self._loop.create_task(self._build_web_context_async(template_name, user_input))

            (rag_context, rag_sources), web_context = await asyncio.gather(rag_task, web_task)
            self._current_rag_sources = rag_sources

            context_parts = []
            if rag_context:
                context_parts.append("本地笔记检索结果：\n" + rag_context)
            if web_context:
                context_parts.append("联网检索结果：\n" + web_context)
            merged_context = "\n\n".join(context_parts)

            messages = prompt_engine.format_prompt(template_name, user_input, merged_context)
            self.ai_request_started_signal.emit(f"正在请求模型 {active_profile.display_name} ...")

            full_response = ""
            async for chunk in llm_client.achat_stream(messages, active_profile):
                if not full_response and chunk.lstrip().startswith("Error:"):
                    self.ai_finished_signal.emit(False, chunk)
                    return
                full_response += chunk
                self.ai_chunk_signal.emit(chunk)

            self.ai_finished_signal.emit(True, full_response)
        except asyncio.CancelledError:
            # 取消时只 raise，让 httpx 的上下文管理器处理清理
            # 不要在此处调用 task.cancel() 或 await
            raise
        except Exception as e:
            logger.error(f"AI 请求任务失败: {e}")
            self.ai_finished_signal.emit(False, str(e))

    def _on_ai_request_started(self, status_text: str):
        self.rag_animation_signal.emit(False)
        self._ai_response_started = False
        self._ai_wait_elapsed.start()
        self._ai_wait_timer.start()
        self.main_window.set_response_status(status_text)

    def _on_ai_chunk_received(self, _chunk: str):
        if not self._ai_response_started:
            self._ai_response_started = True
            self._ai_wait_timer.stop()
            self.main_window.set_response_status("模型已开始输出")

    def _update_ai_wait_status(self):
        if self._ai_response_started or not self._ai_wait_elapsed.isValid():
            return
        seconds = max(1, int((self._ai_wait_elapsed.elapsed() + 999) / 1000))
        self.main_window.set_response_status(f"等待模型响应，已等待 {seconds} 秒")

    def _set_rag_animation(self, enabled: bool):
        if enabled and not self._rag_is_running:
            self._rag_is_running = True
            self._rag_anim_index = 0
            self.main_window.set_response_status(self._rag_anim_frames[0])
            self._rag_anim_timer.start()
            return
        if not enabled and self._rag_is_running:
            self._rag_is_running = False
            self._rag_anim_timer.stop()

    def _tick_rag_animation(self):
        if not self._rag_is_running:
            return
        self._rag_anim_index = (self._rag_anim_index + 1) % len(self._rag_anim_frames)
        self.main_window.set_response_status(self._rag_anim_frames[self._rag_anim_index])

    def _update_speech_wait_status(self):
        if not self._speech_wait_elapsed.isValid():
            return
        seconds = max(1, int((self._speech_wait_elapsed.elapsed() + 999) / 1000))
        if self._speech_recording:
            self.main_window.set_response_status(f"正在录音（系统音频 + 麦克风），已录音 {seconds} 秒")
        elif self._speech_processing:
            self.main_window.set_response_status(f"录音结束，正在离线识别，已用时 {seconds} 秒")

    @Slot(bool, str)
    def _on_speech_finished(self, success: bool, payload: str):
        self._speech_wait_timer.stop()
        self._speech_recording = False
        self._speech_processing = False

        if success and payload.strip():
            transcript = payload.strip()
            self._apply_speech_text(transcript)
            self.main_window.set_response_status("语音转文字完成")
            return
        if success:
            self.main_window.set_response_status("没有识别到有效语音内容")
            return
        self.main_window.set_response_status(payload or "语音识别失败")

    def _speech_combine_text(self, transcript: str) -> str:
        prefix = self._speech_input_prefix.strip()
        transcript = transcript.strip()
        if not prefix:
            return transcript
        if not transcript:
            return prefix
        separator = "" if prefix.endswith("\n") else "\n"
        return f"{prefix}{separator}{transcript}"

    def _apply_speech_text(self, transcript: str):
        combined = self._speech_combine_text(transcript)
        self.main_window.input_text.setPlainText(combined)
        cursor = self.main_window.input_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.main_window.input_text.setTextCursor(cursor)
        self.main_window.input_text.ensureCursorVisible()

    @Slot(str)
    def _on_speech_partial(self, text: str):
        if self._is_shutting_down or not text.strip():
            return
        self._apply_speech_text(text)
        if self._speech_recording or self._speech_processing:
            self.main_window.set_response_status("语音识别中，结果已实时写入输入框")

    def _append_source_tags(self):
        if not self._current_rag_sources:
            return

        links = []
        for item in self._current_rag_sources:
            doc_path = str(item.get("doc_path", "") or "")
            if not doc_path:
                continue
            doc_name = str(item.get("doc_name", "") or Path(doc_path).name)
            link_id = uuid.uuid4().hex
            self._source_link_map[link_id] = doc_path
            links.append(f'<a href="source://{link_id}">[{html.escape(doc_name)}]</a>')

        if not links:
            return

        source_html = "<br><br><span style='color:#666;'>参考来源：</span> " + " ".join(links)
        self.main_window.append_output_html(source_html)

    @Slot(str)
    def _on_source_link_clicked(self, link_text: str):
        try:
            parsed = urlparse(link_text)
            if parsed.scheme != "source":
                return
            link_id = parsed.netloc or parsed.path.strip("/")
            doc_path = self._source_link_map.get(link_id)
            if not doc_path:
                return
            if not Path(doc_path).exists():
                self.main_window.set_response_status(f"来源文件不存在: {doc_path}")
                return
            os.startfile(doc_path)  # type: ignore[attr-defined]
        except Exception as e:
            logger.error(f"打开来源文件失败: {e}")
            self.main_window.set_response_status("打开来源文件失败")

    def _on_ai_finished(self, success: bool, payload: str):
        self.rag_animation_signal.emit(False)
        self._ai_wait_timer.stop()
        self.main_window.btn_send.setEnabled(True)
        self._ai_task = None

        if success:
            if payload.strip():
                self._create_task(
                    self._save_history_async(self._current_user_input, payload),
                    "写入会话历史",
                )
                self._append_source_tags()
                self.main_window.set_response_status("生成完成")
            else:
                self.main_window.set_response_status("模型未返回内容")
            return

        if payload:
            existing_text = self.main_window.get_output_text().strip()
            if existing_text:
                self.main_window.append_output(f"\n{payload}")
            else:
                self.main_window.append_output(payload)
        self.main_window.set_response_status("请求失败")

    async def _save_history_async(self, user_text: str, assistant_text: str):
        await asyncio.gather(
            asyncio.to_thread(db_manager.add_history, "user", user_text, self.session_id),
            asyncio.to_thread(db_manager.add_history, "assistant", assistant_text, self.session_id),
        )

    @Slot()
    def copy_result(self):
        text = self.main_window.get_output_text()
        if not text.strip():
            self.main_window.set_response_status("没有可复制的结果")
            return
        if clipboard_service.copy_to_clipboard(text):
            self.main_window.set_response_status("结果已复制到剪贴板")
        else:
            self.main_window.set_response_status("复制失败")

    @Slot()
    def paste_result(self):
        self._create_task(self._paste_result_async(), "回填结果")

    async def _resolve_paste_target_hwnd(self) -> Optional[int]:
        if self._is_valid_external_window(self._last_foreground_hwnd):
            return self._last_foreground_hwnd

        if win32gui is None:
            return None

        target = self._capture_foreground_window()
        if self._is_valid_external_window(target):
            return target

        try:
            hwnd = win32gui.GetForegroundWindow()
        except Exception:
            hwnd = None

        if self._is_valid_external_window(hwnd):
            self._last_foreground_hwnd = hwnd
            return hwnd

        return None

    async def _paste_result_async(self):
        text = self.main_window.get_output_text()
        if not text.strip():
            self.main_window.set_response_status("没有可回填的结果")
            return

        target_hwnd = await self._resolve_paste_target_hwnd()
        if not target_hwnd:
            copied = await asyncio.to_thread(clipboard_service.copy_to_clipboard, text)
            if copied:
                self.main_window.set_response_status("已复制到剪贴板，但未检测到可回填窗口")
            else:
                self.main_window.set_response_status("回填失败")
            return

        pasted = await asyncio.to_thread(
            clipboard_service.auto_paste,
            text,
            target_hwnd,
            80,
            150,
        )
        if pasted:
            self.main_window.set_response_status("已回填到目标窗口")
            return

        copied = await asyncio.to_thread(clipboard_service.copy_to_clipboard, text)
        if copied:
            self.main_window.set_response_status("回填失败，已复制到剪贴板")
        else:
            self.main_window.set_response_status("回填失败")

    @Slot()
    def shutdown(self):
        if self._is_shutting_down:
            return

        self._is_shutting_down = True
        self._ai_wait_timer.stop()
        self._speech_wait_timer.stop()
        self._rag_anim_timer.stop()

        if self._ai_task and not self._ai_task.done():
            self._ai_task.cancel()
        for task in list(self._pending_tasks):
            task.cancel()

        try:
            speech_service.cancel_recording()
        except Exception as e:
            logger.warning(f"停止语音录音失败: {e}")

        try:
            rag_service.stop()
        except Exception as e:
            logger.warning(f"停止 RAG 索引线程失败: {e}")

        try:
            if self.hotkey_thread.isRunning():
                self.hotkey_thread.stop()
        except Exception as e:
            logger.warning(f"停止热键线程失败: {e}")

        try:
            if self.settings_window.isVisible():
                self.settings_window.close()
        except Exception:
            pass

        app = QApplication.instance()
        if app is not None:
            app.quit()

    @Slot(str)
    def on_config_updated(self, scope: str = "general"):
        if scope in ("general", "hotkey"):
            self.hotkey_thread.register_hotkeys()

        if scope in ("general", "ocr"):
            ocr_service.set_mode(getattr(config_manager.config, "ocr_engine", "rapid"))
            ocr_service.invalidate_cache()
            self.ocr_status_signal.emit(self._status_to_text(ocr_service.get_status()))

        if scope in ("general", "speech"):
            speech_service.invalidate_cache()

        if scope in ("general", "rag"):
            rag_service.reload_config()

        if scope in ("general", "template"):
            prompt_engine.refresh_templates()
            self._load_templates()

        if scope in ("general", "model"):
            self._load_models()
            self._create_task(self._preload_background_services_async(), "配置热更新预加载")

        if scope == "general":
            logger.info("配置已动态更新")

    def start(self):
        self.hotkey_thread.start()
        self._show_main_window()
        self.ocr_status_signal.emit(self._status_to_text(ocr_service.get_status()))
        self._emit_initial_status()
        self.main_window.set_response_status("待发送")
        self._create_task(self._preload_background_services_async(), "启动预加载")

    def _status_to_text(self, status: str) -> str:
        """将状态文本映射为3字状态：已就绪、未启用、未配置、有异常"""
        if not status:
            return "未配置"
        if "失败" in status or "错误" in status or "error" in status.lower():
            return "有异常"
        if status in ("就绪", "已就绪", "已配置", "√"):
            return "已就绪"
        if "未初始化" in status:
            return "初始化中"
        if "未启用" in status or "未配置" in status or "×" in status:
            return "未启用"
        # 正在进行中的状态视为就绪
        if "就绪" in status or "ready" in status.lower() or "识别中" in status:
            return "已就绪"
        return "有异常"

    def _emit_initial_status(self):
        # 语音识别状态
        if config_manager.config.speech_model_dir:
            logger.info("语音识别：已配置，初始化中...")
            self.speech_status_signal.emit("初始化中")
        else:
            logger.info("语音识别：未配置")
            self.speech_status_signal.emit("未配置")

        # RAG 状态
        if config_manager.config.enable_rag and config_manager.config.rag_notes_dir:
            logger.info("RAG：已启用，索引构建中...")
            self.rag_status_signal.emit("初始化中")
        elif config_manager.config.enable_rag:
            logger.info("RAG：已启用但未配置知识库目录")
            self.rag_status_signal.emit("未就绪")
        else:
            logger.info("RAG：未启用")
            self.rag_status_signal.emit("未启用")

        # 联网搜索状态
        if config_manager.config.enable_search and config_manager.config.search_api_key:
            logger.info("联网搜索：已配置")
            self.search_status_signal.emit("已就绪")
        elif config_manager.config.enable_search:
            logger.info("联网搜索：已启用但未配置 API Key")
            self.search_status_signal.emit("未就绪")
        else:
            logger.info("联网搜索：未启用")
            self.search_status_signal.emit("未启用")

    async def _preload_background_services_async(self):
        if speech_service.has_model():
            try:
                logger.info("启动阶段开始预加载语音识别模型")
                await asyncio.to_thread(speech_service.preload)
                logger.info("语音识别模型预加载流程完成")
                self.speech_status_signal.emit("已就绪")
            except Exception as e:
                logger.warning(f"语音模型预加载失败（录音时将自动重试）: {e}")
                self.speech_status_signal.emit("有异常")
        else:
            self.speech_status_signal.emit("未配置")

        try:
            await asyncio.to_thread(self._preload_ocr_task)
        except Exception as e:
            logger.warning(f"OCR 预热失败: {e}")

        try:
            await asyncio.to_thread(rag_service.reload_config)
            if rag_service.is_config_ready():
                logger.info("RAG 索引构建完成")
                self.rag_status_signal.emit("已就绪")
            else:
                self.rag_status_signal.emit("未就绪")
        except Exception as e:
            logger.warning(f"RAG 后台索引启动失败: {e}")
            self.rag_status_signal.emit("有异常")


def main():
    if QEventLoop is None:
        raise RuntimeError("未安装 qasync，请先执行: pip install qasync")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    controller = AppController(loop)
    controller.start()

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
