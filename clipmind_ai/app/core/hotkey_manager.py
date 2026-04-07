import threading
import time

import keyboard
from PySide6.QtCore import QThread, Signal

from app.storage.config import config_manager
from app.utils.logger import logger


class HotkeyThread(QThread):
    trigger_main_signal = Signal()
    trigger_selection_signal = Signal()
    trigger_screenshot_signal = Signal()
    trigger_speech_signal = Signal()
    trigger_paste_signal = Signal()
    trigger_send_signal = Signal()
    trigger_scroll_up_signal = Signal()
    trigger_scroll_down_signal = Signal()
    trigger_clear_input_signal = Signal()
    trigger_delete_char_signal = Signal()

    def __init__(self):
        super().__init__()
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._hotkey_handles: dict[str, int] = {}
        self._default_hotkeys = {
            "main": "alt+space",
            "selection": "alt+q",
            "screenshot": "alt+w",
            "speech": "alt+e",
            "paste": "alt+h",
            "send": "alt+k",
            "scroll_up": "alt+up",
            "scroll_down": "alt+down",
            "clear_input": "alt+del",
            "delete_char": "alt+backspace",
        }

    def _normalize_hotkey(self, value: str) -> str:
        key = (value or "").strip().lower()
        if not key:
            return ""
        # 用户可能输入 "alt + h" 这类带空格写法，统一规整。
        key = key.replace(" + ", "+").replace("+ ", "+").replace(" +", "+")
        key = " ".join(key.split())
        return key

    def run(self):
        self._stop_event.clear()
        self.register_hotkeys()
        while not self._stop_event.is_set():
            time.sleep(0.2)
        self._clear_hotkeys()

    def _clear_hotkeys(self):
        with self._lock:
            handles = list(self._hotkey_handles.values())
            self._hotkey_handles.clear()

        for handle in handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                continue

    def register_hotkeys(self):
        with self._lock:
            self._clear_hotkeys()
            cfg = config_manager.config
            hotkey_map = {
                "main": (getattr(cfg, "hotkey_main", self._default_hotkeys["main"]), self.trigger_main_signal.emit),
                "selection": (
                    getattr(cfg, "hotkey_selection", self._default_hotkeys["selection"]),
                    self.trigger_selection_signal.emit,
                ),
                "screenshot": (
                    getattr(cfg, "hotkey_screenshot", self._default_hotkeys["screenshot"]),
                    self.trigger_screenshot_signal.emit,
                ),
                "speech": (
                    getattr(cfg, "hotkey_speech", self._default_hotkeys["speech"]),
                    self.trigger_speech_signal.emit,
                ),
                "paste": (getattr(cfg, "hotkey_paste", self._default_hotkeys["paste"]), self.trigger_paste_signal.emit),
                "send": (getattr(cfg, "hotkey_send", self._default_hotkeys["send"]), self.trigger_send_signal.emit),
                "scroll_up": (
                    getattr(cfg, "hotkey_scroll_up", self._default_hotkeys["scroll_up"]),
                    self.trigger_scroll_up_signal.emit,
                ),
                "scroll_down": (
                    getattr(cfg, "hotkey_scroll_down", self._default_hotkeys["scroll_down"]),
                    self.trigger_scroll_down_signal.emit,
                ),
                "clear_input": (
                    getattr(cfg, "hotkey_clear_input", self._default_hotkeys["clear_input"]),
                    self.trigger_clear_input_signal.emit,
                ),
                "delete_char": (
                    getattr(cfg, "hotkey_delete_char", self._default_hotkeys["delete_char"]),
                    self.trigger_delete_char_signal.emit,
                ),
            }

            registered_keys: dict[str, str] = {}
            for name, (hotkey, callback) in hotkey_map.items():
                hotkey = self._normalize_hotkey(hotkey)
                if not hotkey:
                    hotkey = self._default_hotkeys[name]
                try:
                    handle = keyboard.add_hotkey(hotkey, callback)
                    self._hotkey_handles[name] = handle
                    registered_keys[name] = hotkey
                except Exception as e:
                    fallback = self._default_hotkeys[name]
                    if hotkey != fallback:
                        try:
                            handle = keyboard.add_hotkey(fallback, callback)
                            self._hotkey_handles[name] = handle
                            registered_keys[name] = fallback
                            logger.warning(f"热键注册失败 [{name}:{hotkey}]，已回退默认键位 [{fallback}]")
                            continue
                        except Exception as fallback_error:
                            logger.error(
                                f"热键注册失败 [{name}:{hotkey}]，回退默认键位 [{fallback}] 仍失败: {fallback_error}"
                            )
                            continue
                    logger.error(f"热键注册失败 [{name}:{hotkey}]: {e}")

            logger.info(
                "热键已更新: "
                f"main={registered_keys.get('main', '未注册')}, "
                f"selection={registered_keys.get('selection', '未注册')}, "
                f"screenshot={registered_keys.get('screenshot', '未注册')}, "
                f"speech={registered_keys.get('speech', '未注册')}, "
                f"paste={registered_keys.get('paste', '未注册')}, "
                f"send={registered_keys.get('send', '未注册')}, "
                f"scroll_up={registered_keys.get('scroll_up', '未注册')}, "
                f"scroll_down={registered_keys.get('scroll_down', '未注册')}, "
                f"clear_input={registered_keys.get('clear_input', '未注册')}, "
                f"delete_char={registered_keys.get('delete_char', '未注册')}, "
                f"total={len(self._hotkey_handles)}"
            )

    def stop(self):
        self._stop_event.set()
        self._clear_hotkeys()
        self.quit()
        self.wait(2000)
