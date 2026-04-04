from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QFileDialog,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QLabel,
)

from app.storage.config import ModelProfile, config, config_manager
from app.storage.db import db_manager


class SettingsWindow(QDialog):
    config_updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ClipMind AI - 设置")
        self.resize(720, 760)

        self._model_profiles = []
        self._selected_model_id = ""

        self._init_ui()
        self._load_config()
        self._load_model_profiles()
        self._load_templates()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()

        self._build_model_tab()
        self._build_hotkey_tab()
        self._build_template_tab()
        self._build_feature_tab()

        self.tabs.addTab(self.model_tab, "模型管理")
        self.tabs.addTab(self.hotkey_tab, "快捷键")
        self.tabs.addTab(self.template_tab, "Prompt 模板")
        self.tabs.addTab(self.feature_tab, "增强功能")

        layout.addWidget(self.tabs)

        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("保存全局设置")
        self.btn_save.clicked.connect(self._save_config)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

    def _build_model_tab(self):
        self.model_tab = QWidget()
        layout = QVBoxLayout(self.model_tab)

        self.model_tip = QLabel(
            "这里可以保存多个模型档案。主窗口会显示所有已配置模型，并可随时切换当前启用的模型。"
        )
        self.model_tip.setWordWrap(True)
        self.model_tip.setStyleSheet("color: #666; font-size: 12px;")

        content = QHBoxLayout()

        left_panel = QVBoxLayout()
        self.model_list = QListWidget()
        self.model_list.currentRowChanged.connect(self._on_model_selected)

        model_btn_row = QHBoxLayout()
        self.btn_add_model = QPushButton("新增")
        self.btn_delete_model = QPushButton("删除")
        self.btn_add_model.clicked.connect(self._add_model)
        self.btn_delete_model.clicked.connect(self._delete_model)
        model_btn_row.addWidget(self.btn_add_model)
        model_btn_row.addWidget(self.btn_delete_model)

        left_panel.addWidget(self.model_list)
        left_panel.addLayout(model_btn_row)

        right_panel = QFormLayout()
        self.edit_model_display_name = QLineEdit()
        self.edit_api_url = QLineEdit()
        self.edit_api_url.setPlaceholderText("https://api.openai.com/v1")
        self.edit_api_key = QLineEdit()
        self.edit_api_key.setEchoMode(QLineEdit.Password)
        self.edit_api_key.setPlaceholderText("sk-...")
        self.edit_model_name = QLineEdit()
        self.edit_model_name.setPlaceholderText("gpt-4o-mini, deepseek-chat, glm-4...")
        self.spin_temp = QDoubleSpinBox()
        self.spin_temp.setRange(0, 2)
        self.spin_temp.setSingleStep(0.1)
        self.spin_tokens = QSpinBox()
        self.spin_tokens.setRange(128, 128000)
        self.spin_tokens.setSingleStep(256)

        right_panel.addRow("显示名称:", self.edit_model_display_name)
        right_panel.addRow("API Base URL:", self.edit_api_url)
        right_panel.addRow("API Key:", self.edit_api_key)
        right_panel.addRow("模型名称:", self.edit_model_name)
        right_panel.addRow("Temperature:", self.spin_temp)
        right_panel.addRow("Max Tokens:", self.spin_tokens)

        action_row = QHBoxLayout()
        self.btn_save_model = QPushButton("保存模型")
        self.btn_set_active_model = QPushButton("设为当前")
        self.btn_save_model.clicked.connect(self._save_model)
        self.btn_set_active_model.clicked.connect(self._set_active_model)
        action_row.addWidget(self.btn_save_model)
        action_row.addWidget(self.btn_set_active_model)
        right_panel.addRow(action_row)

        content.addLayout(left_panel, 1)
        content.addLayout(right_panel, 2)

        layout.addWidget(self.model_tip)
        layout.addLayout(content)

    def _build_hotkey_tab(self):
        self.hotkey_tab = QWidget()
        hotkey_layout = QFormLayout(self.hotkey_tab)

        self.edit_hk_main = QLineEdit()
        self.edit_hk_selection = QLineEdit()
        self.edit_hk_screenshot = QLineEdit()
        self.edit_hk_speech = QLineEdit()

        hotkey_layout.addRow("唤起窗口:", self.edit_hk_main)
        hotkey_layout.addRow("读取选中文本:", self.edit_hk_selection)
        hotkey_layout.addRow("截图 OCR:", self.edit_hk_screenshot)
        hotkey_layout.addRow("录音转文字:", self.edit_hk_speech)
        hotkey_layout.addRow("", QLabel("<font color='gray'>注：修改后通常无需重启即可生效。</font>"))

    def _build_template_tab(self):
        self.template_tab = QWidget()
        template_layout = QHBoxLayout(self.template_tab)

        list_layout = QVBoxLayout()
        self.template_list = QListWidget()
        self.template_list.currentRowChanged.connect(self._on_template_selected)
        tmpl_btn_row = QHBoxLayout()
        self.btn_add_tmpl = QPushButton("添加")
        self.btn_del_tmpl = QPushButton("删除")
        self.btn_add_tmpl.clicked.connect(self._add_template)
        self.btn_del_tmpl.clicked.connect(self._delete_template)
        tmpl_btn_row.addWidget(self.btn_add_tmpl)
        tmpl_btn_row.addWidget(self.btn_del_tmpl)
        list_layout.addWidget(self.template_list)
        list_layout.addLayout(tmpl_btn_row)

        edit_layout = QFormLayout()
        self.tmpl_name = QLineEdit()
        self.tmpl_sys = QPlainTextEdit()
        self.tmpl_user = QPlainTextEdit()
        self.tmpl_search = QCheckBox("启用联网搜索")
        self.btn_save_tmpl = QPushButton("保存此模板")
        self.btn_save_tmpl.clicked.connect(self._save_current_template)

        edit_layout.addRow("模板名称:", self.tmpl_name)
        edit_layout.addRow("系统提示词:", self.tmpl_sys)
        edit_layout.addRow("用户提示词模板:", self.tmpl_user)
        edit_layout.addRow(self.tmpl_search)
        edit_layout.addRow(self.btn_save_tmpl)

        template_layout.addLayout(list_layout, 1)
        template_layout.addLayout(edit_layout, 2)

    def _build_feature_tab(self):
        self.feature_tab = QWidget()
        feature_layout = QFormLayout(self.feature_tab)

        self.check_search = QCheckBox("启用联网搜索（实验性）")
        self.edit_search_key = QLineEdit()
        self.edit_search_key.setPlaceholderText("Tavily/Bing API Key")
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
        self.edit_ocr_cloud_text_path.setPlaceholderText("例如: data.txts / text / result")
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
        self.ocr_tips = QLabel("RapidOCR 已替代旧 OCR 引擎，更轻更快；混合模式会在本地结果较少时调用云端接口。")
        self.ocr_tips.setWordWrap(True)
        self.ocr_tips.setStyleSheet("color: #666; font-size: 12px;")
        self.cloud_tip = QLabel(
            "云端 OCR API 默认使用 multipart/form-data 上传 image_file。若接口返回的文本字段较特殊，可在结果路径里填写 data.text、text、result 等路径。"
        )
        self.cloud_tip.setWordWrap(True)
        self.cloud_tip.setStyleSheet("color: #666; font-size: 12px;")
        self.speech_tips = QLabel(
            "录音快捷键会同时采集系统音频和麦克风。语音模型目录中需包含 model.onnx 和 tokens.txt。"
        )
        self.speech_tips.setWordWrap(True)
        self.speech_tips.setStyleSheet("color: #666; font-size: 12px;")

        feature_layout.addRow(self.check_search)
        feature_layout.addRow("搜索 API Key:", self.edit_search_key)
        feature_layout.addRow("OCR 引擎:", self.combo_ocr_engine)
        feature_layout.addRow("云端 OCR 地址:", self.edit_ocr_cloud_url)
        feature_layout.addRow("云端 API Key:", self.edit_ocr_cloud_key)
        feature_layout.addRow("图片字段名:", self.edit_ocr_cloud_image_field)
        feature_layout.addRow("结果文本路径:", self.edit_ocr_cloud_text_path)
        feature_layout.addRow("云端超时(秒):", self.spin_ocr_cloud_timeout)
        feature_layout.addRow(self.ocr_tips)
        feature_layout.addRow(self.cloud_tip)
        feature_layout.addRow("语音模型目录:", speech_model_row)
        feature_layout.addRow(self.speech_tips)

    def _load_config(self):
        self.edit_hk_main.setText(config.hotkey_main)
        self.edit_hk_selection.setText(config.hotkey_selection)
        self.edit_hk_screenshot.setText(config.hotkey_screenshot)
        self.edit_hk_speech.setText(getattr(config, "hotkey_speech", "ctrl+alt+r"))
        self.check_search.setChecked(config.enable_search)
        self.edit_search_key.setText(config.search_api_key)
        self.edit_speech_model_dir.setText(getattr(config, "speech_model_dir", ""))
        self.edit_ocr_cloud_url.setText(getattr(config, "ocr_cloud_api_url", ""))
        self.edit_ocr_cloud_key.setText(getattr(config, "ocr_cloud_api_key", ""))
        self.edit_ocr_cloud_image_field.setText(getattr(config, "ocr_cloud_image_field", "image_file"))
        self.edit_ocr_cloud_text_path.setText(getattr(config, "ocr_cloud_text_path", ""))
        self.spin_ocr_cloud_timeout.setValue(int(getattr(config, "ocr_cloud_timeout", 30) or 30))

        engine_index = self.combo_ocr_engine.findData(getattr(config, "ocr_engine", "rapid"))
        self.combo_ocr_engine.setCurrentIndex(max(0, engine_index))

    def _load_model_profiles(self):
        self._model_profiles = config_manager.get_model_profiles()
        self._refresh_model_list(config_manager.get_active_model_profile().id if self._model_profiles else "")

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

    def _selected_model_index(self) -> int:
        return next(
            (index for index, profile in enumerate(self._model_profiles) if profile.id == self._selected_model_id),
            -1,
        )

    def _save_model_profiles(self):
        config_manager.set_model_profiles(self._model_profiles, active_model_id=config_manager.config.active_model_id)
        self._model_profiles = config_manager.get_model_profiles()
        self._refresh_model_list(self._selected_model_id)

    def _add_model(self):
        base_profile = self._current_profile_from_form() if self._selected_model_id else config_manager.get_active_model_profile()
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
        self.config_updated.emit()

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
        for index, existing in enumerate(self._model_profiles):
            if existing.id == profile.id:
                self._model_profiles[index] = profile
                replaced = True
                break
        if not replaced:
            self._model_profiles.append(profile)

        self._save_model_profiles()
        self._fill_model_form(profile)
        self.config_updated.emit()

    def _set_active_model(self):
        if not self._selected_model_id:
            return
        if config_manager.set_active_model(self._selected_model_id):
            self._model_profiles = config_manager.get_model_profiles()
            self._refresh_model_list(self._selected_model_id)
            self.config_updated.emit()

    def _delete_model(self):
        if not self._selected_model_id:
            return

        profile = next((item for item in self._model_profiles if item.id == self._selected_model_id), None)
        if profile is None:
            return

        if QMessageBox.question(self, "确认", f"确认删除模型档案 '{profile.display_name}' 吗？") != QMessageBox.Yes:
            return

        config_manager.remove_model_profile(self._selected_model_id)
        self._model_profiles = config_manager.get_model_profiles()
        self._refresh_model_list(config_manager.config.active_model_id)
        self.config_updated.emit()

    def _on_template_selected(self, index):
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
        if QMessageBox.question(self, "确认", f"确认删除模板 '{name}' 吗？") != QMessageBox.Yes:
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
        config_manager.update(
            hotkey_main=self.edit_hk_main.text().strip(),
            hotkey_selection=self.edit_hk_selection.text().strip(),
            hotkey_screenshot=self.edit_hk_screenshot.text().strip(),
            hotkey_speech=self.edit_hk_speech.text().strip(),
            enable_search=self.check_search.isChecked(),
            search_api_key=self.edit_search_key.text(),
            ocr_engine=self.combo_ocr_engine.currentData(),
            ocr_cloud_api_url=self.edit_ocr_cloud_url.text().strip(),
            ocr_cloud_api_key=self.edit_ocr_cloud_key.text().strip(),
            ocr_cloud_image_field=self.edit_ocr_cloud_image_field.text().strip(),
            ocr_cloud_text_path=self.edit_ocr_cloud_text_path.text().strip(),
            ocr_cloud_timeout=self.spin_ocr_cloud_timeout.value(),
            speech_model_dir=self.edit_speech_model_dir.text().strip(),
        )
        self.config_updated.emit()
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
