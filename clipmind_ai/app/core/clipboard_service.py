import time
from typing import Optional

import keyboard
import win32clipboard
import win32con

try:
    import win32gui
except ImportError:  # pragma: no cover - Windows only dependency
    win32gui = None

from PySide6.QtCore import QObject, Signal

from app.utils.logger import logger


class ClipboardService(QObject):
    text_ready_signal = Signal(str)

    def __init__(self):
        super().__init__()

    def _get_clipboard_text(self):
        """
        直接读取剪贴板文本，避免启动外部进程。
        """
        for _ in range(5):
            opened = False
            try:
                win32clipboard.OpenClipboard()
                opened = True
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                return ""
            except Exception:
                time.sleep(0.1)
            finally:
                if opened:
                    try:
                        win32clipboard.CloseClipboard()
                    except Exception:
                        pass
        return ""

    def _set_clipboard_text(self, text):
        """
        直接设置剪贴板文本。
        """
        for _ in range(5):
            opened = False
            try:
                win32clipboard.OpenClipboard()
                opened = True
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                return True
            except Exception:
                time.sleep(0.1)
            finally:
                if opened:
                    try:
                        win32clipboard.CloseClipboard()
                    except Exception:
                        pass
        return False

    def _restore_foreground_window(self, hwnd: Optional[int]) -> bool:
        if not hwnd or win32gui is None:
            return False

        try:
            if not win32gui.IsWindow(hwnd):
                return False
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.05)
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception as e:
            logger.warning(f"恢复目标窗口失败: {e}")
            return False

    def read_selected_text(self):
        """
        通过模拟 Ctrl+C 读取选中文本。
        """
        try:
            original_content = self._get_clipboard_text()

            self._set_clipboard_text("")

            keyboard.release("alt")
            keyboard.release("shift")
            keyboard.release("ctrl")
            time.sleep(0.05)

            keyboard.send("ctrl+c")

            time.sleep(0.2)

            selected_text = self._get_clipboard_text()

            if selected_text and selected_text.strip():
                logger.info(f"读取到选中文本 (长度: {len(selected_text)})")
                self.text_ready_signal.emit(selected_text)
                return selected_text

            logger.warning("未读取到任何选中文本")
            self._set_clipboard_text(original_content)
            return ""

        except Exception as e:
            logger.error(f"读取选中文本时出错: {e}")
            return ""

    def copy_to_clipboard(self, text: str) -> bool:
        success = self._set_clipboard_text(text)
        if success:
            logger.info("已成功复制到剪贴板")
        else:
            logger.error("复制到剪贴板失败")
        return success

    def auto_paste(self, text: str, target_hwnd: Optional[int] = None) -> bool:
        try:
            if not self._set_clipboard_text(text):
                logger.error("自动回填前设置剪贴板失败")
                return False

            if not self._restore_foreground_window(target_hwnd):
                logger.warning("未找到可回填的目标窗口")
                return False

            time.sleep(0.1)
            keyboard.send("ctrl+v")
            logger.info("已执行自动回填")
            return True
        except Exception as e:
            logger.error(f"自动回填失败: {e}")
            return False


clipboard_service = ClipboardService()
