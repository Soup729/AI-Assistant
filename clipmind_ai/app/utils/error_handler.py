import sys
from app.utils.logger import logger
from PySide6.QtWidgets import QMessageBox

def handle_exception(exc_type, exc_value, exc_traceback):
    """
    全局异常捕获
    """
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
    
    # 在主线程中弹出错误提示（如果是 UI 线程出错）
    # 这里是一个简化的实现
    print(f"致命错误: {exc_value}")

def setup_error_handler():
    import sys
    sys.excepthook = handle_exception
