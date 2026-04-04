import time

import keyboard
from PySide6.QtCore import QThread, Signal

from app.storage.config import config
from app.utils.logger import logger


class HotkeyThread(QThread):
    """
    后台热键监听线程。
    """

    trigger_main_signal = Signal()
    trigger_selection_signal = Signal()
    trigger_screenshot_signal = Signal()

    def __init__(self):
        super().__init__()
        self.is_running = True

    def run(self):
        self.register_hotkeys()
        while self.is_running:
            time.sleep(0.1)

    def register_hotkeys(self):
        """
        注册或重新注册热键。
        """
        try:
            keyboard.unhook_all()
            keyboard.add_hotkey(config.hotkey_main, self.trigger_main_signal.emit)
            keyboard.add_hotkey(config.hotkey_selection, self.trigger_selection_signal.emit)
            keyboard.add_hotkey(config.hotkey_screenshot, self.trigger_screenshot_signal.emit)

            logger.info(
                f"热键已更新: 主窗口={config.hotkey_main}, "
                f"选中文本={config.hotkey_selection}, 截图={config.hotkey_screenshot}"
            )
        except Exception as e:
            logger.error(f"热键注册失败: {e}")

    def stop(self):
        self.is_running = False
        keyboard.unhook_all()
        self.quit()
        if self.isRunning():
            self.wait(1000)
