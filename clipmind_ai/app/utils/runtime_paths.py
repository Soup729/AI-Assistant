import os
import sys
from pathlib import Path


APP_NAME = "ClipMindAI"


def get_project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def get_user_data_dir() -> Path:
    override_dir = os.environ.get("CLIPMINDAI_DATA_DIR")
    if override_dir:
        return Path(override_dir)

    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / APP_NAME
        return Path.home() / "AppData" / "Local" / APP_NAME

    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home) / APP_NAME

    return Path.home() / ".local" / "state" / APP_NAME
