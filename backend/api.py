import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import webview

from backend.editor_service import CaptionEditorService
from backend.engine import WhisperXEngine
from backend.storage import LocalStore


class DesktopAPI:
    def __init__(self, store: LocalStore, engine: WhisperXEngine) -> None:
        self.store = store
        self.engine = engine
        self.editor = CaptionEditorService()
        self._window: Optional[webview.Window] = None

    def attach_window(self, window: webview.Window) -> None:
        self._window = window

    def get_bootstrap(self) -> Dict[str, Any]:
        snap = self.store.get_snapshot()
        snap["env"] = self.engine.env
        snap["live"] = self.engine.get_live_status()
        return snap

    def get_live_status(self) -> Dict[str, Any]:
        return self.engine.get_live_status()

    def save_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        updated = self.store.update_settings(patch)
        env = self.engine.refresh_env()
        return {"ok": True, "settings": updated, "env": env}

    def refresh_timeline(self) -> Dict[str, Any]:
        return self.engine.refresh_timeline()

    def refresh_env(self) -> Dict[str, Any]:
        return self.engine.refresh_env()

    def start_job(self, opts: Dict[str, Any]) -> Dict[str, Any]:
        return self.engine.start_job(opts)

    def stop_job(self) -> Dict[str, Any]:
        return self.engine.stop_job()

    def pick_media_file(self) -> str:
        if not self._window:
            return ""
        res = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=(
                "Видео и аудио (*.mp4;*.mov;*.mkv;*.mp3;*.wav;*.m4a;*.flac;*.ogg;*.webm)",
                "Все файлы (*.*)",
            ),
        )
        return str(res[0]) if res else ""

    def pick_destination_folder(self) -> str:
        if not self._window:
            return ""
        cur = self.store.data["settings"].get("destination", "")
        kwargs: Dict[str, Any] = {}
        if cur and Path(cur).is_dir():
            kwargs["directory"] = cur
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER, **kwargs)
        return str(res[0]) if res else ""

    def pick_executable(self) -> str:
        if not self._window:
            return ""
        res = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=("Executable (*.exe)", "All files (*.*)"),
        )
        return str(res[0]) if res else ""

    def pick_existing_srt(self, open_in_editor: bool = False) -> Dict[str, Any]:
        if not self._window:
            return {"ok": False}
        cur = self.store.data["settings"].get("destination", "")
        kwargs: Dict[str, Any] = {
            "allow_multiple": False,
            "file_types": ("Субтитры SubRip (*.srt)", "Все файлы (*.*)"),
        }
        if cur and Path(cur).is_dir():
            kwargs["directory"] = cur
        res = self._window.create_file_dialog(webview.FileDialog.OPEN, **kwargs)
        if not res:
            return {"ok": False, "cancelled": True}
        srt_path = str(res[0])
        parsed = self.engine.load_srt(srt_path)
        if open_in_editor:
            self.engine.launch_caption_editor(srt_path)
        return {"ok": True, "subtitle": parsed}

    def load_history_item(self, srt_path: str, audio_path: str = "") -> Dict[str, Any]:
        if not Path(srt_path).is_file():
            return {"ok": False, "error": "Файл SRT больше не существует на диске."}
        return {"ok": True, "subtitle": self.engine.load_srt(srt_path, audio_path)}

    def open_caption_editor(self, srt_path: str = "") -> Dict[str, Any]:
        return self.engine.launch_caption_editor(srt_path)

    def open_destination_folder(self) -> Dict[str, Any]:
        folder = self.store.data["settings"].get("destination", "")
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "Папка для субтитров не существует."}
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
        return {"ok": True}

    def save_text_file(self, content: str, default_name: str, ext: str) -> Dict[str, Any]:
        if not self._window:
            return {"ok": False}
        clean = "".join(c for c in default_name if c not in '<>:"/\\|?*').strip() or "subtitles"
        if not clean.lower().endswith(f".{ext}"):
            clean = f"{clean}.{ext}"
        res = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=clean,
            file_types=(f"{ext.upper()} (*.{ext})", "Все файлы (*.*)"),
        )
        if not res:
            return {"ok": False, "cancelled": True}
        out = res[0] if isinstance(res, (list, tuple)) else str(res)
        Path(out).write_text(content, encoding="utf-8")
        return {"ok": True, "path": out}

    # ---- Integrated RC-20 Caption Editor API ----
    def ed_open(self, path: str = "") -> Dict[str, Any]:
        target = (
            path
            or self.store.data["settings"].get("edLastPath", "")
            or self.store.data["settings"].get("lastSrt", "")
        )
        if target and Path(target).is_file():
            res = self.editor.load_file(target)
        else:
            res = self.editor.snapshot("Откройте SRT или добавьте реплику.")
        if not self.editor.fonts:
            self.editor.refresh_fonts()
            if res.get("ok"):
                res["fonts"] = self.editor.fonts
        return res

    def ed_pick_and_load(self) -> Dict[str, Any]:
        if not self._window:
            return {"ok": False}
        cur = self.store.data["settings"].get("destination", "")
        kwargs: Dict[str, Any] = {
            "allow_multiple": False,
            "file_types": ("Субтитры и проекты WhisperX (*.srt;*.json)", "Все файлы (*.*)"),
        }
        if cur and Path(cur).is_dir():
            kwargs["directory"] = cur
        res = self._window.create_file_dialog(webview.FileDialog.OPEN, **kwargs)
        if not res:
            return {"ok": False, "cancelled": True}
        loaded = self.editor.load_file(str(res[0]))
        if loaded.get("ok"):
            self.store.update_settings({"edLastPath": str(res[0])})
        return loaded

    def ed_refresh_fonts(self) -> Dict[str, Any]:
        return self.editor.refresh_fonts()

    def ed_update_row(self, index: int, row_data: Dict[str, Any]) -> Dict[str, Any]:
        return self.editor.update_row(int(index), row_data)

    def ed_add_row(self, after_index: Optional[int]) -> Dict[str, Any]:
        return self.editor.add_row(int(after_index) if after_index is not None and after_index >= 0 else None)

    def ed_split_row(self, index: int, cursor_offset: int) -> Dict[str, Any]:
        return self.editor.split_row(int(index), int(cursor_offset))

    def ed_merge_next(self, index: int) -> Dict[str, Any]:
        return self.editor.merge_next(int(index))

    def ed_delete_row(self, index: int) -> Dict[str, Any]:
        return self.editor.delete_row(int(index))

    def ed_apply_style_all(self, index: int) -> Dict[str, Any]:
        return self.editor.apply_style_all(int(index))

    def ed_clear_reference_style(self, index: int) -> Dict[str, Any]:
        return self.editor.clear_reference_style(int(index))

    def ed_import_resolve_style(self, index: int) -> Dict[str, Any]:
        return self.editor.import_resolve_style(int(index))

    def ed_import_style_json(self, index: int) -> Dict[str, Any]:
        if not self._window:
            return {"ok": False}
        kwargs: Dict[str, Any] = {
            "allow_multiple": False,
            "file_types": ("Пресет оформления JSON (*.json)", "Все файлы (*.*)"),
        }
        style_dir = Path(r"C:\ishodniki\text_style")
        if style_dir.is_dir():
            kwargs["directory"] = str(style_dir)
        res = self._window.create_file_dialog(webview.FileDialog.OPEN, **kwargs)
        if not res:
            return {"ok": False, "cancelled": True}
        return self.editor.import_style_json(int(index), str(res[0]))

    def ed_save_style_json(self, index: int) -> Dict[str, Any]:
        if not self._window:
            return {"ok": False}
        res = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename="Text+.whisperx-style.json",
            file_types=("Пресет оформления WhisperX (*.whisperx-style.json)", "JSON (*.json)"),
        )
        if not res:
            return {"ok": False, "cancelled": True}
        out = res[0] if isinstance(res, (list, tuple)) else str(res)
        return self.editor.save_style_json(int(index), out)

    def ed_apply_layout(self, per_line: int, line_count: int) -> Dict[str, Any]:
        return self.editor.apply_layout(int(per_line), int(line_count))

    def ed_undo_layout(self) -> Dict[str, Any]:
        return self.editor.undo_layout()

    def ed_apply_word_timing(
        self, cumulative: bool, per_line: int, line_count: int, hold_mode: str, manual_sec: str, active_index: int
    ) -> Dict[str, Any]:
        res = self.editor.apply_word_timing(bool(cumulative), int(per_line), int(line_count), hold_mode, str(manual_sec), int(active_index))
        if res.get("needWordsFile") and self._window:
            picked = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=("JSON WhisperX (*.json)", "Все файлы (*.*)"),
            )
            if not picked:
                return {"ok": False, "cancelled": True}
            return self.editor.apply_word_timing(
                bool(cumulative), int(per_line), int(line_count), hold_mode, str(manual_sec), int(active_index),
                override_words_path=str(picked[0]),
            )
        return res

    def ed_undo_words(self) -> Dict[str, Any]:
        return self.editor.undo_words()

    def ed_apply_hold(self, hold_mode: str, manual_sec: str, selected_only: bool, active_index: int) -> Dict[str, Any]:
        return self.editor.apply_hold(hold_mode, str(manual_sec), bool(selected_only), int(active_index))

    def ed_apply_orthography(
        self,
        punct_mode: bool,
        keep_qe: bool,
        keep_dots: bool,
        keep_commas: bool,
        case_mode: str,
        selected_only: bool,
        active_index: int,
    ) -> Dict[str, Any]:
        return self.editor.apply_orthography(
            bool(punct_mode), bool(keep_qe), bool(keep_dots), bool(keep_commas),
            str(case_mode), bool(selected_only), int(active_index),
        )

    def ed_undo_orthography(self) -> Dict[str, Any]:
        return self.editor.undo_orthography()

    def ed_save_srt(self) -> Dict[str, Any]:
        if not self._window:
            return {"ok": False}
        cur_dir = self.store.data["settings"].get("destination", "")
        default_name = "subtitles.srt"
        if self.editor.path:
            stem = Path(self.editor.path).name.removesuffix(".whisperx.json").removesuffix(".srt")
            default_name = stem + ".srt"
        kwargs: Dict[str, Any] = {
            "save_filename": default_name,
            "file_types": ("Субтитры SubRip (*.srt)", "Все файлы (*.*)"),
        }
        if cur_dir and Path(cur_dir).is_dir():
            kwargs["directory"] = cur_dir
        res = self._window.create_file_dialog(webview.FileDialog.SAVE, **kwargs)
        if not res:
            return {"ok": False, "cancelled": True}
        out = res[0] if isinstance(res, (list, tuple)) else str(res)
        saved = self.editor.save_srt_file(out)
        if saved.get("ok"):
            self.store.update_settings({"edLastPath": out, "lastSrt": out})
        return saved

    def ed_save_project(self) -> Dict[str, Any]:
        if not self._window:
            return {"ok": False}
        res = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename="captions.whisperx.json",
            file_types=("Проект WhisperX (*.whisperx.json)", "JSON (*.json)"),
        )
        if not res:
            return {"ok": False, "cancelled": True}
        out = res[0] if isinstance(res, (list, tuple)) else str(res)
        saved = self.editor.save_project_file(out)
        if saved.get("ok"):
            self.store.update_settings({"edLastPath": out})
        return saved

    def ed_start_export_resolve(self, behind_person: bool) -> Dict[str, Any]:
        return self.editor.start_export_resolve(bool(behind_person))

    def ed_start_auto_place_sides(self, behind_person: bool) -> Dict[str, Any]:
        return self.editor.start_auto_place_sides(bool(behind_person))

    def ed_get_busy_state(self) -> Dict[str, Any]:
        return self.editor.get_busy_state()
