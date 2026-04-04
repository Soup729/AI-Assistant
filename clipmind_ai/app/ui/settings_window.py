from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, 
                             QLineEdit, QDoubleSpinBox, QSpinBox, QPushButton, 
                             QGroupBox, QCheckBox, QLabel, QMessageBox, QTabWidget, 
                             QWidget, QListWidget, QPlainTextEdit, QListWidgetItem,
                             QComboBox)
from PySide6.QtCore import Qt, Signal
from app.storage.config import config_manager, config
from app.storage.db import db_manager

class SettingsWindow(QDialog):
    config_updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ClipMind AI - 设置")
        self.resize(600, 700)
        self._init_ui()
        self._load_config()
        self._load_templates()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # 使用选项卡分类设置
        self.tabs = QTabWidget()
        
        # --- 选项卡 1: API 设置 ---
        self.api_tab = QWidget()
        api_layout = QFormLayout(self.api_tab)
        
        self.edit_api_url = QLineEdit()
        self.edit_api_url.setPlaceholderText("https://api.openai.com/v1")
        
        self.edit_api_key = QLineEdit()
        self.edit_api_key.setEchoMode(QLineEdit.Password)
        self.edit_api_key.setPlaceholderText("sk-...")
        
        self.edit_model = QLineEdit()
        self.edit_model.setPlaceholderText("gpt-3.5-turbo, qwen-max, glm-4...")
        
        # 提示信息
        self.api_tips = QLabel(
            "<b>常见厂商 Base URL 示例:</b><br/>"
            "• OpenAI: https://api.openai.com/v1<br/>"
            "• 智谱清言: https://open.bigmodel.cn/api/paas/v4<br/>"
            "• 通义千问: https://dashscope.aliyuncs.com/compatible-mode/v1<br/>"
            "• DeepSeek: https://api.deepseek.com"
        )
        self.api_tips.setStyleSheet("color: #666; font-size: 12px; margin-top: 10px;")
        self.api_tips.setWordWrap(True)
        
        self.spin_temp = QDoubleSpinBox()
        self.spin_temp.setRange(0, 2)
        self.spin_temp.setSingleStep(0.1)
        
        self.spin_tokens = QSpinBox()
        self.spin_tokens.setRange(128, 128000)
        self.spin_tokens.setSingleStep(256)
        
        api_layout.addRow("API Base URL:", self.edit_api_url)
        api_layout.addRow("API Key:", self.edit_api_key)
        api_layout.addRow("模型名称:", self.edit_model)
        api_layout.addRow("温度 (Temperature):", self.spin_temp)
        api_layout.addRow("最大 Token 数:", self.spin_tokens)
        api_layout.addRow(self.api_tips)
        
        # --- 选项卡 2: 快捷键设置 ---
        self.hotkey_tab = QWidget()
        hotkey_layout = QFormLayout(self.hotkey_tab)
        
        self.edit_hk_main = QLineEdit()
        self.edit_hk_selection = QLineEdit()
        self.edit_hk_screenshot = QLineEdit()
        
        hotkey_layout.addRow("唤起窗口:", self.edit_hk_main)
        hotkey_layout.addRow("读取选中文本:", self.edit_hk_selection)
        hotkey_layout.addRow("截图 OCR:", self.edit_hk_screenshot)
        hotkey_layout.addRow("", QLabel("<font color='gray'>注：修改后需重启程序生效</font>"))

        # --- 选项卡 3: Prompt 模板管理 ---
        self.template_tab = QWidget()
        template_layout = QHBoxLayout(self.template_tab)
        
        # 左侧列表
        list_layout = QVBoxLayout()
        self.template_list = QListWidget()
        self.template_list.currentRowChanged.connect(self._on_template_selected)
        
        btn_list_layout = QHBoxLayout()
        self.btn_add_tmpl = QPushButton("添加")
        self.btn_del_tmpl = QPushButton("删除")
        self.btn_add_tmpl.clicked.connect(self._add_template)
        self.btn_del_tmpl.clicked.connect(self._delete_template)
        
        btn_list_layout.addWidget(self.btn_add_tmpl)
        btn_list_layout.addWidget(self.btn_del_tmpl)
        list_layout.addWidget(self.template_list)
        list_layout.addLayout(btn_list_layout)
        
        # 右侧编辑
        self.edit_layout = QFormLayout()
        self.tmpl_name = QLineEdit()
        self.tmpl_sys = QPlainTextEdit()
        self.tmpl_user = QPlainTextEdit()
        self.tmpl_search = QCheckBox("启用联网搜索")
        
        self.edit_layout.addRow("模板名称:", self.tmpl_name)
        self.edit_layout.addRow("系统提示词:", self.tmpl_sys)
        self.edit_layout.addRow("用户提示词模板:", self.tmpl_user)
        self.edit_layout.addRow(self.tmpl_search)
        
        self.btn_save_tmpl = QPushButton("保存此模板")
        self.btn_save_tmpl.clicked.connect(self._save_current_template)
        self.edit_layout.addRow(self.btn_save_tmpl)
        
        template_layout.addLayout(list_layout, 1)
        template_layout.addLayout(self.edit_layout, 2)

        # --- 选项卡 4: 功能设置 ---
        self.feature_tab = QWidget()
        feature_layout = QFormLayout(self.feature_tab)
        
        self.check_search = QCheckBox("启用联网搜索 (实验性)")
        self.edit_search_key = QLineEdit()
        self.edit_search_key.setPlaceholderText("Tavily/Bing API Key")
        self.combo_ocr_mode = QComboBox()
        self.combo_ocr_mode.addItem("快速模式", "fast")
        self.combo_ocr_mode.addItem("高精度模式", "accurate")
        self.ocr_tips = QLabel("快速模式优先启动速度；高精度模式会更慢，但通常识别更稳。")
        self.ocr_tips.setWordWrap(True)
        self.ocr_tips.setStyleSheet("color: #666; font-size: 12px;")
        
        feature_layout.addRow(self.check_search)
        feature_layout.addRow("搜索 API Key:", self.edit_search_key)
        feature_layout.addRow("OCR 模式:", self.combo_ocr_mode)
        feature_layout.addRow(self.ocr_tips)
        
        # 添加选项卡
        self.tabs.addTab(self.api_tab, "大模型 API")
        self.tabs.addTab(self.hotkey_tab, "快捷键")
        self.tabs.addTab(self.template_tab, "Prompt 模板")
        self.tabs.addTab(self.feature_tab, "增强功能")
        
        layout.addWidget(self.tabs)
        
        # 底部按钮
        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("保存全局设置")
        self.btn_save.clicked.connect(self._save_config)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

    def _load_config(self):
        # 加载当前配置到界面
        self.edit_api_url.setText(config.api_base_url)
        self.edit_api_key.setText(config.api_key)
        self.edit_model.setText(config.model_name)
        self.spin_temp.setValue(config.temperature)
        self.spin_tokens.setValue(config.max_tokens)
        
        self.edit_hk_main.setText(config.hotkey_main)
        self.edit_hk_selection.setText(config.hotkey_selection)
        self.edit_hk_screenshot.setText(config.hotkey_screenshot)
        
        self.check_search.setChecked(config.enable_search)
        self.edit_search_key.setText(config.search_api_key)
        mode_index = self.combo_ocr_mode.findData(getattr(config, "ocr_mode", "fast"))
        self.combo_ocr_mode.setCurrentIndex(max(0, mode_index))

    def _load_templates(self):
        self.templates = db_manager.get_templates()
        self.template_list.clear()
        for t in self.templates:
            self.template_list.addItem(t["name"])

    def _on_template_selected(self, index):
        if index < 0 or index >= len(self.templates):
            return
        t = self.templates[index]
        self.tmpl_name.setText(t["name"])
        self.tmpl_sys.setPlainText(t["system_prompt"])
        self.tmpl_user.setPlainText(t["user_prompt_template"])
        self.tmpl_search.setChecked(bool(t["enable_search"]))

    def _add_template(self):
        new_tmpl = {
            "name": f"新模板 {self.template_list.count() + 1}",
            "system_prompt": "你是一个助手。",
            "user_prompt_template": "{text}",
            "enable_search": 0,
            "category": "User"
        }
        # 保存到数据库
        with db_manager._get_connection() as conn:
            conn.execute("""
                INSERT INTO templates (name, category, system_prompt, user_prompt_template, enable_search)
                VALUES (?, ?, ?, ?, ?)
            """, (new_tmpl["name"], new_tmpl["category"], new_tmpl["system_prompt"], 
                  new_tmpl["user_prompt_template"], new_tmpl["enable_search"]))
        self._load_templates()
        self.template_list.setCurrentRow(self.template_list.count() - 1)

    def _delete_template(self):
        idx = self.template_list.currentRow()
        if idx < 0: return
        name = self.template_list.currentItem().text()
        if QMessageBox.question(self, "确认", f"确定删除模板 '{name}' 吗？") == QMessageBox.Yes:
            with db_manager._get_connection() as conn:
                conn.execute("DELETE FROM templates WHERE name = ?", (name,))
            self._load_templates()

    def _save_current_template(self):
        idx = self.template_list.currentRow()
        if idx < 0: return
        old_name = self.templates[idx]["name"]
        new_name = self.tmpl_name.text()
        
        with db_manager._get_connection() as conn:
            conn.execute("""
                UPDATE templates SET name=?, system_prompt=?, user_prompt_template=?, enable_search=?
                WHERE name=?
            """, (new_name, self.tmpl_sys.toPlainText(), self.tmpl_user.toPlainText(),
                  1 if self.tmpl_search.isChecked() else 0, old_name))
        self._load_templates()
        QMessageBox.information(self, "成功", "模板已更新")

    def _save_config(self):
        # 保存界面内容到配置
        config_manager.update(
            api_base_url=self.edit_api_url.text(),
            api_key=self.edit_api_key.text(),
            model_name=self.edit_model.text(),
            temperature=self.spin_temp.value(),
            max_tokens=self.spin_tokens.value(),
            hotkey_main=self.edit_hk_main.text(),
            hotkey_selection=self.edit_hk_selection.text(),
            hotkey_screenshot=self.edit_hk_screenshot.text(),
            enable_search=self.check_search.isChecked(),
            search_api_key=self.edit_search_key.text(),
            ocr_mode=self.combo_ocr_mode.currentData()
        )
        self.config_updated.emit()
        QMessageBox.information(self, "成功", "设置已保存，部分设置需重启后生效。")
        self.accept()
