# config_store.py
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional


def app_dir() -> Path:
    # PyInstaller onefile/onedir 대응: 실행파일이 있는 폴더를 기준으로 저장
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


DEFAULT_CONFIG_PATH = app_dir() / ".adb_device_selection.json"


@dataclass(frozen=True)
class SelectedDevice:
    serial: str
    model: str = "Unknown"


@dataclass(frozen=True)
class SyncConfig:
    device: SelectedDevice
    device_sync_dir: str
    local_sync_dir: str


class ConfigStore:
    """
    JSON config store.
    Stores:
      selected_device: {serial, model}
      device_sync_dir: str
      local_sync_dir: str
    """

    def __init__(self, path: Path):
        self.path = path

    @staticmethod
    def default() -> "ConfigStore":
        return ConfigStore(DEFAULT_CONFIG_PATH)

    @staticmethod
    def from_cli_path(path_str: str) -> "ConfigStore":
        return ConfigStore(Path(os.path.expanduser(path_str)))

    def load_raw(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            # corrupted -> treat as empty
            return {}

    def save_raw(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # ---- device only ----

    def get_selected_device(self) -> Optional[SelectedDevice]:
        cfg = self.load_raw()
        sd = cfg.get("selected_device")
        if not isinstance(sd, dict):
            return None

        serial = sd.get("serial")
        if not isinstance(serial, str) or not serial.strip():
            return None

        model = sd.get("model")
        if not isinstance(model, str) or not model.strip():
            model = "Unknown"

        return SelectedDevice(serial=serial.strip(), model=model.strip())

    def set_selected_device(self, device: SelectedDevice) -> None:
        cfg = self.load_raw()
        cfg["selected_device"] = asdict(device)
        self.save_raw(cfg)

    # ---- full sync config ----

    def get_sync_config(self) -> Optional[SyncConfig]:
        cfg = self.load_raw()

        device = self.get_selected_device()
        if device is None:
            return None

        ddir = cfg.get("device_sync_dir")
        ldir = cfg.get("local_sync_dir")

        if not isinstance(ddir, str) or not ddir.strip():
            return None
        if not isinstance(ldir, str) or not ldir.strip():
            return None

        return SyncConfig(
            device=device,
            device_sync_dir=ddir.strip(),
            local_sync_dir=ldir.strip(),
        )

    def is_fully_initialized(self) -> bool:
        return self.get_sync_config() is not None

    def set_sync_dirs(self, device_sync_dir: str, local_sync_dir: str) -> None:
        cfg = self.load_raw()
        cfg["device_sync_dir"] = device_sync_dir
        cfg["local_sync_dir"] = local_sync_dir
        self.save_raw(cfg)