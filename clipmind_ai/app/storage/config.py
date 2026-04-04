import json
import shutil
import uuid
import tempfile
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.utils.runtime_paths import get_project_root, get_user_data_dir


def _generate_model_id() -> str:
    return uuid.uuid4().hex


class ModelProfile(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(default_factory=_generate_model_id)
    display_name: str = Field(default="默认模型")
    api_base_url: str = Field(default="https://api.openai.com/v1")
    api_key: str = Field(default="")
    model_name: str = Field(default="gpt-3.5-turbo")
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=2048)


class AppConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    # Legacy single-model fields are kept for backward compatibility.
    api_base_url: str = Field(default="https://api.openai.com/v1")
    api_key: str = Field(default="")
    model_name: str = Field(default="gpt-3.5-turbo")
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=2048)

    # Multi-model support.
    model_profiles: List[ModelProfile] = Field(default_factory=list)
    active_model_id: str = Field(default="")

    # Global hotkeys.
    hotkey_main: str = Field(default="alt+space")
    hotkey_selection: str = Field(default="alt+q")
    hotkey_screenshot: str = Field(default="alt+w")
    hotkey_speech: str = Field(default="alt+e")

    # Web search.
    enable_search: bool = Field(default=False)
    search_api_key: str = Field(default="")

    # UI.
    theme: str = Field(default="light")
    window_opacity: float = Field(default=0.95)

    # OCR.
    ocr_engine: str = Field(default="rapid")
    ocr_cloud_api_url: str = Field(default="")
    ocr_cloud_api_key: str = Field(default="")
    ocr_cloud_image_field: str = Field(default="image_file")
    ocr_cloud_text_path: str = Field(default="")
    ocr_cloud_timeout: int = Field(default=30)

    # Speech recognition.
    speech_model_dir: str = Field(default="")


class ConfigManager:
    _instance = None
    _config_path = get_user_data_dir() / "config.json"

    def __new__(cls):
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._needs_save = False
            instance.config = instance._load_config()
            if instance._needs_save:
                instance.save_config()
            cls._instance = instance
        return cls._instance

    def _build_default_profile(self) -> ModelProfile:
        profile = ModelProfile()
        return profile

    def _build_profile_from_legacy(self, config: AppConfig) -> ModelProfile:
        display_name = config.model_name or "默认模型"
        return ModelProfile(
            display_name=display_name,
            api_base_url=config.api_base_url,
            api_key=config.api_key,
            model_name=config.model_name,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )

    def _normalize_ocr_engine(self, value: str) -> str:
        normalized = (value or "rapid").strip().lower()
        if normalized in {"fast", "accurate"}:
            return "rapid"
        if normalized in {"rapid", "cloud", "hybrid"}:
            return normalized
        return "rapid"

    def _migrate_ocr_config(self, data: dict) -> dict:
        migrated = dict(data)
        legacy_engine = migrated.pop("ocr_mode", None)
        if not migrated.get("ocr_engine"):
            migrated["ocr_engine"] = self._normalize_ocr_engine(str(legacy_engine or "rapid"))
        return migrated

    def _fallback_config_path(self) -> Path:
        return Path(tempfile.gettempdir()) / "ClipMindAI" / "config.json"

    def _sync_legacy_fields(self, config: AppConfig, profile: ModelProfile) -> bool:
        changed = False
        legacy_fields = {
            "api_base_url": profile.api_base_url,
            "api_key": profile.api_key,
            "model_name": profile.model_name,
            "temperature": profile.temperature,
            "max_tokens": profile.max_tokens,
        }
        for field_name, field_value in legacy_fields.items():
            if getattr(config, field_name) != field_value:
                setattr(config, field_name, field_value)
                changed = True
        return changed

    def _normalize_config(self, config: AppConfig) -> bool:
        changed = False

        if not config.model_profiles:
            default_profile = self._build_profile_from_legacy(config)
            config.model_profiles = [default_profile]
            config.active_model_id = default_profile.id
            changed = True

        active_profile = next(
            (profile for profile in config.model_profiles if profile.id == config.active_model_id),
            None,
        )
        if active_profile is None:
            active_profile = config.model_profiles[0]
            config.active_model_id = active_profile.id
            changed = True

        if self._sync_legacy_fields(config, active_profile):
            changed = True

        normalized_engine = self._normalize_ocr_engine(getattr(config, "ocr_engine", "rapid"))
        if config.ocr_engine != normalized_engine:
            config.ocr_engine = normalized_engine
            changed = True

        if not getattr(config, "ocr_cloud_image_field", "").strip():
            config.ocr_cloud_image_field = "image_file"
            changed = True

        if getattr(config, "ocr_cloud_timeout", 30) <= 0:
            config.ocr_cloud_timeout = 30
            changed = True

        return changed

    def _load_config(self) -> AppConfig:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._config_path.exists():
            self._migrate_legacy_config()

        if not self._config_path.exists():
            config = AppConfig(model_profiles=[self._build_default_profile()])
            config.active_model_id = config.model_profiles[0].id
            self._sync_legacy_fields(config, config.model_profiles[0])
            self._needs_save = True
            return config

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            config = AppConfig(model_profiles=[self._build_default_profile()])
            config.active_model_id = config.model_profiles[0].id
            self._sync_legacy_fields(config, config.model_profiles[0])
            self._needs_save = True
            return config

        if isinstance(data, dict):
            data = self._migrate_ocr_config(data)

        try:
            config = AppConfig(**data)
        except Exception:
            config = AppConfig(model_profiles=[self._build_default_profile()])
            config.active_model_id = config.model_profiles[0].id
            self._sync_legacy_fields(config, config.model_profiles[0])
            self._needs_save = True
            return config

        if self._normalize_config(config):
            self._needs_save = True

        return config

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
        active_profile = self.get_active_model_profile()
        self._sync_legacy_fields(self.config, active_profile)
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                f.write(self.config.model_dump_json(indent=4))
        except OSError:
            fallback_path = self._fallback_config_path()
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            self._config_path = fallback_path
            with open(self._config_path, "w", encoding="utf-8") as f:
                f.write(self.config.model_dump_json(indent=4))

    def update(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        if self.config.model_profiles:
            active_profile = self.get_active_model_profile()
            self._sync_legacy_fields(self.config, active_profile)
        self.save_config()

    def get_model_profiles(self) -> List[ModelProfile]:
        return list(self.config.model_profiles)

    def get_model_profile(self, model_id: str) -> Optional[ModelProfile]:
        return next((profile for profile in self.config.model_profiles if profile.id == model_id), None)

    def get_active_model_profile(self) -> ModelProfile:
        if not self.config.model_profiles:
            default_profile = self._build_default_profile()
            self.config.model_profiles = [default_profile]
            self.config.active_model_id = default_profile.id
            self.save_config()
            return default_profile

        active_profile = self.get_model_profile(self.config.active_model_id)
        if active_profile is None:
            active_profile = self.config.model_profiles[0]
            self.config.active_model_id = active_profile.id
            self.save_config()
        return active_profile

    def set_active_model(self, model_id: str) -> bool:
        profile = self.get_model_profile(model_id)
        if profile is None:
            return False

        self.config.active_model_id = profile.id
        self._sync_legacy_fields(self.config, profile)
        self.save_config()
        return True

    def set_model_profiles(self, profiles: List[ModelProfile], active_model_id: Optional[str] = None):
        cleaned_profiles = [profile if isinstance(profile, ModelProfile) else ModelProfile(**profile) for profile in profiles]
        if not cleaned_profiles:
            cleaned_profiles = [self._build_default_profile()]

        self.config.model_profiles = cleaned_profiles

        target_active_id = active_model_id or self.config.active_model_id
        if target_active_id and any(profile.id == target_active_id for profile in cleaned_profiles):
            self.config.active_model_id = target_active_id
        else:
            self.config.active_model_id = cleaned_profiles[0].id

        self.save_config()

    def add_model_profile(self, profile: Optional[ModelProfile] = None) -> ModelProfile:
        profile = profile or self._build_default_profile()
        self.config.model_profiles.append(profile)
        if not self.config.active_model_id:
            self.config.active_model_id = profile.id
        self.save_config()
        return profile

    def remove_model_profile(self, model_id: str) -> bool:
        profiles = [profile for profile in self.config.model_profiles if profile.id != model_id]
        if len(profiles) == len(self.config.model_profiles):
            return False

        self.config.model_profiles = profiles or [self._build_default_profile()]
        if self.config.active_model_id == model_id or not any(
            profile.id == self.config.active_model_id for profile in self.config.model_profiles
        ):
            self.config.active_model_id = self.config.model_profiles[0].id

        self.save_config()
        return True

    def upsert_model_profile(self, profile: ModelProfile) -> ModelProfile:
        updated_profiles = []
        replaced = False
        for existing_profile in self.config.model_profiles:
            if existing_profile.id == profile.id:
                updated_profiles.append(profile)
                replaced = True
            else:
                updated_profiles.append(existing_profile)

        if not replaced:
            updated_profiles.append(profile)

        self.set_model_profiles(updated_profiles, active_model_id=self.config.active_model_id)
        return profile


config_manager = ConfigManager()
config = config_manager.config
