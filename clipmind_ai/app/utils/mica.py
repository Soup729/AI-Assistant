"""
Windows 毛玻璃/材质效果工具模块。

三种材质各自独立实现，视觉与性能特征完全不同：

  - "mica"     : Mica Standard，win32mica（MicaTheme.AUTO + MicaStyle.DEFAULT）
  - "mica_alt" : 深度云母，DwmSetWindowAttribute + DWMSBT_TABBEDWINDOW (值=4)
                 仅采样桌面壁纸，不透视其他窗口，零性能损耗，Win11 专用
  - "acrylic"  : 亚克力，DwmSetWindowAttribute + DWMSBT_TRANSIENTWINDOW (值=3)
                 实时透视并模糊所有背后窗口，含噪点纹理，Win11 专用
  - "blur"     : 纯净毛玻璃，SetWindowCompositionAttribute + ACCENT_ENABLE_BLURBEHIND
                 无噪点均匀高斯模糊，macOS 质感，Win10+ 支持
  - "none"     : 移除所有材质效果
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

logger: Optional[object] = None


def _get_logger():
    global logger
    if logger is None:
        try:
            from app.utils.logger import logger as _logger
            logger = _logger
        except Exception:
            class _DummyLogger:
                def warning(self, msg, *args, **kwargs): pass
                def info(self, msg, *args, **kwargs): pass
            logger = _DummyLogger()
    return logger


# ---------------------------------------------------------------------------
# Windows 版本检测
# ---------------------------------------------------------------------------

class WindowsVersion(IntEnum):
    UNSUPPORTED = 0
    WIN10_BASE = 1    # Win10 1703+, 支持 BlurBehind
    WIN11_BASE = 2    # Win11 (build >= 22000)
    WIN11_22H1 = 3   # Win11 22H1 (build >= 22621)


def _get_windows_version() -> WindowsVersion:
    try:
        if sys.platform != "win32":
            return WindowsVersion.UNSUPPORTED
        major = ctypes.c_ulong()
        minor = ctypes.c_ulong()
        build = ctypes.c_ulong()
        ctypes.windll.ntdll.RtlGetNtVersionNumbers(
            ctypes.byref(major), ctypes.byref(minor), ctypes.byref(build)
        )
        build_val = build.value
        if major.value == 10 and build_val < 22000:
            return WindowsVersion.WIN10_BASE if build_val >= 17134 else WindowsVersion.UNSUPPORTED
        if major.value == 10 and build_val >= 22000:
            return WindowsVersion.WIN11_22H1 if build_val >= 22621 else WindowsVersion.WIN11_BASE
        if major.value > 10:
            return WindowsVersion.WIN11_22H1
        return WindowsVersion.UNSUPPORTED
    except Exception:
        return WindowsVersion.UNSUPPORTED


# ---------------------------------------------------------------------------
# win32mica 导入（仅用于标准 Mica Default）
# ---------------------------------------------------------------------------

_WIN32MICA_AVAILABLE = False
_MicaTheme = _MicaStyle = None
_ApplyMica: Optional[callable] = None

if sys.platform == "win32":
    try:
        import win32mica as _win32mica
        _WIN32MICA_AVAILABLE = True
        _MicaTheme = _win32mica.MicaTheme
        _MicaStyle = _win32mica.MicaStyle
        _ApplyMica = _win32mica.ApplyMica
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Win32 常量
# ---------------------------------------------------------------------------

# DwmSetWindowAttribute — SystemBackdropType
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMSBT_TABBEDWINDOW = 4     # Mica Alt：仅采样壁纸，不透视其他窗口
_DWMSBT_TRANSIENTWINDOW = 3  # Acrylic：透视并模糊所有背后窗口，含噪点纹理

# SetWindowCompositionAttribute — AccentState
_ACCENT_DISABLED = 0
_ACCENT_ENABLE_BLURBEHIND = 3
_WCA_ACCENT_POLICY = 19


# ---------------------------------------------------------------------------
# ctypes 结构体
# ---------------------------------------------------------------------------

class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_uint),
        ("AccentFlags", ctypes.c_uint),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_uint),
    ]


class _WINDOWCOMPOSITIONATTRIBUTEDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_uint),
        ("Data", ctypes.POINTER(ctypes.c_void_p)),
        ("SizeOfData", ctypes.c_size_t),
    ]


# ---------------------------------------------------------------------------
# DwmSetWindowAttribute — Win11 专用，DWMSBT_* 实现
# ---------------------------------------------------------------------------

def _dwm_set_backdrop(hwnd: int, backdrop_type: int) -> bool:
    """通过 DwmSetWindowAttribute 设置窗口 SystemBackdropType。

    backdrop_type:
      4 = DWMSBT_TABBEDWINDOW  -> 深度云母（Mica Alt）
      3 = DWMSBT_TRANSIENTWINDOW -> 亚克力（Acrylic）
    """
    try:
        dwmapi = ctypes.windll.dwmapi
        DwmSetWindowAttribute = dwmapi.DwmSetWindowAttribute
        DwmSetWindowAttribute.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t
        ]
        DwmSetWindowAttribute.restype = ctypes.HRESULT

        value = ctypes.c_int(backdrop_type)
        hr = DwmSetWindowAttribute(
            hwnd,
            _DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(value),
            ctypes.sizeof(value)
        )
        return hr == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# SetWindowCompositionAttribute — BlurBehind（Win10+）
# ---------------------------------------------------------------------------

def _set_blur_behind(hwnd: int, enabled: bool) -> bool:
    """通过 SetWindowCompositionAttribute 启用/禁用 BlurBehind。"""
    try:
        user32 = ctypes.windll.user32
        SetWindowCompositionAttribute = user32.SetWindowCompositionAttribute
        SetWindowCompositionAttribute.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        SetWindowCompositionAttribute.restype = ctypes.c_bool

        accent = _ACCENT_POLICY()
        accent.AccentState = _ACCENT_ENABLE_BLURBEHIND if enabled else _ACCENT_DISABLED

        data = _WINDOWCOMPOSITIONATTRIBUTEDATA()
        data.Attribute = _WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.byref(accent), ctypes.POINTER(ctypes.c_void_p))
        data.SizeOfData = ctypes.sizeof(_ACCENT_POLICY)

        return SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 移除所有效果
# ---------------------------------------------------------------------------

def _remove_all_effects(hwnd: int, silent: bool = False):
    """清除窗口所有 DWM 材质效果。"""
    if _WIN32MICA_AVAILABLE and _ApplyMica is not None:
        try:
            _ApplyMica(hwnd, False, 0)
        except Exception:
            pass
    _set_blur_behind(hwnd, False)
    # 尝试恢复默认 SystemBackdropType
    try:
        value = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, _DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MaterialResult:
    success: bool
    material: str
    reason: Optional[str] = None


def apply_window_material(hwnd: int, material: str, silent: bool = False) -> MaterialResult:
    """
    为给定窗口应用/切换材质效果。

    参数:
        hwnd:     窗口句柄（int(self.winId())）
        material: "none" | "mica" | "mica_alt" | "acrylic" | "blur"
        silent:   True 时不写日志（预览时使用）
    """
    hwnd = int(hwnd)
    _log = _get_logger()

    # 先清除所有现有效果（silent 模式也执行）
    _remove_all_effects(hwnd, silent=silent)

    if material == "none":
        return MaterialResult(success=True, material="none")

    win_ver = _get_windows_version()

    # ---- 深度云母 Mica Alt（DWMSBT_TABBEDWINDOW）----
    if material == "mica_alt":
        if win_ver.value < WindowsVersion.WIN11_BASE.value:
            return MaterialResult(success=False, material=material,
                                  reason="深度云母需要 Windows 11")
        ok = _dwm_set_backdrop(hwnd, _DWMSBT_TABBEDWINDOW)
        if ok:
            if not silent:
                _log.info("深度云母（Mica Alt）已应用，DWMSBT_TABBEDWINDOW=4")
            return MaterialResult(success=True, material=material)
        return MaterialResult(success=False, material=material,
                              reason="DwmSetWindowAttribute 调用失败")

    # ---- 亚克力 Acrylic（DWMSBT_TRANSIENTWINDOW）----
    if material == "acrylic":
        if win_ver.value < WindowsVersion.WIN11_BASE.value:
            return MaterialResult(success=False, material=material,
                                  reason="亚克力需要 Windows 11")
        ok = _dwm_set_backdrop(hwnd, _DWMSBT_TRANSIENTWINDOW)
        if ok:
            if not silent:
                _log.info("亚克力（Acrylic）已应用，DWMSBT_TRANSIENTWINDOW=3")
            return MaterialResult(success=True, material=material)
        return MaterialResult(success=False, material=material,
                              reason="DwmSetWindowAttribute 调用失败")

    # ---- 纯净毛玻璃 Blur（ACCENT_ENABLE_BLURBEHIND）----
    if material == "blur":
        if win_ver == WindowsVersion.UNSUPPORTED:
            return MaterialResult(success=False, material=material,
                                  reason="系统不支持 BlurBehind（需要 Windows 10）")
        ok = _set_blur_behind(hwnd, True)
        if ok:
            if not silent:
                _log.info("纯净毛玻璃（BlurBehind）已应用")
            return MaterialResult(success=True, material=material)
        return MaterialResult(success=False, material=material,
                              reason="SetWindowCompositionAttribute 调用失败")

    # ---- 标准云母 Mica（win32mica）----
    if material == "mica":
        if not _WIN32MICA_AVAILABLE:
            if not silent:
                _log.warning("win32mica 未安装，无法应用标准云母")
            return MaterialResult(success=False, material=material,
                                  reason="win32mica 未安装")
        if win_ver == WindowsVersion.UNSUPPORTED:
            return MaterialResult(success=False, material=material,
                                  reason="系统不支持（需要 Windows 10）")
        if win_ver == WindowsVersion.WIN10_BASE:
            return MaterialResult(success=False, material=material,
                                  reason="标准云母需要 Windows 11")
        try:
            ret = _ApplyMica(hwnd, _MicaTheme.AUTO, _MicaStyle.DEFAULT)
            if not silent:
                _log.info(f"标准云母（Mica）已应用，ret={ret}")
            return MaterialResult(success=True, material=material)
        except Exception as e:
            if not silent:
                _log.warning(f"标准云母应用失败: {e}")
            return MaterialResult(success=False, material=material, reason=str(e))

    return MaterialResult(success=False, material=material,
                          reason=f"未知材质类型: {material}")


# ---------------------------------------------------------------------------
# 支持检测
# ---------------------------------------------------------------------------

def is_mica_supported() -> bool:
    """标准云母 Mica：需要 Windows 11。"""
    return _get_windows_version().value >= WindowsVersion.WIN11_BASE.value


def is_mica_alt_supported() -> bool:
    """深度云母 Mica Alt：通过 DWMSBT_TABBEDWINDOW 实现，需要 Windows 11。"""
    return is_mica_supported()


def is_acrylic_supported() -> bool:
    """亚克力 Acrylic：通过 DWMSBT_TRANSIENTWINDOW 实现，需要 Windows 11。"""
    return is_mica_supported()


def is_blur_supported() -> bool:
    """纯净毛玻璃 BlurBehind：需要 Windows 10 1703+。"""
    return _get_windows_version().value >= WindowsVersion.WIN10_BASE.value


def get_windows_version_info() -> dict:
    ver = _get_windows_version()
    return {
        "level": ver.name,
        "supports_mica": is_mica_supported(),
        "supports_mica_alt": is_mica_alt_supported(),
        "supports_acrylic": is_acrylic_supported(),
        "supports_blur": is_blur_supported(),
    }
