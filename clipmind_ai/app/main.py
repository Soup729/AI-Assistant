import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

try:
    import win32gui
except ImportError:  # pragma: no cover - Windows only dependency
    win32gui = None

from PySide6.QtCore import QObject, QElapsedTimer, QTimer, Signal, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

# 将项目根目录加入 sys.path，解决 ModuleNotFoundError
current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

# 同时加入 app 的父目录，以支持 "from app.xxx" 这种导入方式
parent_dir = current_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

from app.core.clipboard_service import clipboard_service
from app.core.content_extractor import content_extractor
from app.core.hotkey_manager import HotkeyThread
from app.core.llm_client import llm_client
from app.core.ocr_service import ocr_service
from app.core.prompt_engine import prompt_engine
from app.core.search_service import search_service
from app.core.speech_service import speech_service
from app.storage.config import config_manager
from app.storage.db import db_manager
from app.ui.main_window import MainWindow
from app.ui.overlay_window import screenshot_service
from app.ui.settings_window import SettingsWindow
from app.utils.logger import logger


class AppController(QObject):
    """
    应用的中央控制器，协调 UI 和各服务模块。
    """

    ocr_result_signal = Signal(str)
    ocr_status_signal = Signal(str)
    ai_chunk_signal = Signal(str)
    ai_status_signal = Signal(str)
    ai_finished_signal = Signal(bool, str)
    speech_finished_signal = Signal(bool, str)
    speech_partial_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.main_window = MainWindow()
        self.settings_window = SettingsWindow()
        self.hotkey_thread = HotkeyThread()

        self.session_id = str(uuid.uuid4())
        self._last_foreground_hwnd: Optional[int] = None
        self._ai_wait_timer = QTimer(self)
        self._ai_wait_timer.setInterval(1000)
        self._ai_wait_timer.timeout.connect(self._update_ai_wait_status)
        self._ai_wait_elapsed = QElapsedTimer()
        self._ai_response_started = False
        self._speech_wait_timer = QTimer(self)
        self._speech_wait_timer.setInterval(1000)
        self._speech_wait_timer.timeout.connect(self._update_speech_wait_status)
        self._speech_wait_elapsed = QElapsedTimer()
        self._speech_recording = False
        self._speech_processing = False
        self._is_shutting_down = False
        self._current_user_input = ""
        self._current_template_name = ""
        self._speech_stage = ""
        self._speech_input_prefix = ""

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

        self.main_window.btn_settings.clicked.connect(self.show_settings_window)
        self.main_window.btn_send.clicked.connect(self.handle_send_request)
        self.main_window.btn_copy.clicked.connect(self.copy_result)
        self.main_window.btn_paste.clicked.connect(self.paste_result)
        self.main_window.request_exit_signal.connect(self.shutdown)
        self.main_window.model_changed_signal.connect(self.on_model_changed)

        self.settings_window.config_updated.connect(self.on_config_updated)

        self.ocr_result_signal.connect(self.on_ocr_result_ready)
        self.ocr_status_signal.connect(self.main_window.set_ocr_status)
        self.ai_chunk_signal.connect(self._on_ai_chunk_received)
        self.ai_chunk_signal.connect(self.main_window.append_output)
        self.ai_status_signal.connect(self.main_window.set_response_status)
        self.ai_finished_signal.connect(self._on_ai_finished)
        self.speech_partial_signal.connect(self._on_speech_partial)
        self.speech_finished_signal.connect(self._on_speech_finished)

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
            logger.warning(f"获取当前前台窗口失败: {e}")
            self._last_foreground_hwnd = None
            return None

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
            logger.info(f"当前模型已切换为: {config_manager.get_active_model_profile().display_name}")
        self._load_models()

    @Slot()
    def handle_selection_read(self):
        logger.info("触发选中文本读取")
        self._capture_foreground_window()
        text = clipboard_service.read_selected_text()
        if text:
            self.main_window.set_input(text)

    @Slot()
    def handle_screenshot(self):
        logger.info("触发截图功能")
        self._capture_foreground_window()
        time.sleep(0.2)
        screenshot_service.start_selection(self.on_screenshot_captured)

    @Slot()
    def toggle_speech_recording(self):
        if self._is_shutting_down:
            return

        if self._speech_processing:
            self.main_window.set_response_status("语音识别正在进行中，请稍候")
            return

        self._show_main_window()

        if not self._speech_recording:
            if not speech_service.has_model():
                self.main_window.set_response_status("请先在设置中配置语音模型目录")
                return

            self._speech_input_prefix = self.main_window.input_text.toPlainText()
            started, message = speech_service.start_recording(self.speech_partial_signal.emit)
            if not started:
                self.main_window.set_response_status(message)
                return

            self._speech_recording = True
            self._speech_stage = "recording"
            self._speech_wait_elapsed.start()
            self._speech_wait_timer.start()
            self.main_window.set_response_status("正在录音（系统音频 + 麦克风），再按一次结束并识别")
            return

        self._speech_recording = False
        self._speech_processing = True
        self._speech_stage = "transcribing"
        self.main_window.set_response_status("录音已结束，正在离线识别...")
        threading.Thread(target=self._run_speech_task, daemon=True).start()

    @Slot(object)
    def on_screenshot_captured(self, screenshot):
        logger.info("获取到截图，开始 OCR...")
        self.main_window.set_input("正在执行 OCR 识别，请稍候...")
        self.ocr_status_signal.emit(ocr_service.get_status())
        threading.Thread(target=self._run_ocr_task, args=(screenshot,), daemon=True).start()

    def _run_ocr_task(self, screenshot):
        try:
            self.ocr_status_signal.emit("识别中...")
            text = ocr_service.recognize_text(screenshot)
            self.ocr_status_signal.emit(ocr_service.get_status())
            if text.startswith("Error:"):
                logger.warning(text)
                self.ocr_status_signal.emit("识别失败")
                self.main_window.set_response_status(text)
                return
            if text:
                self.ocr_result_signal.emit(text)
        except Exception as e:
            logger.error(f"OCR 任务失败: {e}")
            self.ocr_status_signal.emit("识别失败")

    def _preload_ocr_task(self):
        self.ocr_status_signal.emit(ocr_service.get_status())
        ocr_service.preload()
        self.ocr_status_signal.emit(ocr_service.get_status())

    def _run_speech_task(self):
        try:
            success, payload = speech_service.stop_and_transcribe()
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
            self.main_window.set_response_status(f"请先为模型「{active_profile.display_name}」配置 API Key")
            return

        self._current_user_input = user_input
        self._current_template_name = template_name
        self.main_window.output_text.clear()
        self.main_window.btn_send.setEnabled(False)
        self._ai_response_started = False
        self._ai_wait_elapsed.start()
        self._ai_wait_timer.start()
        self.ai_status_signal.emit(f"正在请求模型「{active_profile.display_name}」...")

        threading.Thread(
            target=self._run_ai_task,
            args=(template_name, user_input, active_profile),
            daemon=True,
        ).start()

    def _run_ai_task(self, template_name, user_input, active_profile):
        try:
            context = ""
            if prompt_engine.is_search_enabled(template_name):
                logger.info(f"开始联网检索: {user_input}")
                search_results = search_service.search(user_input)
                urls = [res["url"] for res in search_results]
                context = content_extractor.get_summarized_context(urls)

            messages = prompt_engine.format_prompt(template_name, user_input, context)

            full_response = ""
            for chunk in llm_client.chat_stream(messages, active_profile):
                if not full_response and chunk.lstrip().startswith("Error:"):
                    self.ai_finished_signal.emit(False, chunk)
                    return

                full_response += chunk
                self.ai_chunk_signal.emit(chunk)

            self.ai_finished_signal.emit(True, full_response)

        except Exception as e:
            logger.error(f"AI 请求任务失败: {e}")
            self.ai_finished_signal.emit(False, str(e))

    def _on_ai_chunk_received(self, chunk):
        if not self._ai_response_started:
            self._ai_response_started = True
            self._ai_wait_timer.stop()
            self.main_window.set_response_status("模型已开始输出")

    def _update_ai_wait_status(self):
        if self._ai_response_started or not self._ai_wait_elapsed.isValid():
            return

        seconds = max(1, int((self._ai_wait_elapsed.elapsed() + 999) / 1000))
        self.main_window.set_response_status(f"等待模型响应，已等待 {seconds} 秒")

    def _update_speech_wait_status(self):
        if not self._speech_wait_elapsed.isValid():
            return

        seconds = max(1, int((self._speech_wait_elapsed.elapsed() + 999) / 1000))
        if self._speech_recording:
            self.main_window.set_response_status(
                f"正在录音（系统音频 + 麦克风），已录音 {seconds} 秒，再按一次结束"
            )
        elif self._speech_processing:
            self.main_window.set_response_status(f"录音已结束，正在离线识别，已用时 {seconds} 秒")

    @Slot(bool, str)
    def _on_speech_finished(self, success: bool, payload: str):
        self._speech_wait_timer.stop()
        self._speech_recording = False
        self._speech_processing = False
        self._speech_stage = ""

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
        if self._is_shutting_down:
            return
        if not text.strip():
            return
        self._apply_speech_text(text)
        if self._speech_recording or self._speech_processing:
            self.main_window.set_response_status("语音识别中，结果已实时写入输入框")

    def _on_ai_finished(self, success: bool, payload: str):
        self._ai_wait_timer.stop()
        self.main_window.btn_send.setEnabled(True)

        if success:
            if payload.strip():
                db_manager.add_history("user", self._current_user_input, self.session_id)
                db_manager.add_history("assistant", payload, self.session_id)
                self.main_window.set_response_status("生成完成")
            else:
                self.main_window.set_response_status("模型未返回内容")
            return

        if payload:
            existing_text = self.main_window.output_text.toPlainText().strip()
            if existing_text:
                self.main_window.append_output(f"\n{payload}")
            else:
                self.main_window.append_output(payload)

        self.main_window.set_response_status("请求失败")

    @Slot()
    def copy_result(self):
        text = self.main_window.output_text.toPlainText()
        if not text.strip():
            self.main_window.set_response_status("没有可复制的结果")
            return

        if clipboard_service.copy_to_clipboard(text):
            self.main_window.set_response_status("结果已复制到剪贴板")
        else:
            self.main_window.set_response_status("复制失败")

    @Slot()
    def paste_result(self):
        text = self.main_window.output_text.toPlainText()
        if not text.strip():
            self.main_window.set_response_status("没有可回填的结果")
            return

        if not self._last_foreground_hwnd:
            if clipboard_service.copy_to_clipboard(text):
                self.main_window.set_response_status("已复制到剪贴板，但没有捕获到原窗口")
            else:
                self.main_window.set_response_status("回填失败")
            return

        self.main_window.hide()
        if clipboard_service.auto_paste(text, self._last_foreground_hwnd):
            self.main_window.set_response_status("已回填到原窗口")
            return

        self._show_main_window()
        if clipboard_service.copy_to_clipboard(text):
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

        try:
            speech_service.cancel_recording()
        except Exception as e:
            logger.warning(f"停止语音录音失败: {e}")

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

    @Slot()
    def on_config_updated(self):
        self.hotkey_thread.register_hotkeys()
        ocr_service.set_mode(getattr(config_manager.config, "ocr_engine", "rapid"))
        ocr_service.invalidate_cache()
        self.ocr_status_signal.emit(ocr_service.get_status())
        threading.Thread(target=self._preload_ocr_task, daemon=True).start()
        speech_service.invalidate_cache()

        prompt_engine.refresh_templates()
        self._load_templates()
        self._load_models()

        logger.info("配置已动态更新")

    def start(self):
        self.hotkey_thread.start()
        self._show_main_window()
        self.ocr_status_signal.emit(ocr_service.get_status())
        self.main_window.set_response_status("待发送")

        threading.Thread(target=self._preload_background_services, daemon=True).start()

    def _preload_background_services(self):
        if speech_service.has_model():
            try:
                speech_service.preload()
            except Exception as e:
                logger.warning(f"语音识别模型预热失败: {e}")

        try:
            self._preload_ocr_task()
        except Exception as e:
            logger.warning(f"OCR 预热失败: {e}")


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    controller = AppController()
    controller.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
