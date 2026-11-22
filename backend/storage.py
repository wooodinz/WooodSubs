import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List


def get_app_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        p = Path(base) / "WX20-WhisperX"
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "WX20-WhisperX"
    else:
        p = Path.home() / ".wx20-whisperx"
    p.mkdir(parents=True, exist_ok=True)
    (p / "subtitles").mkdir(exist_ok=True)
    (p / "downloads").mkdir(exist_ok=True)
    return p


def _default_subtitle_dir(data_dir: Path) -> str:
    classic = Path(r"C:\ishodniki\subtitles")
    if classic.is_dir():
        return str(classic)
    return str(data_dir / "subtitles")


def _load_legacy_pref(name: str, valid: tuple[str, ...], default: str) -> str:
    try:
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "WhisperX GUI" / name
        val = local.read_text(encoding="utf-8").strip()
        if val in valid:
            return val
    except Exception:
        pass
    return default


class LocalStore:
    def __init__(self) -> None:
        self.data_dir = get_app_data_dir()
        self.db_path = self.data_dir / "wx20_state.json"
        self._lock = threading.RLock()
        self.data = self._load()

    def _default_settings(self) -> Dict[str, Any]:
        return {
            "themeMode": "dark",
            "winOsOverride": "auto",
            "sourceMode": "timeline",  # 'timeline' | 'file' | 'url'
            "selectedFile": "",
            "selectedUrl": "",
            "destination": _default_subtitle_dir(self.data_dir),
            "model": _load_legacy_pref("last_model.txt", ("tiny", "base", "small", "medium", "large-v3"), "medium"),
            "language": "ru",
            "batch": int(_load_legacy_pref("last_batch_size.txt", ("1", "2", "4", "8"), "4")),
            "align": True,
            "diarize": False,
            "maxSpeakers": 4,
            "vadMethod": "pyannote",
            "vadOnset": 0.5,
            "computeType": "float16",
            "hfToken": "",
            "whisperxCmd": "whisperx",
            "ytdlpCmd": "yt-dlp",
            "lastSrt": "",
            "rackCompact": False,
            # Caption Editor remembered preferences
            "edPerLine": 3,
            "edLineCount": 2,
            "edHoldMode": "До конца речи",
            "edHoldSec": "2,0",
            "edBehindPerson": True,
            "edPreviewCenter": True,
            "edLastPath": "",
            # Orthography remembered preferences
            "orthoPunctOn": True,
            "orthoStripAll": True,
            "orthoKeepQe": False,
            "orthoKeepDots": False,
            "orthoKeepCommas": False,
            "orthoCase": "keep",
        }

    def _load(self) -> Dict[str, Any]:
        defaults = self._default_settings()
        if self.db_path.exists():
            try:
                loaded = json.loads(self.db_path.read_text(encoding="utf-8"))
                merged_settings = {**defaults, **loaded.get("settings", {})}
                return {
                    "settings": merged_settings,
                    "history": loaded.get("history", []),
                }
            except Exception:
                pass
        data = {"settings": defaults, "history": []}
        self._write(data)
        return data

    def _write(self, data: Dict[str, Any]) -> None:
        tmp = self.db_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.db_path)

    def save(self) -> None:
        with self._lock:
            self._write(self.data)

    def update_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self.data["settings"].update(patch)
            self._write(self.data)
            return dict(self.data["settings"])

    def add_history(self, item: Dict[str, Any]) -> None:
        with self._lock:
            hist = [h for h in self.data["history"] if h.get("srtPath") != item.get("srtPath")]
            hist.insert(0, item)
            self.data["history"] = hist[:40]
            self.data["settings"]["lastSrt"] = item.get("srtPath", "")
            self._write(self.data)

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "settings": dict(self.data["settings"]),
                "history": list(self.data["history"]),
                "dataDir": str(self.data_dir),
            }
