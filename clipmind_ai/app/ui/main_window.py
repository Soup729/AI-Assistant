import sys
from typing import Any, Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


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

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ClipMind AI")
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(0.95)
        self.setMinimumSize(450, 660)

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

        self.ocr_status_label = QLabel("OCR：未初始化")
        self.ocr_status_label.setObjectName("ocrStatusLabel")

        self.response_status_label = QLabel("AI：等待发送")
        self.response_status_label.setObjectName("responseStatusLabel")

        self.input_text = QTextEdit()
        self.input_text.setObjectName("inputText")
        self.input_text.setPlaceholderText("在这里输入问题，或粘贴选中的文本...")
        self.input_text.setMaximumHeight(120)

        self.output_text = QTextEdit()
        self.output_text.setObjectName("outputText")
        self.output_text.setReadOnly(True)
        self.output_text.setPlaceholderText("AI 的回答会显示在这里...")

        btn_layout = QHBoxLayout()
        self.btn_copy = QPushButton("复制结果")
        self.btn_copy.setObjectName("btnAction")
        self.btn_paste = QPushButton("自动回填")
        self.btn_paste.setObjectName("btnAction")

        self.btn_send = QPushButton("发送(Enter)")
        self.btn_send.setObjectName("btnSend")
        self.btn_send.setCursor(Qt.PointingHandCursor)

        btn_layout.addWidget(self.btn_copy)
        btn_layout.addWidget(self.btn_paste)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_send)

        layout.addWidget(self.title_bar)
        layout.addWidget(self.model_label)
        layout.addWidget(self.combo_model)
        layout.addWidget(self.combo_template)
        layout.addWidget(self.ocr_status_label)
        layout.addWidget(self.response_status_label)
        layout.addWidget(self.input_text)
        layout.addWidget(self.output_text)
        layout.addLayout(btn_layout)

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
            QTextEdit#inputText, QTextEdit#outputText {
                background-color: rgba(255, 255, 255, 180);
                border: 1px solid rgba(220, 220, 220, 200);
                border-radius: 8px;
                padding: 10px;
                font-family: 'Segoe UI', 'Microsoft YaHei';
                font-size: 14px;
                color: #333333;
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
            #ocrStatusLabel, #responseStatusLabel {
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
            """
        )

    def _emit_model_changed(self, index: int):
        model_id = self.combo_model.itemData(index)
        if model_id:
            self.model_changed_signal.emit(str(model_id))

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
        self.ocr_status_label.setText(f"OCR：{text}")

    def set_response_status(self, text: str):
        self.response_status_label.setText(f"AI：{text}")

    def closeEvent(self, event):
        self.request_exit_signal.emit()
        event.accept()


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
