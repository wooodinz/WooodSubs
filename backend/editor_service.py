"""Headless Caption Editor service reusing 100% of original caption_editor & reference_caption_style logic."""
import json
import sys
import threading
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.resolve_bridge import connect_resolve, resolve_app

_TOOLS_DIR = str(Path(__file__).resolve().parent / "resolve_tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import caption_editor as ce
from reference_caption_style import (
    PRESET_ID,
    SUPPORTED_PRESET_IDS,
    parse_reference_preset,
    prepare_subject_mattes,
    resolve_caption_placements,
)


def _row_to_dict(row: ce.Caption) -> Dict[str, Any]:
    d = asdict(row)
    d["startText"] = ce.time_text(row.start)
    d["endText"] = ce.time_text(row.end)
    d["presetLabel"] = ce.caption_preset_label(row)
    d["reveal"] = list(row.reveal)
    return d


def _dict_to_row(d: Dict[str, Any]) -> ce.Caption:
    start = ce.parse_time(d["startText"]) if "startText" in d and isinstance(d["startText"], str) else int(d["start"])
    end = ce.parse_time(d["endText"]) if "endText" in d and isinstance(d["endText"], str) else int(d["end"])
    side = str(d.get("side", "auto")).strip().lower()
    if side not in ce.SIDE_MODES:
        side = ce.LABEL_TO_SIDE.get(str(d.get("side", "")).strip(), "auto")
    x = float(str(d.get("x", 0.5)).replace(",", "."))
    y = float(str(d.get("y", 0.22)).replace(",", "."))
    size = float(str(d.get("size", 0.07)).replace(",", "."))
    frames = int(d.get("frames", 5))
    color = str(d.get("color", "#F5F3ED")).strip()
    text = str(d.get("text", "")).strip()
    font = str(d.get("font", "Arial")).strip()
    style = str(d.get("style", "Bold")).strip()
    animation = str(d.get("animation", ce.ANIMATIONS[0])).strip()

    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < size <= 1):
        raise ValueError("Размер (0..1) и координаты X/Y (0..1) выходят за допустимые пределы.")
    if frames < 1:
        raise ValueError("Длительность анимации должна быть больше нуля.")
    if not ce.re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("Введите цвет в формате #RRGGBB.")
    if end <= start or not text or not font or not style:
        raise ValueError("Проверьте текст, шрифт и время конца реплики.")
    if not all(line.strip() for line in text.splitlines()):
        raise ValueError("Уберите пустые строки внутри реплики.")

    reveal = tuple(int(v) for v in (d.get("reveal") or ()))
    if reveal and (len(text.split()) != len(reveal) or reveal[-1] >= end - start):
        raise ValueError("В режиме накопления сохраните число слов и оставьте время для последнего слова.")

    return ce.Caption(
        start=start,
        end=end,
        text=text,
        font=font,
        style=style,
        color=color,
        size=size,
        side=side,
        x=x,
        y=y,
        animation=animation if animation in ce.ANIMATIONS else ce.ANIMATIONS[0],
        frames=frames,
        reveal=reveal,
        speech_duration=d.get("speech_duration"),
        full_duration=d.get("full_duration"),
        reference_preset=d.get("reference_preset"),
        preset_name=str(d.get("preset_name") or ""),
    )


class CaptionEditorService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.rows: List[ce.Caption] = []
        self.path: str = ""
        self.words_path: Optional[Path] = None
        self.before_layout: Optional[List[ce.Caption]] = None
        self.before_words: Optional[tuple] = None
        self.before_ortho: Optional[List[ce.Caption]] = None
        self.fonts: Dict[str, List[str]] = {}
        self.busy: bool = False
        self.busy_status: str = ""
        self.last_result: Dict[str, Any] = {}

    def snapshot(self, message: str = "") -> Dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "path": self.path,
                "wordsPath": str(self.words_path) if self.words_path else "",
                "rows": [_row_to_dict(r) for r in self.rows],
                "canUndoLayout": self.before_layout is not None,
                "canUndoWords": self.before_words is not None,
                "canUndoOrtho": self.before_ortho is not None,
                "fonts": self.fonts,
                "busy": self.busy,
                "busyStatus": self.busy_status,
                "message": message,
            }

    def get_busy_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "busy": self.busy,
                "busyStatus": self.busy_status,
                "lastResult": self.last_result,
                "rows": [_row_to_dict(r) for r in self.rows] if not self.busy else None,
            }

    def refresh_fonts(self) -> Dict[str, Any]:
        try:
            raw = resolve_app().Fusion().FontManager.GetFontList()
            fonts = {
                fam: sorted(list(styles), key=str.casefold)
                for fam, styles in raw.items()
                if fam and styles
            }
            if not fonts:
                raise RuntimeError("Resolve вернул пустой список шрифтов.")
            with self._lock:
                self.fonts = fonts
            return {"ok": True, "fonts": fonts, "count": len(fonts)}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "fonts": self.fonts}

    def load_file(self, path: str) -> Dict[str, Any]:
        try:
            file = Path(path)
            if not file.is_file():
                raise ValueError("Выбранный файл не найден.")
            skipped = 0
            words_path: Optional[Path] = None
            before_words = None

            if file.name.lower().endswith(".whisperx.json"):
                data = json.loads(file.read_text(encoding="utf-8"))
                if data.get("version") != 1:
                    raise ValueError("Версия проекта не поддерживается.")
                rows = [ce.Caption(**rec) for rec in data["captions"]]
                if data.get("before_words") is not None:
                    before_words = ([ce.Caption(**rec) for rec in data["before_words"]], None)
                if data.get("words_path"):
                    cand = Path(data["words_path"])
                    words_path = cand if cand.is_file() else (file.parent / data["words_path"])
            elif file.suffix.lower() == ".json":
                rows, skipped = ce.aligned_captions(file)
                if not rows:
                    raise ValueError("В JSON нет таймкодов слов. Создайте субтитры с включённым выравниванием.")
                words_path = file
            else:
                rows = ce.read_srt(file)
                companion = file.with_suffix(".whisperx-words.json")
                if companion.is_file():
                    words_path = companion
                    try:
                        words, _ = ce.aligned_captions(companion, trim_pauses=True)
                        rows = ce.trim_srt_to_words(rows, words)
                    except Exception:
                        pass

            with self._lock:
                self.rows = rows
                self.words_path = words_path
                self.before_layout = None
                self.before_words = before_words
                self.before_ortho = None
                self.path = str(file)
            note = f" · пропущено слов без таймкода: {skipped}" if skipped else ""
            return self.snapshot(f"Открыто реплик: {len(rows)}{note}")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def update_row(self, index: int, row_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            with self._lock:
                if not (0 <= index < len(self.rows)):
                    raise ValueError("Реплика не найдена.")
                prev = self.rows[index]
                merged = {
                    **asdict(prev),
                    **row_data,
                    "reveal": list(prev.reveal),
                    "speech_duration": prev.speech_duration,
                    "full_duration": prev.full_duration,
                    "reference_preset": prev.reference_preset,
                    "preset_name": prev.preset_name,
                }
                self.rows[index] = _dict_to_row(merged)
                return {"ok": True, "row": _row_to_dict(self.rows[index])}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def add_row(self, after_index: Optional[int]) -> Dict[str, Any]:
        with self._lock:
            if after_index is not None and 0 <= after_index < len(self.rows):
                start = self.rows[after_index].end
                idx = after_index + 1
                style = ce.caption_appearance(self.rows[after_index])
                new_row = ce.Caption(start, start + 1500, "Новая реплика", **style)
            else:
                start = self.rows[-1].end if self.rows else 0
                idx = len(self.rows)
                new_row = ce.Caption(start, start + 1500, "Новая реплика")
            self.rows.insert(idx, new_row)
            snap = self.snapshot("Реплика добавлена")
            snap["selectIndex"] = idx
            return snap

    def split_row(self, index: int, cursor_offset: int) -> Dict[str, Any]:
        with self._lock:
            if not (0 <= index < len(self.rows)):
                return {"ok": False, "error": "Выберите реплику."}
            row = self.rows[index]
            left = row.text[:cursor_offset].strip()
            right = row.text[cursor_offset:].strip()
            if not left or not right or row.end - row.start < 2:
                return {"ok": False, "error": "Поставьте курсор внутри текста в точку разделения."}
            mid = (row.start + row.end) // 2
            self.rows[index : index + 1] = [
                replace(row, end=mid, text=left, reveal=(), speech_duration=None, full_duration=None),
                replace(row, start=mid, text=right, reveal=(), speech_duration=None, full_duration=None),
            ]
            snap = self.snapshot("Реплика разделена")
            snap["selectIndex"] = index + 1
            return snap

    def merge_next(self, index: int) -> Dict[str, Any]:
        with self._lock:
            if not (0 <= index < len(self.rows) - 1):
                return {"ok": False, "error": "Нет следующей реплики для объединения."}
            r1, r2 = self.rows[index], self.rows[index + 1]
            merged = replace(
                r1,
                start=min(r1.start, r2.start),
                end=max(r1.end, r2.end),
                text=r1.text + "\n" + r2.text,
                reveal=(),
                speech_duration=None,
                full_duration=None,
            )
            self.rows[index : index + 2] = [merged]
            snap = self.snapshot("Реплики объединены")
            snap["selectIndex"] = index
            return snap

    def delete_row(self, index: int) -> Dict[str, Any]:
        with self._lock:
            if not (0 <= index < len(self.rows)):
                return {"ok": False, "error": "Выберите реплику."}
            del self.rows[index]
            snap = self.snapshot("Реплика удалена")
            snap["selectIndex"] = min(index, len(self.rows) - 1) if self.rows else -1
            return snap

    def apply_style_all(self, index: int) -> Dict[str, Any]:
        with self._lock:
            if not (0 <= index < len(self.rows)):
                return {"ok": False, "error": "Выберите реплику."}
            style = ce.caption_appearance(self.rows[index])
            self.rows = [replace(r, **style) for r in self.rows]
            return self.snapshot("Оформление применено ко всем репликам.")

    def clear_reference_style(self, index: int) -> Dict[str, Any]:
        with self._lock:
            if not (0 <= index < len(self.rows)):
                return {"ok": False, "error": "Выберите реплику."}
            self.rows[index] = replace(self.rows[index], reference_preset=None, preset_name="")
            return self.snapshot("Фактура пресета снята с выбранной реплики.")

    def import_resolve_style(self, index: int) -> Dict[str, Any]:
        try:
            with self._lock:
                if not (0 <= index < len(self.rows)):
                    raise ValueError("Выберите реплику в редакторе.")
            _, timeline = connect_resolve()
            style = ce.read_resolve_text_style(timeline)
            with self._lock:
                self.rows[index] = replace(self.rows[index], **style, reference_preset=None, preset_name="Из Resolve Text+")
            return self.snapshot("Оформление импортировано из Text+ под курсором Resolve.")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def import_style_json(self, index: int, json_path: str) -> Dict[str, Any]:
        try:
            with self._lock:
                if not (0 <= index < len(self.rows)):
                    raise ValueError("Выберите реплику в редакторе.")
            file_path = Path(json_path)
            data = json.loads(file_path.read_text(encoding="utf-8-sig"))
            with self._lock:
                if isinstance(data, dict) and data.get("id") in SUPPORTED_PRESET_IDS:
                    name = str(data.get("title") or "").strip() or file_path.stem
                    validated = ce.validate_style(parse_reference_preset(data))
                    self.rows[index] = replace(self.rows[index], **validated, reference_preset=data, preset_name=name)
                    return self.snapshot(f"Пресет «{name}» применён к реплике.")

                changes = ce.style_from_json(data)
                current = ce.caption_style(self.rows[index])
                preset = data.get("reference_preset") if isinstance(data.get("style"), dict) else None
                if preset is not None:
                    if not isinstance(preset, dict):
                        raise ValueError("В JSON повреждён пресет с потёртостью.")
                    current.update(parse_reference_preset(preset))
                if "font" in changes and "style" not in changes and isinstance(changes["font"], str):
                    styles = self.fonts.get(changes["font"], [])
                    if styles and current["style"] not in styles:
                        changes["style"] = "Regular" if "Regular" in styles else styles[0]
                name = (
                    str(data.get("name") or "").strip()
                    or (str(preset.get("title") or "").strip() if isinstance(preset, dict) else "")
                    or file_path.name.removesuffix(".whisperx-style.json").removesuffix(".json")
                )
                validated = ce.validate_style({**current, **changes})
                self.rows[index] = replace(self.rows[index], **validated, reference_preset=preset, preset_name=name)
                return self.snapshot(f"Пресет «{name}» применён к реплике.")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def save_style_json(self, index: int, save_path: str) -> Dict[str, Any]:
        try:
            with self._lock:
                if not (0 <= index < len(self.rows)):
                    raise ValueError("Выберите реплику.")
                row = self.rows[index]
                fp = Path(save_path)
                saved_name = fp.name.removesuffix(".whisperx-style.json").removesuffix(".json")
                preset: Dict[str, Any] = {"version": 1, "name": saved_name, "style": ce.caption_style(row)}
                if row.reference_preset:
                    preset["reference_preset"] = row.reference_preset
                fp.write_text(json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8")
                if not row.preset_name:
                    self.rows[index] = replace(row, preset_name=saved_name)
                return self.snapshot(f"Пресет «{saved_name}» сохранён.")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def apply_layout(self, per_line: int, line_count: int) -> Dict[str, Any]:
        try:
            if not (1 <= per_line <= 20 and 1 <= line_count <= 8):
                raise ValueError("Укажите 1–20 слов в строке и 1–8 строк в титре.")
            with self._lock:
                if not self.rows:
                    raise ValueError("Нет реплик для разбивки.")
                grouped = ce.group_captions(self.rows, per_line, line_count)
                self.before_layout = self.rows[:]
                self.rows = grouped
                return self.snapshot(f"Разбито на {len(self.rows)} блоков.")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def undo_layout(self) -> Dict[str, Any]:
        with self._lock:
            if self.before_layout is None:
                return {"ok": False, "error": "Нет сохранённой разбивки."}
            self.rows = self.before_layout
            self.before_layout = None
            return self.snapshot("Предыдущая разбивка восстановлена.")

    def apply_word_timing(
        self,
        cumulative: bool,
        per_line: int,
        line_count: int,
        hold_mode: str,
        manual_sec: str,
        active_index: int,
        override_words_path: str = "",
    ) -> Dict[str, Any]:
        try:
            path = Path(override_words_path) if override_words_path else self.words_path
            if not path or not path.is_file():
                return {"ok": False, "needWordsFile": True, "error": "Выберите JSON WhisperX с таймкодами слов."}
            words, skipped = ce.aligned_captions(path, trim_pauses=True)
            if not words:
                raise ValueError("В JSON нет таймкодов слов. Создайте субтитры с включённым выравниванием.")

            manual_ms = None
            if cumulative:
                if not (1 <= per_line <= 20 and 1 <= line_count <= 8):
                    raise ValueError("Укажите 1–20 слов в строке и 1–8 строк в титре.")
                if hold_mode == ce.HOLD_MANUAL:
                    sec = float(str(manual_sec).strip().replace(",", "."))
                    manual_ms = round(sec * 1000)
                    if manual_ms <= 0:
                        raise ValueError("Введите длительность > 0 сек.")

            with self._lock:
                if self.rows:
                    ref_idx = active_index if 0 <= active_index < len(self.rows) else 0
                    style = ce.caption_appearance(self.rows[ref_idx])
                    words = [replace(w, **style) for w in words]
                self.words_path = path
                if self.before_words is None:
                    self.before_words = (self.rows[:], self.before_layout)
                self.before_layout = None
                self.rows = ce.cumulative_captions(words, per_line, line_count, hold_mode, manual_ms) if cumulative else words
                return self.snapshot(f"Титров: {len(self.rows)} · слов WhisperX: {len(words)} · пропущено: {skipped}")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def undo_words(self) -> Dict[str, Any]:
        with self._lock:
            if self.before_words is None:
                return {"ok": False, "error": "Нет сохранённого состояния."}
            self.rows, self.before_layout = self.before_words
            self.before_words = None
            return self.snapshot("Режим по словам отменён.")

    def apply_hold(self, hold_mode: str, manual_sec: str, selected_only: bool, active_index: int) -> Dict[str, Any]:
        try:
            manual_ms = None
            if hold_mode == ce.HOLD_MANUAL:
                sec = float(str(manual_sec).strip().replace(",", "."))
                manual_ms = round(sec * 1000)
                if manual_ms <= 0:
                    raise ValueError("Введите длительность в секундах (например, 1.5).")

            with self._lock:
                if not self.rows:
                    raise ValueError("Нет реплик.")
                if selected_only and not (0 <= active_index < len(self.rows)):
                    raise ValueError("Выберите блок для изменения.")
                if hold_mode == ce.HOLD_MANUAL or selected_only:
                    targets = {active_index} if selected_only else set(range(len(self.rows)))
                else:
                    targets = {i for i, r in enumerate(self.rows) if r.reveal}
                if not targets:
                    raise ValueError("Сначала соберите блоки кнопкой «Накопление слов».")
                ordered = sorted(range(len(self.rows)), key=lambda i: self.rows[i].start)
                next_starts = {i: self.rows[j].start for i, j in zip(ordered, ordered[1:])}
                revised = self.rows[:]
                for i in targets:
                    row = self.rows[i]
                    ns = next_starts.get(i)
                    revised[i] = ce.with_hold(row, hold_mode, manual_ms, ns if ns is not None and ns > row.start else None)
                self.rows = revised
                return self.snapshot(f"Длительность обновлена (блоков: {len(targets)}).")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def apply_orthography(
        self,
        punct_mode: bool,
        keep_qe: bool,
        keep_dots: bool,
        keep_commas: bool,
        case_mode: str,
        selected_only: bool,
        active_index: int,
    ) -> Dict[str, Any]:
        """Multi-option punctuation & word-case transformer preserving line breaks and word reveal sync."""
        try:
            with self._lock:
                if not self.rows:
                    raise ValueError("Нет реплик для обработки.")
                if selected_only and not (0 <= active_index < len(self.rows)):
                    raise ValueError("Выберите реплику для обработки.")

                targets = {active_index} if selected_only else set(range(len(self.rows)))
                all_punct = set(".,!?:;…—–-«»\"'()[]{}/\\|@#$%^&*_~`")
                allowed = set()
                if keep_qe:
                    allowed.update("?!")
                if keep_dots:
                    allowed.update(".…")
                if keep_commas:
                    allowed.add(",")
                strip_chars = (all_punct - allowed) if punct_mode else set()

                def clean_word(tok: str) -> str:
                    if strip_chars:
                        # Keep internal hyphen inside compound words like 'что-то' unless it's standalone
                        if tok in ("-", "—", "–"):
                            tok = ""
                        else:
                            tok = "".join(ch for ch in tok if ch not in strip_chars or (ch == "-" and len(tok) > 1))
                            tok = tok.strip("-")
                    if not tok:
                        return ""
                    if case_mode == "title":
                        # Capitalize first alphabetic char of each word
                        chars = list(tok.lower())
                        for idx_c, ch in enumerate(chars):
                            if ch.isalpha():
                                chars[idx_c] = ch.upper()
                                break
                        tok = "".join(chars)
                    elif case_mode == "lower":
                        tok = tok.lower()
                    elif case_mode == "upper":
                        tok = tok.upper()
                    return tok

                self.before_ortho = self.rows[:]
                revised = self.rows[:]
                changed = 0

                for i in targets:
                    row = self.rows[i]
                    lines = row.text.splitlines()
                    old_reveal = list(row.reveal)
                    has_reveal = bool(old_reveal) and len(old_reveal) == len(row.text.split())
                    r_cursor = 0
                    new_lines: List[str] = []
                    new_reveal: List[int] = []

                    for line in lines:
                        toks = line.split()
                        kept_toks: List[str] = []
                        for tok in toks:
                            cleaned = clean_word(tok)
                            if cleaned:
                                kept_toks.append(cleaned)
                                if has_reveal:
                                    new_reveal.append(old_reveal[r_cursor])
                            r_cursor += 1
                        if kept_toks:
                            new_lines.append(" ".join(kept_toks))

                    new_text = "\n".join(new_lines).strip()
                    if not new_text:
                        continue
                    if new_text != row.text or (has_reveal and tuple(new_reveal) != row.reveal):
                        revised[i] = replace(
                            row,
                            text=new_text,
                            reveal=tuple(new_reveal) if has_reveal else row.reveal,
                        )
                        changed += 1

                self.rows = revised
                return self.snapshot(f"Орфография обновлена (изменено реплик: {changed}).")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def undo_orthography(self) -> Dict[str, Any]:
        with self._lock:
            if self.before_ortho is None:
                return {"ok": False, "error": "Нет сохранённого состояния орфографии."}
            self.rows = self.before_ortho
            self.before_ortho = None
            return self.snapshot("Изменения орфографии отменены.")

    def save_srt_file(self, save_path: str) -> Dict[str, Any]:
        try:
            with self._lock:
                if not self.rows:
                    raise ValueError("Нет реплик для сохранения.")
                rows = sorted(ce.srt_captions(self.rows), key=lambda r: r.start)
                text = "".join(f"{i}\n{ce.time_text(r.start)} --> {ce.time_text(r.end)}\n{r.text}\n\n" for i, r in enumerate(rows, 1))
                Path(save_path).write_text(text, encoding="utf-8")
                self.path = save_path
                return self.snapshot("SRT сохранён. Оформление передаётся в Resolve через Text+.")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def save_project_file(self, save_path: str) -> Dict[str, Any]:
        try:
            with self._lock:
                if not self.rows:
                    raise ValueError("Нет реплик для сохранения.")
                data: Dict[str, Any] = {
                    "version": 1,
                    "captions": [asdict(r) for r in self.rows],
                    "words_path": str(self.words_path.resolve()) if self.words_path else None,
                }
                if self.before_words is not None:
                    data["before_words"] = [asdict(r) for r in self.before_words[0]]
                Path(save_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                return self.snapshot("Проект .whisperx.json сохранён со всем оформлением и анимацией.")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def start_export_resolve(self, behind_person: bool) -> Dict[str, Any]:
        with self._lock:
            if self.busy:
                return {"ok": False, "error": "Выполняется другая операция Resolve."}
            if not self.rows:
                return {"ok": False, "error": "Нет реплик для экспорта."}
            rows_copy = self.rows[:]
            self.busy = True
            self.busy_status = "Подключение к DaVinci Resolve…"
            self.last_result = {}

        def _run():
            try:
                project, timeline = connect_resolve()
                if project.IsRenderingInProgress():
                    raise RuntimeError("Дождитесь завершения рендера в Resolve.")
                if not resolve_app().OpenPage("edit"):
                    raise RuntimeError("Не удалось открыть страницу монтажа в Resolve.")
                self._set_busy_status(f"Добавление Text+ на таймлайн «{timeline.GetName()}»…")
                name = ce.export_titles(
                    project,
                    timeline,
                    rows_copy,
                    behind_person=behind_person,
                    status_callback=self._set_busy_status,
                )
                with self._lock:
                    self.last_result = {"ok": True, "message": f"Готово! Text+ добавлены на таймлайн «{name}»."}
            except Exception as exc:
                with self._lock:
                    self.last_result = {"ok": False, "error": str(exc)}
            finally:
                with self._lock:
                    self.busy = False
                    self.busy_status = ""

        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "started": True}

    def start_auto_place_sides(self, behind_person: bool) -> Dict[str, Any]:
        with self._lock:
            if self.busy:
                return {"ok": False, "error": "Выполняется другая операция Resolve."}
            if not self.rows:
                return {"ok": False, "error": "Нет реплик."}
            if not any(r.reference_preset for r in self.rows):
                return {"ok": False, "error": "Сначала примените JSON-пресет оформления хотя бы к одной реплике."}
            rows_copy = self.rows[:]
            self.busy = True
            self.busy_status = "Анализ пустых зон кадра на таймлайне Resolve…"
            self.last_result = {}

        def _run():
            try:
                project, timeline = connect_resolve()
                fps = float(timeline.GetSetting("timelineFrameRate"))
                width = int(timeline.GetSetting("timelineResolutionWidth") or project.GetSetting("timelineResolutionWidth"))
                height = int(timeline.GetSetting("timelineResolutionHeight") or project.GetSetting("timelineResolutionHeight"))
                origin = timeline.GetStartFrame()
                end = timeline.GetEndFrame()
                ordered = sorted(range(len(rows_copy)), key=lambda i: rows_copy[i].start)
                spans = []
                for idx in ordered:
                    row = rows_copy[idx]
                    first = max(origin, min(end - 1, origin + round(row.start * fps / 1000)))
                    last = max(first + 1, min(end, origin + round(row.end * fps / 1000)))
                    probe = replace(row, side="auto") if row.reference_preset else row
                    spans.append((first, last, probe))

                matte_entries = []
                if behind_person:
                    self._set_busy_status("Построение ИИ-маски персонажа…")
                    _, matte_entries = prepare_subject_mattes(
                        timeline, int(timeline.GetTrackCount("video") or 0), spans,
                        status_callback=self._set_busy_status,
                    )
                self._set_busy_status("Подбор пустых зон кадра…")
                placements = resolve_caption_placements(timeline, spans, width, height, matte_entries=matte_entries or None)

                left_cnt, right_cnt = 0, 0
                updated = rows_copy[:]
                for idx, place in zip(ordered, placements):
                    row = updated[idx]
                    if not row.reference_preset:
                        continue
                    side = place["side"]
                    updated[idx] = replace(row, side=side, x=place["x"], y=place["y"])
                    if side == "left":
                        left_cnt += 1
                    else:
                        right_cnt += 1

                with self._lock:
                    self.rows = updated
                    self.last_result = {"ok": True, "message": f"Стороны подобраны по кадру: слева {left_cnt}, справа {right_cnt}."}
            except Exception as exc:
                with self._lock:
                    self.last_result = {"ok": False, "error": str(exc)}
            finally:
                with self._lock:
                    self.busy = False
                    self.busy_status = ""

        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "started": True}

    def _set_busy_status(self, msg: str) -> None:
        with self._lock:
            self.busy_status = msg
