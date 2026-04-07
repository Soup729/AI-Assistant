import keyboard

from PySide6.QtCore import Signal, Qt, QEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.storage.config import ModelProfile, config, config_manager
from app.storage.db import db_manager
from app.utils.mica import (
    is_mica_supported,
    is_mica_alt_supported,
    is_acrylic_supported,
    is_blur_supported,
)


class _HotkeyCapture(QFrame):
    """支持直接按键捕获 + 冲突检测的快捷键输入组件。"""

    hotkey_changed = Signal(str)  # 发出规范化后的快捷键字符串

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hotkey = ""          # 当前已确认的快捷键
        self._saved_hotkey = ""     # 用于冲突时回滚
        self._capturing = False
        self._modifier_keys = set()

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        self._label = QLabel("点击输入快捷键", self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color: gray; background: transparent; border: none;")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.addWidget(self._label)

        self._apply_style(False)
        self.installEventFilter(self)

    # ── 公开 API ────────────────────────────────────────────────

    def hotkey(self) -> str:
        return self._hotkey

    def set_hotkey(self, value: str):
        """外部设置初始值（不从事件触发）。"""
        self._hotkey = (value or "").strip().lower()
        self._saved_hotkey = self._hotkey
        self._label.setText(self._hotkey or "点击输入快捷键")
        self._label.setStyleSheet("color: white; background: transparent; border: none;")
        self._capturing = False
        self._apply_style(False)

    def set_revalidate_callback(self, fn):
        """传入一个 (hotkey_str) -> bool 函数，用于检测同表单内冲突。"""
        self._revalidate = fn

    # ── 内部 ────────────────────────────────────────────────────

    def _apply_style(self, capturing: bool):
        if capturing:
            self.setStyleSheet(
                "QFrame { background: #1a3a2a; border: 1.5px solid #00b4d8; border-radius: 4px; }"
            )
            self._label.setStyleSheet("color: #00b4d8; background: transparent; border: none;")
        else:
            self.setStyleSheet(
                "QFrame { background: #2a2a2a; border: 1px solid #444; border-radius: 4px; }"
                "QFrame:hover { border: 1px solid #00b4d8; }"
            )
            self._label.setStyleSheet("color: white; background: transparent; border: none;")

    def _start_capture(self):
        self._capturing = True
        self._modifier_keys = set()
        self._label.setText("按下快捷键...")
        self._apply_style(True)
        self.setFocus()

    def _finish_capture(self):
        self._capturing = False
        self._apply_style(False)

    def _normalize_key(self, key: str) -> str:
        key = key.lower().strip()
        mappings = {
            "control": "ctrl",
            "caps lock": "capslock",
            "num lock": "numlock",
            "scroll lock": "scrolllock",
            "print screen": "printscreen",
            "backspace": "backspace",
            "delete": "delete",
            "insert": "insert",
            "return": "enter",
            "esc": "escape",
            "left": "left",
            "right": "right",
            "up": "up",
            "down": "down",
            "space": "space",
        }
        return mappings.get(key, key)

    def _build_hotkey_string(self, key_str: str) -> str:
        parts = sorted(self._modifier_keys, key=lambda x: ["ctrl", "alt", "shift", "win"].index(x) if x in ["ctrl", "alt", "shift", "win"] else 99)
        main = self._normalize_key(key_str)
        if main not in ("ctrl", "alt", "shift", "win"):
            parts.append(main)
        return "+".join(parts)

    def _attempt_register(self, hotkey: str) -> bool:
        """尝试注册 hotkey，返回 True 表示成功（无冲突）。"""
        try:
            handle = keyboard.add_hotkey(hotkey, lambda: None, suppress=False)
            keyboard.remove_hotkey(handle)
            return True
        except (keyboard.exceptions.RecordingException,
                keyboard.exceptions.RegistrationException):
            return False
        except Exception:
            return False

    def _on_conflict(self, hotkey: str):
        self._hotkey = self._saved_hotkey
        self._label.setText(self._hotkey or "点击输入快捷键")
        self._finish_capture()
        QMessageBox.warning(
            self.window(),
            "快捷键冲突",
            f"快捷键「{hotkey}」已被系统或其他应用占用，请换一个组合键。",
        )

    # ── 事件 ────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if not self._capturing:
                self._start_capture()
            return True

        if event.type() == QEvent.Type.KeyPress and self._capturing:
            key_event = event
            key = key_event.key()

            # Escape 取消
            if key == Qt.Key_Escape:
                self._hotkey = self._saved_hotkey
                self._label.setText(self._hotkey or "点击输入快捷键")
                self._finish_capture()
                return True

            # 忽略单独的功能键（无主键）
            if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta,
                       Qt.Key_CapsLock, Qt.Key_NumLock, Qt.Key_ScrollLock):
                if key == Qt.Key_Control:
                    self._modifier_keys.add("ctrl")
                elif key == Qt.Key_Shift:
                    self._modifier_keys.add("shift")
                elif key == Qt.Key_Alt:
                    self._modifier_keys.add("alt")
                elif key == Qt.Key_Meta:
                    self._modifier_keys.add("win")
                return True

            # 主键转字符串
            if key == Qt.Key_Return or key == Qt.Key_Enter:
                main = "enter"
            elif key == Qt.Key_Space:
                main = "space"
            elif key == Qt.Key_Backspace:
                main = "backspace"
            elif key == Qt.Key_Tab:
                main = "tab"
            elif Qt.Key_F1 <= key <= Qt.Key_F25:
                main = f"f{key - Qt.Key_F1 + 1}"
            elif key in (Qt.Key_Left, Qt.Key_Up, Qt.Key_Right, Qt.Key_Down,
                         Qt.Key_Home, Qt.Key_End, Qt.Key_PageUp, Qt.Key_PageDown,
                         Qt.Key_Insert, Qt.Key_Delete):
                main = {Qt.Key_Left: "left", Qt.Key_Up: "up", Qt.Key_Right: "right",
                        Qt.Key_Down: "down", Qt.Key_Home: "home", Qt.Key_End: "end",
                        Qt.Key_PageUp: "page up", Qt.Key_PageDown: "page down",
                        Qt.Key_Insert: "insert", Qt.Key_Delete: "delete"}[key]
            else:
                text = key_event.text()
                if text:
                    main = text.lower()
                else:
                    self._finish_capture()
                    return True

            # 缺少修饰键 → 视为无效
            if not self._modifier_keys:
                self._finish_capture()
                return True

            hotkey_str = self._build_hotkey_string(main)

            # 检测同表单冲突（_revalidate 返回 False 表示冲突）
            if hasattr(self, "_revalidate") and not self._revalidate(hotkey_str):
                self._hotkey = self._saved_hotkey
                self._label.setText(self._hotkey or "点击输入快捷键")
                self._finish_capture()
                QMessageBox.warning(
                    self.window(),
                    "快捷键重复",
                    f"快捷键「{hotkey_str}」已被本页其他功能占用，请换一个组合键。",
                )
                return True

            # 检测系统冲突（临时注册测试）
            if not self._attempt_register(hotkey_str):
                self._on_conflict(hotkey_str)
                return True

            # 有效 → 确认
            self._hotkey = hotkey_str
            self._saved_hotkey = hotkey_str
            self._label.setText(hotkey_str)
            self._label.setStyleSheet("color: white; background: transparent; border: none;")
            self._finish_capture()
            self.hotkey_changed.emit(hotkey_str)
            return True

        return super().eventFilter(obj, event)


class SettingsWindow(QDialog):
    # scope: "model" | "template" | "hotkey" | "ocr" | "rag" | "speech" | "general"
    config_updated = Signal(str)
    # material: "none" | "mica" | "acrylic",  opacity: 1-255
    preview_updated = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ClipMind AI - 设置")
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Dialog | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating)  # 显示但不抢夺键盘焦点
        self.resize(760, 780)

        self._model_profiles = []
        self._selected_model_id = ""
        self.templates = []

        self._init_ui()
        self._load_config()
        self._load_model_profiles()
        self._load_templates()

    def _init_ui(self):
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()

        self._build_model_tab()
        self._build_hotkey_tab()
        self._build_template_tab()
        self._build_feature_tab()

        self.tabs.addTab(self.model_tab, "模型管理")
        self.tabs.addTab(self.hotkey_tab, "快捷键")
        self.tabs.addTab(self.template_tab, "Prompt 模板")
        self.tabs.addTab(self.feature_tab, "增强功能")

        root.addWidget(self.tabs)

        action_row = QHBoxLayout()
        self.btn_save = QPushButton("保存全局设置")
        self.btn_cancel = QPushButton("取消")
        self.btn_save.clicked.connect(self._save_config)
        self.btn_cancel.clicked.connect(self.reject)
        action_row.addStretch()
        action_row.addWidget(self.btn_save)
        action_row.addWidget(self.btn_cancel)
        root.addLayout(action_row)

    def _build_model_tab(self):
        self.model_tab = QWidget()
        layout = QVBoxLayout(self.model_tab)

        tip = QLabel("可配置多个模型档案，并在主窗口随时切换当前模型。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(tip)

        content = QHBoxLayout()
        layout.addLayout(content)

        left = QVBoxLayout()
        self.model_list = QListWidget()
        self.model_list.currentRowChanged.connect(self._on_model_selected)
        left.addWidget(self.model_list)

        model_btn_row = QHBoxLayout()
        self.btn_add_model = QPushButton("新增")
        self.btn_delete_model = QPushButton("删除")
        self.btn_add_model.clicked.connect(self._add_model)
        self.btn_delete_model.clicked.connect(self._delete_model)
        model_btn_row.addWidget(self.btn_add_model)
        model_btn_row.addWidget(self.btn_delete_model)
        left.addLayout(model_btn_row)

        right_form = QFormLayout()
        self.edit_model_display_name = QLineEdit()
        self.edit_api_url = QLineEdit()
        self.edit_api_url.setPlaceholderText("https://api.openai.com/v1")
        self.edit_api_key = QLineEdit()
        self.edit_api_key.setEchoMode(QLineEdit.Password)
        self.edit_api_key.setPlaceholderText("sk-...")
        self.edit_model_name = QLineEdit()
        self.edit_model_name.setPlaceholderText("gpt-4o-mini / deepseek-chat / glm-4 ...")
        self.spin_temp = QDoubleSpinBox()
        self.spin_temp.setRange(0.0, 2.0)
        self.spin_temp.setSingleStep(0.1)
        self.spin_temp.setValue(0.7)
        self.spin_tokens = QSpinBox()
        self.spin_tokens.setRange(128, 128000)
        self.spin_tokens.setSingleStep(256)
        self.spin_tokens.setValue(2048)

        right_form.addRow("显示名称:", self.edit_model_display_name)
        right_form.addRow("API Base URL:", self.edit_api_url)
        right_form.addRow("API Key:", self.edit_api_key)
        right_form.addRow("模型名称:", self.edit_model_name)
        right_form.addRow("Temperature:", self.spin_temp)
        right_form.addRow("Max Tokens:", self.spin_tokens)

        model_action_row = QHBoxLayout()
        self.btn_save_model = QPushButton("保存模型")
        self.btn_set_active_model = QPushButton("设为当前")
        self.btn_save_model.clicked.connect(self._save_model)
        self.btn_set_active_model.clicked.connect(self._set_active_model)
        model_action_row.addWidget(self.btn_save_model)
        model_action_row.addWidget(self.btn_set_active_model)
        right_form.addRow(model_action_row)

        content.addLayout(left, 1)
        content.addLayout(right_form, 2)

    def _build_hotkey_tab(self):
        self.hotkey_tab = QWidget()
        form = QFormLayout(self.hotkey_tab)

        # 所有热键捕获组件（用于同表单冲突检测）
        self._hotkey_widgets = {}

        self.edit_hk_main = _HotkeyCapture()
        self.edit_hk_selection = _HotkeyCapture()
        self.edit_hk_screenshot = _HotkeyCapture()
        self.edit_hk_speech = _HotkeyCapture()
        self.edit_hk_paste = _HotkeyCapture()
        self.edit_hk_send = _HotkeyCapture()
        self.edit_hk_scroll_up = _HotkeyCapture()
        self.edit_hk_scroll_down = _HotkeyCapture()
        self.edit_hk_clear_input = _HotkeyCapture()
        self.edit_hk_delete_char = _HotkeyCapture()

        self._hotkey_widgets = {
            "hotkey_main": self.edit_hk_main,
            "hotkey_selection": self.edit_hk_selection,
            "hotkey_screenshot": self.edit_hk_screenshot,
            "hotkey_speech": self.edit_hk_speech,
            "hotkey_paste": self.edit_hk_paste,
            "hotkey_send": self.edit_hk_send,
            "hotkey_scroll_up": self.edit_hk_scroll_up,
            "hotkey_scroll_down": self.edit_hk_scroll_down,
            "hotkey_clear_input": self.edit_hk_clear_input,
            "hotkey_delete_char": self.edit_hk_delete_char,
        }

        # 为每个组件注册同表单冲突检测
        for exclude_key, exclude_wgt in self._hotkey_widgets.items():
            exclude_wgt.set_revalidate_callback(
                lambda hk, _ek=exclude_key, _ew=exclude_wgt: all(
                    _w.hotkey() != hk for _k, _w in self._hotkey_widgets.items() if _k != _ek
                )
            )

        form.addRow("唤起窗口:", self.edit_hk_main)
        form.addRow("读取选中文本:", self.edit_hk_selection)
        form.addRow("截图 OCR:", self.edit_hk_screenshot)
        form.addRow("录音转文字:", self.edit_hk_speech)
        form.addRow("自动回填:", self.edit_hk_paste)
        form.addRow("发送提问:", self.edit_hk_send)
        form.addRow("上滑输出:", self.edit_hk_scroll_up)
        form.addRow("下滑输出:", self.edit_hk_scroll_down)
        form.addRow("清空输入:", self.edit_hk_clear_input)
        form.addRow("删除字符:", self.edit_hk_delete_char)
        form.addRow("", QLabel("<font color='gray'>修改后通常无需重启即可生效。</font>"))

    def _build_template_tab(self):
        self.template_tab = QWidget()
        layout = QHBoxLayout(self.template_tab)

        left = QVBoxLayout()
        self.template_list = QListWidget()
        self.template_list.currentRowChanged.connect(self._on_template_selected)
        left.addWidget(self.template_list)

        tmpl_btn_row = QHBoxLayout()
        self.btn_add_tmpl = QPushButton("新增")
        self.btn_del_tmpl = QPushButton("删除")
        self.btn_add_tmpl.clicked.connect(self._add_template)
        self.btn_del_tmpl.clicked.connect(self._delete_template)
        tmpl_btn_row.addWidget(self.btn_add_tmpl)
        tmpl_btn_row.addWidget(self.btn_del_tmpl)
        left.addLayout(tmpl_btn_row)

        right = QFormLayout()
        self.tmpl_name = QLineEdit()
        self.tmpl_sys = QPlainTextEdit()
        self.tmpl_user = QPlainTextEdit()
        self.tmpl_search = QCheckBox("启用联网搜索")
        self.btn_save_tmpl = QPushButton("保存模板")
        self.btn_save_tmpl.clicked.connect(self._save_current_template)

        right.addRow("模板名称:", self.tmpl_name)
        right.addRow("系统提示词:", self.tmpl_sys)
        right.addRow("用户提示词模板:", self.tmpl_user)
        right.addRow(self.tmpl_search)
        right.addRow(self.btn_save_tmpl)

        layout.addLayout(left, 1)
        layout.addLayout(right, 2)

    def _build_feature_tab(self):
        self.feature_tab = QWidget()
        form = QFormLayout(self.feature_tab)

        self.check_search = QCheckBox("启用联网搜索（实验性）")
        self.edit_search_key = QLineEdit()
        self.edit_search_key.setPlaceholderText("Tavily/Bing API Key")

        # 输出渲染设置
        self.check_markdown_render = QCheckBox("启用 Markdown 渲染")
        self.check_markdown_render.setToolTip("开启后，AI 回答将支持标题、列表、链接等 Markdown 格式")
        self.check_code_highlight = QCheckBox("启用代码高亮")
        self.check_code_highlight.setToolTip("开启后，代码块将使用语法高亮显示")
        render_tip = QLabel("关闭渲染功能后，输出将以纯文本形式显示。")
        render_tip.setStyleSheet("color: #666; font-size: 12px;")

        self.slider_bg_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_bg_opacity.setRange(1, 255)
        self.slider_bg_opacity.setValue(255)
        self.slider_bg_opacity.setToolTip("调整窗口背景透明度，配合桌面壁纸达到最佳显示效果")
        self.label_bg_opacity_value = QLabel("255")
        self.slider_bg_opacity.valueChanged.connect(
            lambda v: self.label_bg_opacity_value.setText(str(v))
        )
        self.slider_bg_opacity.valueChanged.connect(
            lambda v: self.preview_updated.emit(
                self.combo_ui_material.currentData() or "none", v
            )
        )
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.slider_bg_opacity)
        opacity_row.addWidget(self.label_bg_opacity_value)

        self.combo_ui_material = QComboBox()
        self._populate_material_combo()
        self.combo_ui_material.currentIndexChanged.connect(
            lambda _: self.preview_updated.emit(
                self.combo_ui_material.currentData() or "none",
                self.slider_bg_opacity.value(),
            )
        )

        self.combo_ocr_engine = QComboBox()
        self.combo_ocr_engine.addItem("本地 RapidOCR", "rapid")
        self.combo_ocr_engine.addItem("云端 OCR API", "cloud")
        self.combo_ocr_engine.addItem("混合增强（本地 + 云端）", "hybrid")
        self.edit_ocr_cloud_url = QLineEdit()
        self.edit_ocr_cloud_url.setPlaceholderText("https://your-ocr-api.example.com/ocr")
        self.edit_ocr_cloud_key = QLineEdit()
        self.edit_ocr_cloud_key.setEchoMode(QLineEdit.Password)
        self.edit_ocr_cloud_key.setPlaceholderText("可选")
        self.edit_ocr_cloud_image_field = QLineEdit()
        self.edit_ocr_cloud_image_field.setPlaceholderText("image_file")
        self.edit_ocr_cloud_text_path = QLineEdit()
        self.edit_ocr_cloud_text_path.setPlaceholderText("例如: data.text / text / result")
        self.spin_ocr_cloud_timeout = QSpinBox()
        self.spin_ocr_cloud_timeout.setRange(5, 120)
        self.spin_ocr_cloud_timeout.setValue(30)

        self.edit_speech_model_dir = QLineEdit()
        self.edit_speech_model_dir.setPlaceholderText("例如: clipmind_ai/assets/speech_models/xxx")
        self.btn_browse_speech_model_dir = QPushButton("选择目录")
        self.btn_browse_speech_model_dir.clicked.connect(self._browse_speech_model_dir)
        speech_model_row = QHBoxLayout()
        speech_model_row.addWidget(self.edit_speech_model_dir)
        speech_model_row.addWidget(self.btn_browse_speech_model_dir)

        self.check_enable_rag = QCheckBox("启用本地笔记 RAG（实验性）")
        self.edit_rag_notes_dir = QLineEdit()
        self.edit_rag_notes_dir.setPlaceholderText("例如: D:/Notes 或 Obsidian 仓库目录")
        self.btn_browse_rag_notes_dir = QPushButton("选择目录")
        self.btn_browse_rag_notes_dir.clicked.connect(self._browse_rag_notes_dir)
        rag_notes_row = QHBoxLayout()
        rag_notes_row.addWidget(self.edit_rag_notes_dir)
        rag_notes_row.addWidget(self.btn_browse_rag_notes_dir)
        self.edit_rag_embedding_url = QLineEdit()
        self.edit_rag_embedding_url.setPlaceholderText("https://api.openai.com/v1")
        self.edit_rag_embedding_key = QLineEdit()
        self.edit_rag_embedding_key.setEchoMode(QLineEdit.Password)
        self.edit_rag_embedding_key.setPlaceholderText("sk-...")
        self.edit_rag_embedding_model = QLineEdit()
        self.edit_rag_embedding_model.setPlaceholderText("text-embedding-3-small")

        ocr_tips = QLabel("RapidOCR 已替代旧 OCR 引擎，更轻更快；混合模式会在必要时调用云端 OCR。")
        ocr_tips.setWordWrap(True)
        ocr_tips.setStyleSheet("color: #666; font-size: 12px;")
        material_tip = QLabel("窗口材质建议在 Windows 11 下开启；若兼容性不佳可切回「关闭特效」。Acrylic 支持 Windows 10。")
        material_tip.setWordWrap(True)
        material_tip.setStyleSheet("color: #666; font-size: 12px;")
        cloud_tip = QLabel(
            "云端 OCR API 默认使用 multipart/form-data 上传 image_file；若返回字段较特殊，可在结果文本路径里填写 data.text / text / result。"
        )
        cloud_tip.setWordWrap(True)
        cloud_tip.setStyleSheet("color: #666; font-size: 12px;")
        speech_tips = QLabel("语音模型目录中需要包含 model.onnx 与 tokens.txt。")
        speech_tips.setWordWrap(True)
        speech_tips.setStyleSheet("color: #666; font-size: 12px;")
        rag_tips = QLabel(
            "启用后会在后台静默索引 Markdown 笔记：文本块写入 SQLite，向量写入本地 Numpy 索引。提问时自动进行向量 + 关键词混合检索。"
        )
        rag_tips.setWordWrap(True)
        rag_tips.setStyleSheet("color: #666; font-size: 12px;")

        form.addRow(self.check_search)
        form.addRow("搜索 API Key:", self.edit_search_key)
        form.addRow(self.check_markdown_render)
        form.addRow(self.check_code_highlight)
        form.addRow(render_tip)
        form.addRow("窗口材质:", self.combo_ui_material)
        form.addRow("背景透明度:", opacity_row)
        form.addRow(material_tip)
        form.addRow("OCR 引擎:", self.combo_ocr_engine)
        form.addRow("云端 OCR 地址:", self.edit_ocr_cloud_url)
        form.addRow("云端 API Key:", self.edit_ocr_cloud_key)
        form.addRow("图片字段名:", self.edit_ocr_cloud_image_field)
        form.addRow("结果文本路径:", self.edit_ocr_cloud_text_path)
        form.addRow("云端超时(秒):", self.spin_ocr_cloud_timeout)
        form.addRow(ocr_tips)
        form.addRow(cloud_tip)
        form.addRow("语音模型目录:", speech_model_row)
        form.addRow(speech_tips)
        form.addRow(self.check_enable_rag)
        form.addRow("Notes 目录:", rag_notes_row)
        form.addRow("Embedding API URL:", self.edit_rag_embedding_url)
        form.addRow("Embedding API Key:", self.edit_rag_embedding_key)
        form.addRow("Embedding 模型:", self.edit_rag_embedding_model)
        form.addRow(rag_tips)

    def _populate_material_combo(self):
        """填充窗口材质下拉框，根据系统支持情况动态启用选项。"""
        self.combo_ui_material.blockSignals(True)
        self.combo_ui_material.clear()
        self.combo_ui_material.addItem("关闭特效（兼容模式）", "none")

        blur_supported = is_blur_supported()
        if blur_supported:
            self.combo_ui_material.addItem("纯净毛玻璃（Blur）", "blur")
        if is_acrylic_supported():
            self.combo_ui_material.addItem("亚克力（Acrylic）", "acrylic")
        if is_mica_alt_supported():
            self.combo_ui_material.addItem("深度云母（Mica Alt）", "mica_alt")
        if is_mica_supported():
            self.combo_ui_material.addItem("云母（Mica）", "mica")

        if not (blur_supported or is_acrylic_supported() or is_mica_supported()):
            self.combo_ui_material.setEnabled(False)
            self.slider_bg_opacity.setEnabled(False)
        else:
            self.combo_ui_material.setEnabled(True)
            self.slider_bg_opacity.setEnabled(True)

        self.combo_ui_material.blockSignals(False)

    def _load_config(self):
        self.edit_hk_main.set_hotkey(config.hotkey_main)
        self.edit_hk_selection.set_hotkey(config.hotkey_selection)
        self.edit_hk_screenshot.set_hotkey(config.hotkey_screenshot)
        self.edit_hk_speech.set_hotkey(getattr(config, "hotkey_speech", "alt+e"))
        self.edit_hk_paste.set_hotkey(getattr(config, "hotkey_paste", "alt+h"))
        self.edit_hk_send.set_hotkey(getattr(config, "hotkey_send", "alt+k"))
        self.edit_hk_scroll_up.set_hotkey(getattr(config, "hotkey_scroll_up", "alt+up"))
        self.edit_hk_scroll_down.set_hotkey(getattr(config, "hotkey_scroll_down", "alt+down"))
        self.edit_hk_clear_input.set_hotkey(getattr(config, "hotkey_clear_input", "alt+del"))
        self.edit_hk_delete_char.set_hotkey(getattr(config, "hotkey_delete_char", "alt+backspace"))

        self.check_search.setChecked(config.enable_search)
        self.edit_search_key.setText(config.search_api_key)
        self.check_markdown_render.setChecked(getattr(config, "enable_markdown_render", True))
        self.check_code_highlight.setChecked(getattr(config, "enable_code_highlight", True))
        material_index = self.combo_ui_material.findData(getattr(config, "ui_material", "none"))
        self.combo_ui_material.setCurrentIndex(max(0, material_index))
        self.slider_bg_opacity.setValue(int(getattr(config, "background_opacity", 255)))
        self.label_bg_opacity_value.setText(str(getattr(config, "background_opacity", 255)))

        self.edit_ocr_cloud_url.setText(getattr(config, "ocr_cloud_api_url", ""))
        self.edit_ocr_cloud_key.setText(getattr(config, "ocr_cloud_api_key", ""))
        self.edit_ocr_cloud_image_field.setText(getattr(config, "ocr_cloud_image_field", "image_file"))
        self.edit_ocr_cloud_text_path.setText(getattr(config, "ocr_cloud_text_path", ""))
        self.spin_ocr_cloud_timeout.setValue(int(getattr(config, "ocr_cloud_timeout", 30) or 30))
        engine_index = self.combo_ocr_engine.findData(getattr(config, "ocr_engine", "rapid"))
        self.combo_ocr_engine.setCurrentIndex(max(0, engine_index))

        self.edit_speech_model_dir.setText(getattr(config, "speech_model_dir", ""))

        self.check_enable_rag.setChecked(bool(getattr(config, "enable_rag", False)))
        self.edit_rag_notes_dir.setText(getattr(config, "rag_notes_dir", ""))
        self.edit_rag_embedding_url.setText(getattr(config, "rag_embedding_api_url", "https://api.openai.com/v1"))
        self.edit_rag_embedding_key.setText(getattr(config, "rag_embedding_api_key", ""))
        self.edit_rag_embedding_model.setText(getattr(config, "rag_embedding_model", "text-embedding-3-small"))

    def _load_model_profiles(self):
        self._model_profiles = config_manager.get_model_profiles()
        active_profile = config_manager.get_active_model_profile()
        self._refresh_model_list(active_profile.id if active_profile else "")

    def _refresh_model_list(self, select_id: str = ""):
        self.model_list.blockSignals(True)
        self.model_list.clear()
        active_id = config_manager.config.active_model_id
        for profile in self._model_profiles:
            prefix = "★ " if profile.id == active_id else ""
            label = profile.display_name or profile.model_name or "未命名模型"
            self.model_list.addItem(f"{prefix}{label}")

        if not self._model_profiles:
            self._clear_model_form()
            self.model_list.blockSignals(False)
            return

        target_row = 0
        if select_id:
            for index, profile in enumerate(self._model_profiles):
                if profile.id == select_id:
                    target_row = index
                    break
        self.model_list.setCurrentRow(target_row)
        self.model_list.blockSignals(False)
        self._on_model_selected(self.model_list.currentRow())

    def _clear_model_form(self):
        self._selected_model_id = ""
        self.edit_model_display_name.clear()
        self.edit_api_url.clear()
        self.edit_api_key.clear()
        self.edit_model_name.clear()
        self.spin_temp.setValue(0.7)
        self.spin_tokens.setValue(2048)

    def _load_templates(self):
        self.templates = db_manager.get_templates()
        self.template_list.clear()
        for template in self.templates:
            self.template_list.addItem(template["name"])

    def _current_profile_from_form(self, profile_id: str = "") -> ModelProfile:
        return ModelProfile(
            id=profile_id or self._selected_model_id or ModelProfile().id,
            display_name=self.edit_model_display_name.text().strip() or "默认模型",
            api_base_url=self.edit_api_url.text().strip() or "https://api.openai.com/v1",
            api_key=self.edit_api_key.text(),
            model_name=self.edit_model_name.text().strip() or "gpt-3.5-turbo",
            temperature=self.spin_temp.value(),
            max_tokens=self.spin_tokens.value(),
        )

    def _fill_model_form(self, profile: ModelProfile):
        self._selected_model_id = profile.id
        self.edit_model_display_name.setText(profile.display_name)
        self.edit_api_url.setText(profile.api_base_url)
        self.edit_api_key.setText(profile.api_key)
        self.edit_model_name.setText(profile.model_name)
        self.spin_temp.setValue(profile.temperature)
        self.spin_tokens.setValue(profile.max_tokens)

    def _on_model_selected(self, index: int):
        if index < 0 or index >= len(self._model_profiles):
            self._clear_model_form()
            return
        self._fill_model_form(self._model_profiles[index])

    def _save_model_profiles(self):
        config_manager.set_model_profiles(self._model_profiles, active_model_id=config_manager.config.active_model_id)
        self._model_profiles = config_manager.get_model_profiles()
        self._refresh_model_list(self._selected_model_id)

    def _add_model(self):
        base_profile = (
            self._current_profile_from_form()
            if self._selected_model_id
            else config_manager.get_active_model_profile()
        )
        base_name = base_profile.display_name.strip() or "新模型"
        new_profile = ModelProfile(
            display_name=f"{base_name} 副本",
            api_base_url=base_profile.api_base_url,
            api_key=base_profile.api_key,
            model_name=base_profile.model_name,
            temperature=base_profile.temperature,
            max_tokens=base_profile.max_tokens,
        )
        self._model_profiles.append(new_profile)
        self._save_model_profiles()
        self._fill_model_form(new_profile)
        self.config_updated.emit("model")

    def _save_model(self):
        if not self._model_profiles:
            new_profile = self._current_profile_from_form()
            self._model_profiles = [new_profile]
            self._save_model_profiles()
            self._fill_model_form(new_profile)
            self.config_updated.emit()
            return

        profile = self._current_profile_from_form(self._selected_model_id)
        replaced = False
        for idx, existing in enumerate(self._model_profiles):
            if existing.id == profile.id:
                self._model_profiles[idx] = profile
                replaced = True
                break
        if not replaced:
            self._model_profiles.append(profile)

        self._save_model_profiles()
        self._fill_model_form(profile)
        self.config_updated.emit("model")

    def _set_active_model(self):
        if not self._selected_model_id:
            return
        if config_manager.set_active_model(self._selected_model_id):
            self._model_profiles = config_manager.get_model_profiles()
            self._refresh_model_list(self._selected_model_id)
            self.config_updated.emit("model")

    def _delete_model(self):
        if not self._selected_model_id:
            return

        profile = next((item for item in self._model_profiles if item.id == self._selected_model_id), None)
        if profile is None:
            return

        answer = QMessageBox.question(self, "确认", f"确认删除模型档案「{profile.display_name}」吗？")
        if answer != QMessageBox.Yes:
            return

        config_manager.remove_model_profile(self._selected_model_id)
        self._model_profiles = config_manager.get_model_profiles()
        self._refresh_model_list(config_manager.config.active_model_id)
        self.config_updated.emit("model")

    def _on_template_selected(self, index: int):
        if index < 0 or index >= len(self.templates):
            return
        template = self.templates[index]
        self.tmpl_name.setText(template["name"])
        self.tmpl_sys.setPlainText(template["system_prompt"])
        self.tmpl_user.setPlainText(template["user_prompt_template"])
        self.tmpl_search.setChecked(bool(template["enable_search"]))

    def _add_template(self):
        new_template = {
            "name": f"新模板 {self.template_list.count() + 1}",
            "system_prompt": "你是一个助手。",
            "user_prompt_template": "{text}",
            "enable_search": 0,
            "category": "User",
        }
        with db_manager._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO templates (name, category, system_prompt, user_prompt_template, enable_search)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    new_template["name"],
                    new_template["category"],
                    new_template["system_prompt"],
                    new_template["user_prompt_template"],
                    new_template["enable_search"],
                ),
            )
        self._load_templates()
        self.template_list.setCurrentRow(self.template_list.count() - 1)

    def _delete_template(self):
        index = self.template_list.currentRow()
        if index < 0:
            return
        name = self.template_list.currentItem().text()
        answer = QMessageBox.question(self, "确认", f"确认删除模板「{name}」吗？")
        if answer != QMessageBox.Yes:
            return
        with db_manager._get_connection() as conn:
            conn.execute("DELETE FROM templates WHERE name = ?", (name,))
        self._load_templates()

    def _save_current_template(self):
        index = self.template_list.currentRow()
        if index < 0:
            return
        old_name = self.templates[index]["name"]
        new_name = self.tmpl_name.text().strip()
        if not new_name:
            QMessageBox.warning(self, "提示", "模板名称不能为空。")
            return

        with db_manager._get_connection() as conn:
            conn.execute(
                """
                UPDATE templates
                SET name=?, system_prompt=?, user_prompt_template=?, enable_search=?
                WHERE name=?
                """,
                (
                    new_name,
                    self.tmpl_sys.toPlainText(),
                    self.tmpl_user.toPlainText(),
                    1 if self.tmpl_search.isChecked() else 0,
                    old_name,
                ),
            )
        self._load_templates()
        QMessageBox.information(self, "成功", "模板已更新。")

    def _save_config(self):
        old_config = config_manager.config

        # 收集新值
        new_values = {
            "hotkey_main": self.edit_hk_main.hotkey(),
            "hotkey_selection": self.edit_hk_selection.hotkey(),
            "hotkey_screenshot": self.edit_hk_screenshot.hotkey(),
            "hotkey_speech": self.edit_hk_speech.hotkey(),
            "hotkey_paste": self.edit_hk_paste.hotkey(),
            "hotkey_send": self.edit_hk_send.hotkey(),
            "hotkey_scroll_up": self.edit_hk_scroll_up.hotkey(),
            "hotkey_scroll_down": self.edit_hk_scroll_down.hotkey(),
            "hotkey_clear_input": self.edit_hk_clear_input.hotkey(),
            "hotkey_delete_char": self.edit_hk_delete_char.hotkey(),
            "enable_search": self.check_search.isChecked(),
            "search_api_key": self.edit_search_key.text().strip(),
            "enable_markdown_render": self.check_markdown_render.isChecked(),
            "enable_code_highlight": self.check_code_highlight.isChecked(),
            "ui_material": self.combo_ui_material.currentData(),
            "background_opacity": self.slider_bg_opacity.value(),
            "ocr_engine": self.combo_ocr_engine.currentData(),
            "ocr_cloud_api_url": self.edit_ocr_cloud_url.text().strip(),
            "ocr_cloud_api_key": self.edit_ocr_cloud_key.text().strip(),
            "ocr_cloud_image_field": self.edit_ocr_cloud_image_field.text().strip(),
            "ocr_cloud_text_path": self.edit_ocr_cloud_text_path.text().strip(),
            "ocr_cloud_timeout": self.spin_ocr_cloud_timeout.value(),
            "speech_model_dir": self.edit_speech_model_dir.text().strip(),
            "enable_rag": self.check_enable_rag.isChecked(),
            "rag_notes_dir": self.edit_rag_notes_dir.text().strip(),
            "rag_embedding_api_url": self.edit_rag_embedding_url.text().strip(),
            "rag_embedding_api_key": self.edit_rag_embedding_key.text().strip(),
            "rag_embedding_model": self.edit_rag_embedding_model.text().strip(),
        }

        # 检测变更的scope
        changed_scopes = set()
        old_vals = old_config.model_dump()

        # 热键变更
        if any(old_vals.get(k) != new_values[k] for k in ("hotkey_main", "hotkey_selection", "hotkey_screenshot", "hotkey_speech", "hotkey_paste", "hotkey_send", "hotkey_scroll_up", "hotkey_scroll_down", "hotkey_clear_input", "hotkey_delete_char")):
            changed_scopes.add("hotkey")
        # 搜索设置变更
        if old_vals.get("enable_search") != new_values["enable_search"] or old_vals.get("search_api_key") != new_values["search_api_key"]:
            changed_scopes.add("search")
        # OCR变更
        if any(old_vals.get(k) != new_values[k] for k in ("ocr_engine", "ocr_cloud_api_url", "ocr_cloud_api_key", "ocr_cloud_image_field", "ocr_cloud_text_path", "ocr_cloud_timeout")):
            changed_scopes.add("ocr")
        # 语音模型变更
        if old_vals.get("speech_model_dir") != new_values["speech_model_dir"]:
            changed_scopes.add("speech")
        # RAG变更
        if any(old_vals.get(k) != new_values[k] for k in ("enable_rag", "rag_notes_dir", "rag_embedding_api_url", "rag_embedding_api_key", "rag_embedding_model")):
            changed_scopes.add("rag")
        # UI材质/透明度变更
        if old_vals.get("ui_material") != new_values["ui_material"] or old_vals.get("background_opacity") != new_values["background_opacity"]:
            changed_scopes.add("ui")

        # 如果没有任何明确识别的变更，但有值不同，仍触发general
        if not changed_scopes:
            # 检查是否有其他未列出的字段变更
            for k, v in new_values.items():
                if old_vals.get(k) != v:
                    changed_scopes.add("general")
                    break

        config_manager.update(**new_values)

        if changed_scopes:
            self.config_updated.emit(next(iter(changed_scopes)) if len(changed_scopes) == 1 else "general")
        QMessageBox.information(self, "成功", "设置已保存。")
        self.accept()

    def _browse_speech_model_dir(self):
        selected_dir = QFileDialog.getExistingDirectory(
            self,
            "选择语音模型目录",
            self.edit_speech_model_dir.text().strip() or "",
        )
        if selected_dir:
            self.edit_speech_model_dir.setText(selected_dir)

    def _browse_rag_notes_dir(self):
        selected_dir = QFileDialog.getExistingDirectory(
            self,
            "选择本地 Notes 目录",
            self.edit_rag_notes_dir.text().strip() or "",
        )
        if selected_dir:
            self.edit_rag_notes_dir.setText(selected_dir)
