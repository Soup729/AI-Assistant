import ctypes
import html
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
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QFont,
    QFontDatabase,
    QPainter,
    QPainterPath,
    QPen,
    QTextCursor,
)
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
from app.utils.markdown_renderer import render_markdown
from app.utils.mica import (
    apply_window_material,
    get_windows_version_info,
    is_mica_supported,
    is_mica_alt_supported,
    is_acrylic_supported,
    is_blur_supported,
)



class BorderFrame(QWidget):
    """透明边框流光层：仅负责绘制极细的 RGB 渐变走马灯，不阻挡任何鼠标事件。"""

    BORDER_WIDTH = 2   # 边框宽度（像素）
    TIMER_INTERVAL = 30  # ms，~33fps 足够丝滑

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 盖满父窗口
        self._resize_to_parent()
        # 动画状态
        self._elapsed_ms = 0
        self._timer = QTimer(self)
        self._timer.setInterval(self.TIMER_INTERVAL)
        self._timer.timeout.connect(self._on_tick)
        self._flow_colors = [
            QColor(255, 100, 180),   # 粉色
            QColor(100, 140, 255),   # 蓝紫
            QColor(60, 200, 220),    # 青色
            QColor(80, 230, 120),    # 翠绿
            QColor(255, 210, 60),    # 金黄
            QColor(255, 130, 60),    # 橙色
            QColor(255, 80, 100),    # 珊瑚红
        ]
        self._flow_speed = 25  # 每秒流动的角度（度）

    def _resize_to_parent(self):
        if self.parent():
            self.setGeometry(self.parent().rect())

    def _on_tick(self):
        self._elapsed_ms += self.TIMER_INTERVAL
        self.update()  # 仅更新自己，极低 CPU 消耗

    def paintEvent(self, event):
        geo = self.rect()
        pad = self.BORDER_WIDTH // 2
        rect = geo.adjusted(pad, pad, -pad, -pad)

        # 用 QConicalGradient 实现旋转流光
        cx = rect.center().x()
        cy = rect.center().y()
        angle_deg = (self._elapsed_ms * self._flow_speed) % 360
        gradient = QConicalGradient(cx, cy, angle_deg)
        gradient.setCoordinateMode(QConicalGradient.CoordinateMode.ObjectBoundingMode)

        num = len(self._flow_colors)
        for i, color in enumerate(self._flow_colors):
            gradient.setColorAt(i / num, color)

        pen = QPen()
        pen.setWidthF(self.BORDER_WIDTH)
        pen.setBrush(gradient)   # 用 brush 而非 setColor，渐变通过 brush 传入
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()

    def start_flow(self):
        """启动流光动画。"""
        if self._timer.isActive():
            return
        self._elapsed_ms = 0
        self._timer.start()
        self.show()

    def stop_flow(self):
        """立即停止动画并隐藏。"""
        if self._timer.isActive():
            self._timer.stop()
        self._elapsed_ms = 0
        self.hide()
        self.update()  # 清除残留


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
        self.setAttribute(Qt.WA_ShowWithoutActivating)  # 显示但不抢夺键盘焦点
        self._apply_window_opacity()
        self.setMinimumSize(450, 560)

        # 恢复窗口位置和大小
        self._restore_geometry()

        self._init_ui()
        self._setup_style()
        self._apply_mica_material()

    def _apply_mica_material(self):
        """应用 Mica/Acrylic 毛玻璃材质（如果配置启用且系统支持）。"""
        material = getattr(config_manager.config, "ui_material", "none")
        if material == "none":
            return

        hwnd = int(self.winId())
        apply_window_material(hwnd, material)

    def _apply_window_opacity(self):
        """根据配置设置窗口透明度。"""
        bg_opacity = getattr(config_manager.config, "background_opacity", 255)
        self.setWindowOpacity(bg_opacity / 255.0)

    def _build_stylesheet(self, material: str, bg_opacity: int) -> str:
        """根据材质类型和透明度生成 stylesheet。"""
        if material in ("mica", "mica_alt", "acrylic", "blur"):
            bg_r, bg_g, bg_b = 20, 20, 20
            bg_alpha = max(1, bg_opacity)
            central_bg = f"rgba({bg_r}, {bg_g}, {bg_b}, {bg_alpha})"
            title_border = "rgba(255, 255, 255, 20)"
            text_dark = "rgba(240, 240, 240, 230)"
            text_light = "rgba(180, 180, 180, 200)"
            input_bg = f"rgba({bg_r + 15}, {bg_g + 15}, {bg_b + 15}, {bg_alpha})"
        else:
            central_bg = f"rgba(255, 255, 255, {bg_opacity})"
            title_border = "rgba(230, 230, 230, 150)"
            text_dark = "#333333"
            text_light = "#666666"
            input_bg = f"rgba(255, 255, 255, 180)"

        return f"""
            #centralWidget {{
                background-color: {central_bg};
                border: 1px solid rgba(200, 200, 200, 150);
                border-radius: 12px;
            }}
            #titleBar {{
                background-color: transparent;
                border-bottom: 1px solid {title_border};
                margin-bottom: 5px;
            }}
            #titleLabel {{
                font-weight: bold;
                font-size: 16px;
                color: {text_dark};
                font-family: "Segoe UI", "Microsoft YaHei";
            }}
            #sectionLabel {{
                color: {text_light};
                font-size: 12px;
                font-weight: 600;
                padding-left: 2px;
            }}
            #btnTitle, #btnClose {{
                border: none;
                background: transparent;
                font-size: 20px;
                color: {text_light};
                border-radius: 4px;
            }}
            #btnClose:hover {{
                background-color: #ff4d4f;
                color: white;
            }}
            #btnTitle:hover {{
                background-color: rgba(255, 255, 255, 30);
            }}
            QTextEdit#inputText, QTextBrowser#outputText {{
                background-color: {input_bg};
                border: 1px solid rgba(220, 220, 220, 200);
                border-radius: 8px;
                padding: 10px;
                font-family: 'Segoe UI', 'Microsoft YaHei';
                font-size: 14px;
                color: {text_dark};
            }}
            QTextBrowser#outputText {{
                font-size: 13px;
                line-height: 1;
            }}
            QTextBrowser#outputText p {{
                margin-top: 6px;
                margin-bottom: 6px;
            }}
            QTextBrowser#outputText a {{
                color: #40a9ff;
                text-decoration: none;
            }}
            QTextBrowser#outputText a:hover {{
                color: #69c0ff;
                text-decoration: underline;
            }}
            QComboBox {{
                background-color: {input_bg};
                border: 1px solid rgba(220, 220, 220, 200);
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 13px;
                color: {text_dark};
            }}
            QComboBox::drop-down {{
                border: none;
            }}
            #ocrStatusLabel, #speechStatusLabel, #ragStatusLabel, #searchStatusLabel {{
                color: {text_light};
                font-size: 12px;
                padding: 3px 10px 3px 10px;
                border: 1px solid rgba(200, 200, 200, 150);
                border-radius: 4px;
                background-color: rgba(245, 245, 245, 80);
            }}
            #responseStatusLabel {{
                color: {text_light};
                font-size: 12px;
                padding: 2px 4px 2px 4px;
            }}
            QPushButton#btnAction {{
                padding: 6px 12px;
                border-radius: 6px;
                background-color: rgba(245, 245, 245, 200);
                border: 1px solid rgba(210, 210, 210, 150);
                font-size: 13px;
                color: {text_light};
            }}
            QPushButton#btnAction:hover {{
                background-color: rgba(255, 255, 255, 220);
                border-color: #1890ff;
                color: #1890ff;
            }}
            #btnSend {{
                padding: 8px 20px;
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 14px;
            }}
            #btnSend:hover {{
                background-color: #40a9ff;
            }}
            #btnSend:disabled {{
                background-color: #bfbfbf;
            }}
            #sizeGrip {{
                width: 16px;
                height: 16px;
                background-color: transparent;
                border: none;
            }}
        """

    def preview_ui(self, material: str, opacity: int):
        """实时预览材质和透明度（不持久化到配置）。"""
        hwnd = int(self.winId())

        # 预览 setWindowOpacity
        self.setWindowOpacity(opacity / 255.0)

        # 预览 DWM 材质（silent=True 不写日志，避免控制台刷屏）
        if material != "none":
            apply_window_material(hwnd, material, silent=True)

        # 预览 stylesheet
        self.setStyleSheet(self._build_stylesheet(material, opacity))

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

        # ✨ 思考动画图标（QGraphicsOpacityEffect + QPropertyAnimation）
        self.thinking_icon = QLabel("✨")
        self.thinking_icon.setObjectName("thinkingIcon")
        self.thinking_icon.setFixedWidth(20)
        self.thinking_icon.setAlignment(Qt.AlignCenter)
        self._thinking_opacity = QGraphicsOpacityEffect(self.thinking_icon)
        self._thinking_opacity.setOpacity(0.0)
        self.thinking_icon.setGraphicsEffect(self._thinking_opacity)
        self._thinking_anim = QPropertyAnimation(self._thinking_opacity, b"opacity")
        self._thinking_anim.setDuration(800)
        self._thinking_anim.setStartValue(0.0)
        self._thinking_anim.setEndValue(1.0)
        self._thinking_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._thinking_anim.setLoopCount(-1)  # 无限循环，ping-pong 由 direction 控制

        self.input_text = QTextEdit()
        self.input_text.setObjectName("inputText")
        self.input_text.setPlaceholderText("在这里输入问题，或粘贴选中的文本...")
        self.input_text.setMaximumHeight(80)

        self.output_text = QTextBrowser()
        self.output_text.setObjectName("outputText")
        self.output_text.setOpenExternalLinks(False)
        self.output_text.setPlaceholderText("AI 的回答会显示在这里...")
        self.output_text.anchorClicked.connect(self._on_output_anchor_clicked)

        btn_layout = QHBoxLayout()
        self.btn_copy = QPushButton("复制结果")
        self.btn_copy.setObjectName("btnAction")
        self.btn_send = QPushButton("发送提问")
        self.btn_send.setObjectName("btnSend")
        self.btn_send.setCursor(Qt.PointingHandCursor)

        btn_layout.addWidget(self.btn_copy)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_send)

        # 状态栏：OCR、语音、RAG、搜索 一行排列
        status_layout = QHBoxLayout()
        status_layout.addWidget(self.ocr_status_label)
        status_layout.addWidget(self.speech_status_label)
        status_layout.addWidget(self.rag_status_label)
        status_layout.addWidget(self.search_status_label)
        status_layout.addStretch()

        # 状态文字行：状态 + ✨（最右侧）
        response_layout = QHBoxLayout()
        response_layout.addWidget(self.response_status_label, stretch=1)
        response_layout.addWidget(self.thinking_icon)

        layout.addWidget(self.title_bar)
        layout.addWidget(self.model_label)
        layout.addWidget(self.combo_model)
        layout.addWidget(self.combo_template)
        layout.addLayout(status_layout)
        layout.addLayout(response_layout)
        layout.addWidget(self.input_text)
        layout.addWidget(self.output_text)
        layout.addLayout(btn_layout)

        # 添加窗口大小调整把手
        self.size_grip = QSizeGrip(self)
        self.size_grip.setObjectName("sizeGrip")
        self.size_grip.setFixedSize(16, 16)
        self.size_grip.move(self.width() - 20, self.height() - 20)
        self.size_grip.raise_()

        # 流光边框层（透明，盖在主窗口最上，鼠标穿透）
        self.border_frame = BorderFrame(self)
        self.border_frame.hide()

    def _setup_style(self):
        material = getattr(config_manager.config, "ui_material", "none")
        bg_opacity = getattr(config_manager.config, "background_opacity", 255)
        self.setStyleSheet(self._build_stylesheet(material, bg_opacity))

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
        # 保存当前滚动位置，禁用自动滚动
        scrollbar = self.output_text.verticalScrollBar()
        scroll_pos = scrollbar.value()
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.output_text.setTextCursor(cursor)
        scrollbar.setValue(scroll_pos)

    def render_markdown(self, text: str):
        """将 Markdown 渲染为高亮 HTML 并替换整个输出区。"""
        # 保存当前滚动位置，禁用自动滚动
        scrollbar = self.output_text.verticalScrollBar()
        scroll_pos = scrollbar.value()

        # 检查渲染设置
        enable_md = getattr(config_manager.config, "enable_markdown_render", True)
        enable_code = getattr(config_manager.config, "enable_code_highlight", True)

        if not enable_md:
            # 纯文本模式：仅转义显示
            escaped = html.escape(text).replace("\n", "<br>")
            html = f'<p style="margin:6px 0;color:#d0d0d0;line-height:1.7;">{escaped}</p>'
        else:
            # Markdown 渲染模式
            html = render_markdown(text, enable_code_highlight=enable_code)

        # 使用 QTextDocument.setHtml 完全替换内容，避免光标位置问题
        doc = self.output_text.document()
        doc.setHtml(html)
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.output_text.setTextCursor(cursor)
        scrollbar.setValue(scroll_pos)

    def append_output_html(self, html: str):
        # 保存当前滚动位置，禁用自动滚动
        scrollbar = self.output_text.verticalScrollBar()
        scroll_pos = scrollbar.value()
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(html)
        self.output_text.setTextCursor(cursor)
        scrollbar.setValue(scroll_pos)

    def get_output_text(self) -> str:
        return self.output_text.toPlainText()

    def set_input(self, text: str):
        self.input_text.setPlainText(text)
        self.input_text.moveCursor(QTextCursor.End)
        self.show()
        # 仅调用 show()，不调用 activateWindow()/raise_() 以避免抢夺焦点

    def append_input(self, text: str):
        cursor = self.input_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.input_text.setTextCursor(cursor)
        self.input_text.ensureCursorVisible()
        self.show()
        # 仅调用 show()，不调用 activateWindow()/raise_() 以避免抢夺焦点

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

    # ------------------------------------------------------------------ #
    #  思考动画：QGraphicsOpacityEffect + QPropertyAnimation (800ms)      #
    # ------------------------------------------------------------------ #
    def start_thinking_animation(self):
        """启动 ✨ 呼吸动画（透明度 0 → 1 → 0 循环，ping-pong）。"""
        if self._thinking_anim.state() == QPropertyAnimation.Running:
            return
        self._thinking_anim.setDirection(QPropertyAnimation.Forward)
        self._thinking_anim.start()

    def stop_thinking_animation(self):
        """立即停止 ✨ 动画，透明度归零。"""
        if self._thinking_anim.state() == QPropertyAnimation.Running:
            self._thinking_anim.stop()
        self._thinking_opacity.setOpacity(0.0)

    # ------------------------------------------------------------------ #
    #  边框流光动画：QConicalGradient + QTimer (30ms)                       #
    # ------------------------------------------------------------------ #
    def start_flow_animation(self):
        """启动边框 RGB 流光动画。"""
        self.border_frame.start_flow()

    def stop_flow_animation(self):
        """立即停止边框流光动画并隐藏。"""
        self.border_frame.stop_flow()

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
        self.size_grip.move(self.width() - 20, self.height() - 20)
        self.border_frame.setGeometry(self.rect())

    def refresh_material(self):
        """动态刷新毛玻璃材质和样式（在设置保存后由 AppController 调用）。"""
        self._apply_mica_material()
        self._apply_window_opacity()
        self._setup_style()

    def scroll_output_up(self):
        self.output_text.verticalScrollBar().setValue(
            self.output_text.verticalScrollBar().value() - self.output_text.viewport().height() // 4
        )

    def scroll_output_down(self):
        self.output_text.verticalScrollBar().setValue(
            self.output_text.verticalScrollBar().value() + self.output_text.viewport().height() // 4
        )

    def clear_input(self):
        """清空输入框内容。"""
        self.input_text.clear()

    def delete_char(self):
        """删除输入框末尾一个字符（Backspace）。"""
        cursor = self.input_text.textCursor()
        if cursor.hasSelection():
            cursor.removeSelectedText()
        else:
            cursor.deletePreviousChar()
        self.input_text.setTextCursor(cursor)

    def closeEvent(self, event):
        self.request_exit_signal.emit()
        event.accept()


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
