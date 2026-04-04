import json
import shutil
from pathlib import Path
from pydantic import BaseModel, Field

from app.utils.runtime_paths import get_project_root, get_user_data_dir

class AppConfig(BaseModel):
    # API 配置
    api_base_url: str = Field(default="https://api.openai.com/v1")
    api_key: str = Field(default="")
    model_name: str = Field(default="gpt-3.5-turbo")
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=2048)
    
    # 全局快捷键 (根据用户要求修改)
    hotkey_main: str = Field(default="alt+space")
    hotkey_selection: str = Field(default="alt+a")
    hotkey_screenshot: str = Field(default="alt+s")
    
    # 联网搜索
    enable_search: bool = Field(default=False)
    search_api_key: str = Field(default="")
    
    # UI 配置
    theme: str = Field(default="light")
    window_opacity: float = Field(default=0.95)
    
    # OCR 配置
    ocr_mode: str = Field(default="fast")

class ConfigManager:
    _instance = None
    _config_path = get_user_data_dir() / "config.json"
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.config = cls._instance._load_config()
        return cls._instance
    
    def _load_config(self) -> AppConfig:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._config_path.exists():
            self._migrate_legacy_config()

        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return AppConfig(**data)
            except Exception:
                return AppConfig()
        return AppConfig()

    def _migrate_legacy_config(self):
        legacy_paths = [
            get_project_root() / "config.json",
            Path.cwd() / "config.json",
        ]
        for legacy_config_path in legacy_paths:
            if legacy_config_path == self._config_path or not legacy_config_path.exists():
                continue
            try:
                shutil.copy2(legacy_config_path, self._config_path)
                return
            except OSError:
                continue
    
    def save_config(self):
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            f.write(self.config.model_dump_json(indent=4))
    
    def update(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        self.save_config()

# 单例模式
config_manager = ConfigManager()
config = config_manager.config
