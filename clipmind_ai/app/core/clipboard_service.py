import threading
import time
from contextlib import suppress

import win32api
import win32clipboard
import win32con
import win32gui
import win32process
from PySide6.QtCore import QObject, Signal

from app.utils.logger import logger


class ClipboardService(QObject):
    text_ready_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self._lock = threading.RLock()

    def _open_clipboard(self, retries: int = 8, delay: float = 0.04) -> bool:
        last_error = None
        for _ in range(retries):
            try:
                win32clipboard.OpenClipboard()
                return True
            except Exception as exc:  # pragma: no cover
                last_error = exc
                time.sleep(delay)

        if last_error is not None:
            raise last_error
        return False

    def _close_clipboard(self):
        with suppress(Exception):
            win32clipboard.CloseClipboard()

    def _get_clipboard_text(self) -> str:
        with self._lock:
            for _ in range(8):
                try:
                    self._open_clipboard()
                    try:
                        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                        return ""
                    finally:
                        self._close_clipboard()
                except Exception:
                    time.sleep(0.04)
        return ""

    def _set_clipboard_text(self, text: str) -> bool:
        with self._lock:
            for _ in range(8):
                try:
                    self._open_clipboard()
                    try:
                        win32clipboard.EmptyClipboard()
                        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                        return True
                    finally:
                        self._close_clipboard()
                except Exception as exc:
                    logger.debug(f"写入剪贴板失败，准备重试: {exc}")
                    time.sleep(0.04)
        return False

    def _release_modifiers(self):
        for vk in (win32con.VK_MENU, win32con.VK_CONTROL, win32con.VK_SHIFT):
            with suppress(Exception):
                win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)

    def _send_ctrl_combo(self, key_code: int):
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        time.sleep(0.02)
        win32api.keybd_event(key_code, 0, 0, 0)
        time.sleep(0.02)
        win32api.keybd_event(key_code, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.01)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

    def _focus_window(self, hwnd: int) -> bool:
        if not hwnd:
            return False

        try:
            if not win32gui.IsWindow(hwnd):
                return False
            if win32gui.GetForegroundWindow() == hwnd:
                return True

            fg_hwnd = win32gui.GetForegroundWindow()
            current_tid = win32api.GetCurrentThreadId()
            target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
            fg_tid = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0

            attached_pairs: list[tuple[int, int]] = []
            for src, dst in ((current_tid, target_tid), (current_tid, fg_tid), (target_tid, fg_tid)):
                if src and dst and src != dst:
                    try:
                        win32process.AttachThreadInput(src, dst, True)
                        attached_pairs.append((src, dst))
                    except Exception:
                        continue

            try:
                with suppress(Exception):
                    if win32gui.IsIconic(hwnd):
                        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    else:
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                with suppress(Exception):
                    win32gui.BringWindowToTop(hwnd)
                with suppress(Exception):
                    win32gui.SetForegroundWindow(hwnd)
                with suppress(Exception):
                    win32gui.SetActiveWindow(hwnd)
                with suppress(Exception):
                    win32gui.SetFocus(hwnd)
                time.sleep(0.05)
                if win32gui.GetForegroundWindow() == hwnd:
                    return True

                # Second-chance focus attempt.
                with suppress(Exception):
                    win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.08)
                return win32gui.GetForegroundWindow() == hwnd
            finally:
                for src, dst in reversed(attached_pairs):
                    with suppress(Exception):
                        win32process.AttachThreadInput(src, dst, False)
        except Exception as exc:
            logger.warning(f"恢复目标窗口焦点失败: {exc}")
            return False

    def read_selected_text(self) -> str:
        with self._lock:
            original_content = self._get_clipboard_text()
            try:
                self._release_modifiers()
                time.sleep(0.05)
                self._send_ctrl_combo(ord("C"))

                selected_text = ""
                for _ in range(8):
                    time.sleep(0.05)
                    selected_text = self._get_clipboard_text()
                    if selected_text and selected_text != original_content:
                        break

                if selected_text and selected_text.strip():
                    logger.info(f"读取到选中文本 (长度: {len(selected_text)})")
                    self.text_ready_signal.emit(selected_text)
                    return selected_text

                logger.warning("未读取到任何选中文本")
                return ""
            except Exception as exc:
                logger.error(f"读取选中文本时出错: {exc}")
                return ""
            finally:
                self._set_clipboard_text(original_content)

    def copy_to_clipboard(self, text: str) -> bool:
        if self._set_clipboard_text(text):
            logger.info("已成功复制到剪贴板")
            return True
        logger.error("复制到剪贴板失败")
        return False

    def auto_paste(self, text: str, target_hwnd: int, focus_delay_ms: int = 80, restore_delay_ms: int = 150) -> bool:
        with self._lock:
            original_content = self._get_clipboard_text()

            try:
                # 热键触发时常残留 Alt/Ctrl 状态，先显式抬起，避免只触发一次或粘贴失败。
                self._release_modifiers()
                time.sleep(0.02)

                if not self._set_clipboard_text(text):
                    return False

                if not self._focus_window(target_hwnd):
                    logger.warning("未能将焦点切回目标窗口，回填已中止")
                    return False

                time.sleep(max(0, focus_delay_ms) / 1000.0)
                self._send_ctrl_combo(ord("V"))
                self._release_modifiers()
                time.sleep(max(0, restore_delay_ms) / 1000.0)
                logger.info("已执行自动回填")
                return True
            except Exception as exc:
                logger.error(f"自动回填失败: {exc}")
                return False
            finally:
                self._release_modifiers()
                self._set_clipboard_text(original_content)


clipboard_service = ClipboardService()
