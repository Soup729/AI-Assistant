import time
from contextlib import suppress
from threading import Lock

import keyboard
from PySide6.QtCore import QThread, Signal
from app.storage.config import config
from app.utils.logger import logger

class HotkeyThread(QThread):
    """
    后台线程，用于监听全局热键
    """
    trigger_main_signal = Signal()
    trigger_selection_signal = Signal()
    trigger_screenshot_signal = Signal()
    trigger_speech_signal = Signal()

    def __init__(self):
        super().__init__()
        self.is_running = True
        self._hotkey_handles = []
        self._lock = Lock()

    def run(self):
        self.register_hotkeys()
        while self.is_running:
            time.sleep(0.2)

    def _clear_hotkeys(self):
        for handle in self._hotkey_handles:
            with suppress(Exception):
                keyboard.remove_hotkey(handle)
        self._hotkey_handles.clear()

    def register_hotkeys(self):
        """
        注册或重新注册热键
        """
        try:
            with self._lock:
                self._clear_hotkeys()

                if config.hotkey_main:
                    self._hotkey_handles.append(
                        keyboard.add_hotkey(config.hotkey_main, self.trigger_main_signal.emit)
                    )
                if config.hotkey_selection:
                    self._hotkey_handles.append(
                        keyboard.add_hotkey(config.hotkey_selection, self.trigger_selection_signal.emit)
                    )
                if config.hotkey_screenshot:
                    self._hotkey_handles.append(
                        keyboard.add_hotkey(config.hotkey_screenshot, self.trigger_screenshot_signal.emit)
                    )
                if getattr(config, "hotkey_speech", ""):
                    self._hotkey_handles.append(
                        keyboard.add_hotkey(config.hotkey_speech, self.trigger_speech_signal.emit)
                    )

            logger.info(
                "热键已更新: "
                f"主窗口={config.hotkey_main}, "
                f"选中文本={config.hotkey_selection}, "
                f"截图={config.hotkey_screenshot}, "
                f"语音转文字={getattr(config, 'hotkey_speech', '')}"
            )
        except Exception as e:
            logger.error(f"热键注册失败: {e}")

    def stop(self):
        self.is_running = False
        with self._lock:
            self._clear_hotkeys()
        self.wait(3000)
