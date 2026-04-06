import ctypes
import sys
from typing import Any, Iterable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
    QRect,
)
from PySide6.QtGui import QColor, QFont, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QApplication,
    QPushButton,
    QSizeGrip,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.storage.config import config_manager
from app.utils.logger import logger
from app.utils.runtime_paths import get_project_root



class DraggableTitleBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    request_exit_signal = Signal()
    model_changed_signal = Signal(str)
    source_link_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ClipMind AI")
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(0.95)
        self.setMinimumSize(450, 560)

        # 恢复窗口位置和大小
        self._restore_geometry()

        self._init_ui()
        self._setup_style()

    def _init_ui(self):
        self.central_widget = QWidget()
        self.central_widget.setObjectName("centralWidget")
        self.setCentralWidget(self.central_widget)

        layout = QVBoxLayout(self.central_widget)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        self.title_bar = DraggableTitleBar()
        self.title_bar.setObjectName("titleBar")
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(10, 5, 10, 5)

        self.title_label = QLabel("ClipMind AI")
        self.title_label.setObjectName("titleLabel")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setFixedSize(28, 28)
        self.btn_settings.setObjectName("btnTitle")

        self.btn_close = QPushButton("×")
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setObjectName("btnClose")
        self.btn_close.clicked.connect(self.close)

        title_layout.addWidget(self.title_label)
        title_layout.addStretch()
        title_layout.addWidget(self.btn_settings)
        title_layout.addWidget(self.btn_close)

        self.model_label = QLabel("当前模型")
        self.model_label.setObjectName("sectionLabel")

        self.combo_model = QComboBox()
        self.combo_model.setObjectName("comboModel")
        self.combo_model.setFixedHeight(32)
        self.combo_model.setToolTip("切换当前启用的模型")
        self.combo_model.currentIndexChanged.connect(self._emit_model_changed)

        self.combo_template = QComboBox()
        self.combo_template.setObjectName("comboTemplate")
        self.combo_template.setFixedHeight(32)

        # 状态栏：OCR、语音、RAG、搜索 一行排列
        self.ocr_status_label = QLabel("OCR")
        self.ocr_status_label.setObjectName("ocrStatusLabel")
        self.speech_status_label = QLabel("语音")
        self.speech_status_label.setObjectName("speechStatusLabel")
        self.rag_status_label = QLabel("RAG")
        self.rag_status_label.setObjectName("ragStatusLabel")
        self.search_status_label = QLabel("搜索")
        self.search_status_label.setObjectName("searchStatusLabel")

        self.response_status_label = QLabel("状态：等待发送")
        self.response_status_label.setObjectName("responseStatusLabel")

        self.input_text = QTextEdit()
        self.input_text.setObjectName("inputText")
        self.input_text.setPlaceholderText("在这里输入问题，或粘贴选中的文本...")
        self.input_text.setMaximumHeight(120)

        self.output_text = QTextBrowser()
        self.output_text.setObjectName("outputText")
        self.output_text.setOpenExternalLinks(False)
        self.output_text.setPlaceholderText("AI 的回答会显示在这里...")
        self.output_text.anchorClicked.connect(self._on_output_anchor_clicked)

        btn_layout = QHBoxLayout()
        self.btn_copy = QPushButton("复制结果")
        self.btn_copy.setObjectName("btnAction")
        self.btn_send = QPushButton("发送 (Enter)")
        self.btn_send.setObjectName("btnSend")
        self.btn_send.setCursor(Qt.PointingHandCursor)

        btn_layout.addWidget(self.btn_copy)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_send)

        # 状态栏：OCR、语音、RAG、搜索 一行排列，AI状态单独一行
        status_layout = QHBoxLayout()
        status_layout.addWidget(self.ocr_status_label)
        status_layout.addWidget(self.speech_status_label)
        status_layout.addWidget(self.rag_status_label)
        status_layout.addWidget(self.search_status_label)
        status_layout.addStretch()

        layout.addWidget(self.title_bar)
        layout.addWidget(self.model_label)
        layout.addWidget(self.combo_model)
        layout.addWidget(self.combo_template)
        layout.addLayout(status_layout)
        layout.addWidget(self.response_status_label)
        layout.addWidget(self.input_text)
        layout.addWidget(self.output_text)
        layout.addLayout(btn_layout)

        # 添加窗口大小调整把手
        self.size_grip = QSizeGrip(self)
        self.size_grip.setObjectName("sizeGrip")
        size_grip_layout = QHBoxLayout()
        size_grip_layout.addStretch()
        size_grip_layout.addWidget(self.size_grip)
        layout.addLayout(size_grip_layout)

    def _setup_style(self):
        self.setStyleSheet(
            """
            #centralWidget {
                background-color: rgba(255, 255, 255, 230);
                border: 1px solid rgba(200, 200, 200, 150);
                border-radius: 12px;
            }
            #titleBar {
                background-color: transparent;
                border-bottom: 1px solid rgba(230, 230, 230, 150);
                margin-bottom: 5px;
            }
            #titleLabel {
                font-weight: bold;
                font-size: 16px;
                color: #333333;
                font-family: "Segoe UI", "Microsoft YaHei";
            }
            #sectionLabel {
                color: #444444;
                font-size: 12px;
                font-weight: 600;
                padding-left: 2px;
            }
            #btnTitle, #btnClose {
                border: none;
                background: transparent;
                font-size: 20px;
                color: #666666;
                border-radius: 4px;
            }
            #btnClose:hover {
                background-color: #ff4d4f;
                color: white;
            }
            #btnTitle:hover {
                background-color: #e6e6e6;
            }
            QTextEdit#inputText, QTextBrowser#outputText {
                background-color: rgba(255, 255, 255, 180);
                border: 1px solid rgba(220, 220, 220, 200);
                border-radius: 8px;
                padding: 10px;
                font-family: 'Segoe UI', 'Microsoft YaHei';
                font-size: 14px;
                color: #333333;
            }
            QTextBrowser#outputText a {
                color: #1890ff;
                text-decoration: none;
            }
            QTextBrowser#outputText a:hover {
                color: #40a9ff;
                text-decoration: underline;
            }
            QComboBox {
                background-color: rgba(255, 255, 255, 180);
                border: 1px solid rgba(220, 220, 220, 200);
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 13px;
            }
            QComboBox::drop-down {
                border: none;
            }
            #ocrStatusLabel, #speechStatusLabel, #ragStatusLabel, #searchStatusLabel {
                color: #888888;
                font-size: 12px;
                padding: 3px 10px 3px 10px;
                border: 1px solid rgba(200, 200, 200, 150);
                border-radius: 4px;
                background-color: rgba(245, 245, 245, 180);
            }
            #responseStatusLabel {
                color: #666666;
                font-size: 12px;
                padding: 2px 4px 2px 4px;
            }
            QPushButton#btnAction {
                padding: 6px 12px;
                border-radius: 6px;
                background-color: rgba(245, 245, 245, 200);
                border: 1px solid rgba(210, 210, 210, 150);
                font-size: 13px;
                color: #555555;
            }
            QPushButton#btnAction:hover {
                background-color: #ffffff;
                border-color: #1890ff;
                color: #1890ff;
            }
            #btnSend {
                padding: 8px 20px;
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 14px;
            }
            #btnSend:hover {
                background-color: #40a9ff;
            }
            #btnSend:disabled {
                background-color: #bfbfbf;
            }
            #sizeGrip {
                width: 16px;
                height: 16px;
                background-color: transparent;
                border: none;
            }
            """
        )

    def _emit_model_changed(self, index: int):
        model_id = self.combo_model.itemData(index)
        if model_id:
            self.model_changed_signal.emit(str(model_id))

    @Slot(QUrl)
    def _on_output_anchor_clicked(self, url: QUrl):
        # 阻止 QTextBrowser 的默认导航行为，否则会清空内容
        self.output_text.setSource(QUrl())
        if url.isValid():
            self.source_link_signal.emit(url.toString())

    def _model_label(self, profile: Any) -> str:
        display_name = getattr(profile, "display_name", "") or ""
        model_name = getattr(profile, "model_name", "") or ""
        if display_name and model_name and display_name != model_name:
            return f"{display_name} ({model_name})"
        return display_name or model_name or "未命名模型"

    def _model_tooltip(self, profile: Any) -> str:
        api_base_url = getattr(profile, "api_base_url", "") or ""
        model_name = getattr(profile, "model_name", "") or ""
        temperature = getattr(profile, "temperature", "")
        max_tokens = getattr(profile, "max_tokens", "")
        return (
            f"Base URL: {api_base_url}\n"
            f"Model: {model_name}\n"
            f"Temperature: {temperature}\n"
            f"Temperature: {temperature}\n"
            f"Temperature: {temperature}\n"
            f"Temperature: {temperature}\n"
            f"Max Tokens: {max_tokens}"
        )

    def set_model_profiles(self, profiles: Iterable[Any], active_model_id: str = ""):
        self.combo_model.blockSignals(True)
        self.combo_model.clear()

        profiles = list(profiles)
        if not profiles:
            self.combo_model.addItem("未配置模型", "")
            self.combo_model.setEnabled(False)
            self.combo_model.blockSignals(False)
            return

        self.combo_model.setEnabled(True)
        for profile in profiles:
            model_id = getattr(profile, "id", "") or ""
            label = self._model_label(profile)
            index = self.combo_model.count()
            self.combo_model.addItem(label, model_id)
            self.combo_model.setItemData(index, self._model_tooltip(profile), Qt.ToolTipRole)

        target_index = self.combo_model.findData(active_model_id) if active_model_id else -1
        if target_index < 0:
            target_index = 0
        self.combo_model.setCurrentIndex(target_index)
        self.combo_model.blockSignals(False)

    def append_output(self, text: str):
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.output_text.setTextCursor(cursor)
        self.output_text.ensureCursorVisible()

    def append_output_html(self, html: str):
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(html)
        self.output_text.setTextCursor(cursor)
        self.output_text.ensureCursorVisible()

    def get_output_text(self) -> str:
        return self.output_text.toPlainText()

    def set_input(self, text: str):
        self.input_text.setPlainText(text)
        self.input_text.moveCursor(QTextCursor.End)
        self.show()
        self.activateWindow()

    def append_input(self, text: str):
        cursor = self.input_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.input_text.setTextCursor(cursor)
        self.input_text.ensureCursorVisible()
        self.show()
        self.activateWindow()

    def set_ocr_status(self, text: str):
        self.ocr_status_label.setText(f"OCR {text}")

    def set_speech_status(self, text: str):
        self.speech_status_label.setText(f"语音 {text}")

    def set_rag_status(self, text: str):
        self.rag_status_label.setText(f"RAG {text}")

    def set_search_status(self, text: str):
        self.search_status_label.setText(f"搜索 {text}")

    def set_response_status(self, text: str):
        self.response_status_label.setText(f"状态：{text}")

    def _restore_geometry(self):
        """恢复窗口位置和大小"""
        geometry = config_manager.config.window_geometry
        if geometry:
            try:
                from json import loads
                data = loads(geometry)
                # 确保恢复的窗口至少部分可见
                frame_geo = QRect(
                    data.get("x", 100),
                    data.get("y", 100),
                    data.get("width", 500),
                    data.get("height", 750)
                )
                # 验证窗口至少部分在屏幕内
                screen = QApplication.primaryScreen().geometry()
                if screen.intersects(frame_geo):
                    self.setGeometry(frame_geo)
                    return
            except Exception as e:
                logger.warning(f"恢复窗口几何信息失败: {e}")
        # 默认居中显示
        self._center_on_screen()

    def _save_geometry(self):
        """保存窗口位置和大小"""
        try:
            from json import dumps
            geo = self.geometry()
            data = {
                "x": geo.x(),
                "y": geo.y(),
                "width": geo.width(),
                "height": geo.height()
            }
            config_manager.update(window_geometry=dumps(data))
        except Exception as e:
            logger.warning(f"保存窗口几何信息失败: {e}")

    def _center_on_screen(self):
        """窗口居中显示"""
        screen = QApplication.primaryScreen().geometry()
        geo = self.geometry()
        geo.moveCenter(screen.center())
        self.setGeometry(geo)

    def moveEvent(self, event):
        """窗口移动后保存位置"""
        super().moveEvent(event)
        self._save_geometry()

    def resizeEvent(self, event):
        """窗口大小变化后保存大小"""
        super().resizeEvent(event)
        self._save_geometry()

    def closeEvent(self, event):
        self.request_exit_signal.emit()
        event.accept()


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
