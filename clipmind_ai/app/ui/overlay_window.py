from PySide6.QtWidgets import QWidget, QApplication, QRubberBand
from PySide6.QtCore import Qt, QRect, QSize, QPoint, Signal
from PySide6.QtGui import QPainter, QColor, QPixmap, QScreen, QBrush
import mss
from app.utils.logger import logger

class OverlayWindow(QWidget):
    """
    截图遮罩层，采用先截全屏再蒙层的稳定方案
    """
    screenshot_captured = Signal(object)
    screenshot_cancelled = Signal()

    def __init__(self):
        super().__init__()
        # 设置窗口属性：置顶、全屏、无边框
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setWindowState(Qt.WindowFullScreen)
        # 注意：这里不再设置 WA_TranslucentBackground，因为我们要自己画背景
        self.setCursor(Qt.CrossCursor)
        # 设置焦点策略，确保能接收键盘事件
        self.setFocusPolicy(Qt.StrongFocus)

        # 1. 立即捕获全屏内容
        screen = QApplication.primaryScreen()
        self.full_screen_pixmap = screen.grabWindow(0)
        self.device_pixel_ratio = self.full_screen_pixmap.devicePixelRatio()

        self.origin = QPoint()
        self.rubberBand = QRubberBand(QRubberBand.Rectangle, self)

    def paintEvent(self, event):
        painter = QPainter(self)
        # 2. 绘制原始全屏内容
        painter.drawPixmap(0, 0, self.full_screen_pixmap)
        
        # 3. 绘制一层半透明黑色蒙层 (dim the screen)
        painter.setBrush(QColor(0, 0, 0, 100))
        painter.setPen(Qt.NoPen)
        
        if self.rubberBand.isVisible():
            # 如果正在拉框，避开拉框区域绘制蒙层
            rect = self.rubberBand.geometry()
            # 这里我们通过排除法绘制蒙层
            # 也可以简单地画全屏蒙层，然后在选区重画原始图片
            painter.drawRect(0, 0, self.width(), rect.y()) # 上
            painter.drawRect(0, rect.y(), rect.x(), rect.height()) # 左
            painter.drawRect(rect.right(), rect.y(), self.width() - rect.right(), rect.height()) # 右
            painter.drawRect(0, rect.bottom(), self.width(), self.height() - rect.bottom()) # 下
            
            # 绘制选区边框
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QColor(24, 144, 255)) # 蓝色边框
            painter.drawRect(rect)
        else:
            # 没拉框时全屏蒙层
            painter.drawRect(self.rect())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.origin = event.position().toPoint()
            self.rubberBand.setGeometry(QRect(self.origin, QSize()))
            self.rubberBand.show()
            self.update()

    def mouseMoveEvent(self, event):
        if not self.origin.isNull():
            self.rubberBand.setGeometry(QRect(self.origin, event.position().toPoint()).normalized())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.rubberBand.hide()
            rect = self.rubberBand.geometry()

            # 区域过小时视为取消操作
            if rect.width() <= 5 or rect.height() <= 5:
                self.screenshot_cancelled.emit()
                self.close()
                return

            self.hide()
            QApplication.processEvents()

            # 从我们之前存的全屏图中裁剪出选区
            source_rect = QRect(
                int(rect.x() * self.device_pixel_ratio),
                int(rect.y() * self.device_pixel_ratio),
                int(rect.width() * self.device_pixel_ratio),
                int(rect.height() * self.device_pixel_ratio),
            )
            selected_pixmap = self.full_screen_pixmap.copy(source_rect)
            selected_pixmap.setDevicePixelRatio(1.0)
            image = selected_pixmap.toImage()
            self.screenshot_captured.emit(image)
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.screenshot_cancelled.emit()
            self.close()

class ScreenshotService:
    def __init__(self):
        self.overlay = None

    def start_selection(self, callback, cancel_callback=None):
        try:
            # 确保在主线程创建和显示
            self.overlay = OverlayWindow()
            self.overlay.screenshot_captured.connect(callback)
            if cancel_callback:
                self.overlay.screenshot_cancelled.connect(cancel_callback)
            self.overlay.show()
            self.overlay.raise_()
            self.overlay.activateWindow()
            self.overlay.setFocus()
            logger.info("启动截图区域选择")
        except Exception as e:
            logger.error(f"启动截图服务失败: {e}")

screenshot_service = ScreenshotService()
