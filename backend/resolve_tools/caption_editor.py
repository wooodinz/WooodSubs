import json
import math
import re
import time
from bisect import bisect_left
from dataclasses import asdict, dataclass, replace
from pathlib import Path
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
from reference_caption_style import (
    PRESET_ID, SIDE_MODES, parse_reference_preset, place_foreground_person_track,
    prepare_subject_mattes, render_reference_style, resolve_caption_placements,
)


HOLD_FULL = "До следующего блока"
HOLD_SPEECH = "До конца речи"
HOLD_MANUAL = "Вручную (сек)"
SPEECH_PAUSE_MS = 300
VAD_GAP_MS = 150
PHRASE_END_RE = re.compile(r"[.!?…][\"'»)\]]*$")
WORD_CHAR_RE = re.compile(r"\w")
ANIMATIONS = ("Без анимации", "Увеличение", "Плавное появление")
SIDE_LABELS = {"auto": "Авто (композиция)", "left": "Слева", "right": "Справа"}
LABEL_TO_SIDE = {label: key for key, label in SIDE_LABELS.items()}
STYLE_FIELDS = ("font", "style", "color", "size", "side", "x", "y", "animation", "frames")
STYLE_FILE_TYPE = [("Пресет оформления WhisperX", "*.whisperx-style.json"), ("JSON", "*.json")]


def parse_time(value):
    match = re.fullmatch(r"(\d+):([0-5]\d):([0-5]\d)[,.](\d{3})", value.strip())
    if not match:
        raise ValueError("Введите время в формате ЧЧ:ММ:СС,мс.")
    h, m, s, ms = map(int, match.groups())
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def time_text(ms):
    seconds, fraction = divmod(ms, 1000)
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02}:{minute:02}:{second:02},{fraction:03}"


@dataclass
class Caption:
    start: int
    end: int
    text: str
    font: str = "Arial"
    style: str = "Bold"
    color: str = "#F5F3ED"
    size: float = 0.07
    side: str = "auto"
    x: float = 0.50
    y: float = 0.22
    animation: str = ANIMATIONS[0]
    frames: int = 5
    reveal: tuple[int, ...] = ()
    speech_duration: int | None = None
    full_duration: int | None = None
    reference_preset: dict | None = None
    preset_name: str = ""

    def __post_init__(self):
        self.reveal = tuple(self.reveal)


DEFAULT_PRESET_LABEL = "Стандартный (без пресета)"


def caption_preset_label(row):
    if row.preset_name:
        return row.preset_name
    if isinstance(row.reference_preset, dict):
        return (
            str(row.reference_preset.get("title") or "").strip()
            or str(row.reference_preset.get("id") or "").strip()
            or "Пресет с потёртостью"
        )
    return DEFAULT_PRESET_LABEL


def caption_style(row):
    return {key: getattr(row, key) for key in STYLE_FIELDS}


def caption_appearance(row):
    return {
        **caption_style(row),
        "reference_preset": row.reference_preset,
        "preset_name": row.preset_name,
    }


def validate_style(style):
    if not isinstance(style, dict) or not set(style) <= set(STYLE_FIELDS):
        raise ValueError("Файл не содержит пресет оформления WhisperX.")
    for name in ("font", "style"):
        if not isinstance(style.get(name), str) or not style[name].strip():
            raise ValueError("В оформлении не указано имя шрифта или начертание.")
    if not isinstance(style.get("color"), str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", style["color"]):
        raise ValueError("Некорректный цвет в оформлении.")
    for name in ("size", "x", "y"):
        value = style.get(name)
        if type(value) not in (float, int) or not 0 <= value <= 1 or (name == "size" and value == 0):
            raise ValueError("Размер и позиция выходят за допустимые значения редактора.")
    if "side" in style and style["side"] not in SIDE_MODES:
        raise ValueError("Неизвестный режим выбора стороны кадра в пресете.")
    if "animation" in style and style["animation"] not in ANIMATIONS:
        raise ValueError("Неизвестная анимация в пресете.")
    if "frames" in style and (type(style["frames"]) is not int or style["frames"] < 1):
        raise ValueError("Укажите положительное число кадров анимации.")
    return style


def style_from_json(document):
    if not isinstance(document, dict):
        raise ValueError("JSON с оформлением должен содержать объект с настройками.")
    if isinstance(document.get("style"), dict):
        if "version" in document and document["version"] != 1:
            raise ValueError("Версия пресета WhisperX не поддерживается.")
        document = document["style"]
    elif isinstance(document.get("settings"), dict):
        document = document["settings"]

    inputs = {key.casefold(): value for key, value in document.items()}
    style = {key: inputs[key] for key in STYLE_FIELDS if key in inputs}
    if "side" in style and isinstance(style["side"], str):
        style["side"] = style["side"].strip().lower()
    if "center" in inputs:
        center = inputs["center"]
        if isinstance(center, list) and len(center) >= 2:
            x, y = center[:2]
        elif isinstance(center, dict):
            x, y = center.get("x", center.get("1")), center.get("y", center.get("2"))
        else:
            raise ValueError("Поле Center должно содержать две координаты.")
        style.setdefault("x", x)
        style.setdefault("y", y)
    if "color" not in style and any(key in inputs for key in ("red1", "green1", "blue1")):
        channels = [inputs.get(key) for key in ("red1", "green1", "blue1")]
        if not all(type(value) in (float, int) and 0 <= value <= 1 for value in channels):
            raise ValueError("Поля Red1, Green1 и Blue1 должны быть числами от 0 до 1.")
        style["color"] = "#" + "".join(f"{round(value * 255):02X}" for value in channels)
    if not style:
        raise ValueError("В этом JSON нет настроек оформления текста.")
    return style


def read_resolve_text_style(timeline):
    item = timeline.GetCurrentVideoItem()
    if not item or not item.GetFusionCompCount():
        raise ValueError("Поставьте курсор Resolve на Text+ и повторите импорт.")
    comp = item.GetFusionCompByIndex(1)
    node = next((node for node in comp.GetToolList(False).values() if node.ID == "TextPlus"), None) if comp else None
    if not node:
        raise ValueError("В клипе под курсором нет Text+. Выберите другой клип.")
    start = int(comp.GetAttrs().get("COMPN_GlobalStart", 0))
    frame = start + max(0, int(item.GetDuration()) - 1)
    center = node.GetInput("Center", frame)
    color = "#" + "".join(f"{round(max(0, min(1, node.GetInput(channel, frame))) * 255):02X}"
                          for channel in ("Red1", "Green1", "Blue1"))
    return validate_style({
        "font": node.GetInput("Font", frame),
        "style": node.GetInput("Style", frame),
        "color": color,
        "size": node.GetInput("Size", frame),
        "x": center[1],
        "y": center[2],
    })


def read_srt(path):
    content = Path(path).read_text(encoding="utf-8-sig").strip()
    rows = []
    for block in re.split(r"\n[ \t]*\n", content):
        lines = block.splitlines()
        if not lines:
            continue
        if lines[0].strip().isdigit():
            lines.pop(0)
        marks = lines[0].split("-->", 1)
        if len(marks) != 2 or len(lines) < 2:
            raise ValueError("Не удалось прочитать один из блоков SRT.")
        rows.append(Caption(parse_time(marks[0]), parse_time(marks[1].split()[0]), "\n".join(lines[1:])))
    return rows


def aligned_captions(path, trim_pauses=True):
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    words = payload.get("word_segments")
    if not words:
        words = [word for segment in payload.get("segments", []) for word in segment.get("words", [])]
    segment_ends = {
        round(float(segment["end"]) * 1000)
        for segment in payload.get("segments", [])
        if isinstance(segment.get("end"), (float, int)) and math.isfinite(segment["end"])
    }
    captions = []
    skipped = 0
    for word in words:
        token = word["word"].strip()
        if not token:
            continue
        start, end = word.get("start"), word.get("end")
        if (not isinstance(start, (float, int)) or not isinstance(end, (float, int))
                or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start):
            skipped += 1
            continue
        first = round(start * 1000)
        captions.append(Caption(first, max(first + 1, round(end * 1000)), token))
    captions.sort(key=lambda row: row.start)
    if trim_pauses and captions:
        captions = trim_to_speech(captions, payload.get("speech_intervals", []), segment_ends=segment_ends)
    return captions, skipped


def trim_to_speech(words, intervals, segment_ends=()):
    spans = [(round(start * 1000), round(end * 1000)) for start, end in intervals]
    gap_starts = [end for (_, end), (next_start, _) in zip(spans, spans[1:])
                  if next_start - end >= VAD_GAP_MS]
    if spans and words and max(word.end for word in words) - spans[-1][1] >= VAD_GAP_MS:
        gap_starts.append(spans[-1][1])
    has_vad = bool(spans)
    corrected = []
    for index, word in enumerate(words):
        end = word.end
        i = bisect_left(gap_starts, word.start + 1)
        if i < len(gap_starts) and gap_starts[i] < end:
            end = gap_starts[i]
        letters = max(1, len(WORD_CHAR_RE.findall(word.text)))
        is_phrase_end = bool(PHRASE_END_RE.search(word.text)) or (word.end in segment_ends)
        if not has_vad:
            max_dur = max(480, letters * 80 + 180) if is_phrase_end else max(750, letters * 95 + 250)
        else:
            max_dur = max(900, letters * 110 + 300) if is_phrase_end else max(1100, letters * 120 + 350)
        if end - word.start > max_dur:
            end = word.start + max_dur
        if index + 1 < len(words) and words[index + 1].start > word.start:
            end = min(end, words[index + 1].start)
        end = max(word.start + 1, end)
        corrected.append(replace(word, end=end) if end != word.end else word)
    return corrected


def trim_srt_to_words(rows, words):
    if not words:
        return rows
    trimmed = []
    for row in rows:
        matched = [w for w in words if row.start - 200 <= w.start < row.end]
        if matched and row.start < matched[-1].end < row.end:
            row = replace(row, end=matched[-1].end)
        trimmed.append(row)
    return trimmed


def group_captions(rows, per_line, line_count):
    grouped = []
    capacity = per_line * line_count
    for row in rows:
        words = row.text.split()
        if not words:
            continue
        starts = [0]
        for i in range(1, len(words)):
            if i - starts[-1] >= capacity or PHRASE_END_RE.search(words[i - 1]):
                starts.append(i)
        duration = row.end - row.start
        for offset, next_offset in zip(starts, [*starts[1:], len(words)]):
            block = words[offset:next_offset]
            lines = [" ".join(block[i:i + per_line]) for i in range(0, len(block), per_line)]
            start = row.start + round(duration * offset / len(words))
            end = row.start + round(duration * next_offset / len(words))
            if end <= start:
                raise ValueError(f"Реплика {time_text(row.start)} слишком короткая для такой разбивки.")
            grouped.append(replace(row, start=start, end=end, text="\n".join(lines), reveal=(),
                                   speech_duration=None, full_duration=None))
    return grouped


def cumulative_captions(words, per_line, line_count, hold=HOLD_SPEECH, manual_ms=None):
    capacity = per_line * line_count
    starts = [0]
    for i in range(1, len(words)):
        if (i - starts[-1] >= capacity
                or words[i].start - words[i - 1].end >= SPEECH_PAUSE_MS
                or PHRASE_END_RE.search(words[i - 1].text)):
            starts.append(i)
    result = []
    if not words:
        return result
    for offset, next_block in zip(starts, [*starts[1:], len(words)]):
        block = words[offset:next_block]
        first = block[0].start
        next_start = words[next_block].start if next_block < len(words) else None
        speech_end = block[-1].end
        end = next_start if next_start is not None else speech_end
        lines = [" ".join(word.text for word in block[i:i + per_line])
                 for i in range(0, len(block), per_line)]
        caption = replace(block[0], end=end, text="\n".join(lines),
                          reveal=tuple(word.start - first for word in block),
                          speech_duration=speech_end - first, full_duration=end - first)
        result.append(with_hold(caption, hold, manual_ms, next_start))
    return result


def with_hold(row, mode, manual_ms, next_start=None):
    if mode == HOLD_FULL:
        requested = row.full_duration
    elif mode == HOLD_SPEECH:
        requested = row.speech_duration
    else:
        requested = manual_ms
    if requested is None:
        raise ValueError("Для автоматической длительности сначала соберите блоки заново через «Накопление слов».")
    # Leave at least a brief display for the last word if the requested time is too short.
    if row.reveal and requested <= row.reveal[-1]:
        requested = min(row.reveal[-1] + 50, max(row.reveal[-1] + 1, row.speech_duration or 0))
    end = row.start + max(1, requested)
    if next_start is not None:
        end = min(end, next_start)
    if row.reveal:
        end = max(end, row.start + row.reveal[-1] + 1)
    return replace(row, end=end)


def reveal_prefixes(row):
    return [row.text[:word.end()] for word in re.finditer(r"\S+", row.text)]


def srt_captions(rows):
    for row in rows:
        if not row.reveal:
            yield row
            continue
        steps = dict(zip(row.reveal, reveal_prefixes(row)))
        for (offset, prefix), end in zip(steps.items(), [*list(steps)[1:], row.end - row.start]):
            if end > offset:
                yield replace(row, text=prefix, start=row.start + offset, end=row.start + end, reveal=())


def resolve_timecode(frame, fps, drop):
    nominal = round(fps)
    if drop:
        skipped = round(nominal * .0666667)
        per_minute = nominal * 60 - skipped
        per_ten = nominal * 600 - skipped * 9
        tens, remainder = divmod(frame, per_ten)
        frame += skipped * (9 * tens + max(0, (remainder - skipped) // per_minute))
    seconds, fraction = divmod(frame, nominal)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    separator = ";" if drop else ":"
    return f"{hour % 24:02}:{minute:02}:{second:02}{separator}{fraction:02}"


def seek_for_title(timeline, timecode):
    if not timeline.SetCurrentTimecode(timecode):
        return False
    deadline = time.monotonic() + .5
    while timeline.GetCurrentTimecode() != timecode:
        if time.monotonic() >= deadline:
            return False
        time.sleep(.02)
    return True


def export_titles(project, timeline, captions, behind_person=False, status_callback=None):
    current = project.GetCurrentTimeline()
    if not current or current.GetUniqueId() != timeline.GetUniqueId():
        raise RuntimeError("Открытый таймлайн изменился. Запустите экспорт снова.")
    fps = float(timeline.GetSetting("timelineFrameRate"))
    origin = timeline.GetStartFrame()
    end = timeline.GetEndFrame()
    drop = ";" in timeline.GetStartTimecode()
    spans = []
    for caption in sorted(captions, key=lambda row: row.start):
        first = origin + round(caption.start * fps / 1000)
        last = max(first + 1, origin + round(caption.end * fps / 1000))
        if first < origin or last > end:
            raise ValueError(f"Реплика {time_text(caption.start)} выходит за пределы таймлайна.")
        spans.append((first, last, caption))
    original_tracks = timeline.GetTrackCount("video")
    matte_entries = []
    if behind_person and spans:
        _, matte_entries = prepare_subject_mattes(
            timeline, original_tracks, spans, status_callback=status_callback,
        )
    resolved_placements = None
    if any(caption.reference_preset for _, _, caption in spans):
        width = int(timeline.GetSetting("timelineResolutionWidth") or project.GetSetting("timelineResolutionWidth"))
        height = int(timeline.GetSetting("timelineResolutionHeight") or project.GetSetting("timelineResolutionHeight"))
        if width <= 0 or height <= 0:
            raise ValueError("Не удалось определить размер кадра таймлайна.")
        if status_callback:
            status_callback("Подбор пустых зон кадра для субтитров…")
        resolved_placements = resolve_caption_placements(
            timeline, spans, width, height, matte_entries=matte_entries or None,
        )
    playhead = timeline.GetCurrentTimecode()
    staging_timecode = resolve_timecode(end, fps, drop)
    locked = original_tracks > 0 and timeline.GetIsTrackLocked("video", 1)
    unlocked = False
    created_items = []
    created_tracks = []
    try:
        if locked:
            unlocked = timeline.SetTrackLock("video", 1, False)
            if not unlocked:
                raise RuntimeError("Не удалось временно разблокировать V1 для вставки Text+.")
        lane_ends = []
        for caption_index, (first, last, caption) in enumerate(spans):
            lane = next((i for i, lane_end in enumerate(lane_ends) if lane_end <= first), len(lane_ends))
            if lane == len(lane_ends):
                if not timeline.AddTrack("video"):
                    raise RuntimeError("Не удалось добавить дорожку Text+.")
                track = original_tracks + lane + 1
                created_tracks.append(track)
                timeline.SetTrackName("video", track, f"WhisperX Text+ (V{track})")
                lane_ends.append(last)
            else:
                lane_ends[lane] = last
            frame = first
            while frame < last:
                if not seek_for_title(timeline, staging_timecode):
                    raise RuntimeError("Не удалось поставить курсор в конец таймлайна.")
                item = timeline.InsertFusionTitleIntoTimeline("Text+")
                if item:
                    created_items.append(item)
                if not item or item.GetStart() != end:
                    raise RuntimeError("Resolve не смог вставить Text+ после последнего кадра.")
                length = int(item.GetDuration())
                used = min(length, last - frame)
                if used < length and not item.SetRightOffset(item.GetRightOffset() + length - used):
                    raise RuntimeError("Resolve не удалось задать длину Text+.")
                track = original_tracks + lane + 1
                if not timeline.MoveItems([item], {"trackIndex": track, "startFrame": frame}, False):
                    raise RuntimeError("Resolve не удалось переместить Text+ на дорожку.")
                if item.GetStart() != frame or item.GetDuration() != used:
                    raise RuntimeError("Resolve изменил рассчитанный таймкод титра.")
                reveals = [(min(last - 1, origin + round((caption.start + offset) * fps / 1000)) - frame, prefix)
                           for offset, prefix in zip(caption.reveal, reveal_prefixes(caption))]
                if caption.reference_preset:
                    style_title(
                        item, caption, used, frame == first, reveals,
                        reference_context=(
                            width, height, caption_index, frame - first, last - first,
                            resolved_placements[caption_index] if resolved_placements else None,
                        ),
                    )
                else:
                    style_title(item, caption, used, frame == first, reveals)
                frame += used
        if behind_person and created_tracks and matte_entries:
            fg_track, fg_items = place_foreground_person_track(project, timeline, matte_entries)
            if fg_track is not None:
                created_tracks.append(fg_track)
                created_items.extend(fg_items)
        return timeline.GetName()
    except Exception as error:
        rollback_ok = not created_items or timeline.DeleteClips(created_items, False)
        for track in reversed(created_tracks):
            if timeline.GetItemListInTrack("video", track):
                rollback_ok = False
            elif not timeline.DeleteTrack("video", track):
                rollback_ok = False
        if not rollback_ok:
            raise RuntimeError(f"{error}\nResolve не смог полностью убрать титры, созданные при этом экспорте.") from error
        raise
    finally:
        if unlocked:
            timeline.SetTrackLock("video", 1, True)
        if playhead:
            timeline.SetCurrentTimecode(playhead)


def style_title(item, row, length, first_piece, reveals=(), reference_context=None):
    comp = item.GetFusionCompByIndex(1)
    node = next(n for n in comp.GetToolList(False).values() if n.ID == "TextPlus")
    if row.reference_preset:
        render_reference_style(comp, node, row, reveals, reference_context)
        return
    start = int(comp.GetAttrs().get("COMPN_GlobalStart", 0))
    for key, value in (
        ("StyledText", row.text), ("Font", row.font), ("Style", row.style),
        ("Center", {1: row.x, 2: row.y}), ("Size", row.size),
    ):
        node.SetInput(key, value, start)
    for index, channel in enumerate(("Red1", "Green1", "Blue1")):
        node.SetInput(channel, int(row.color[1 + index * 2:3 + index * 2], 16) / 255, start)
    if node.GetInput("StyledText", start) != row.text:
        raise RuntimeError("Resolve повредил текст при записи. Экспорт прерван.")
    if reveals:
        progress = {0: 0.0}
        for frame, prefix in reveals:
            if frame < length:
                progress[max(0, frame)] = len(prefix) / len(row.text)
        node.End = comp.BezierSpline()
        previous = progress[0]
        for frame, value in progress.items():
            if frame:
                node.SetInput("End", previous, start + frame - 1)
            node.SetInput("End", value, start + frame)
            previous = value
    if not first_piece or length < 2:
        return
    stop = start + min(length - 1, row.frames)
    if row.animation == "Увеличение":
        node.Size = comp.BezierSpline()
        node.SetInput("Size", row.size * .82, start)
        node.SetInput("Size", row.size, stop)
    elif row.animation == "Плавное появление":
        node.Opacity1 = comp.BezierSpline()
        node.SetInput("Opacity1", 0, start)
        node.SetInput("Opacity1", 1, stop)


class CaptionEditor(tk.Toplevel):
    def __init__(self, app, path=None):
        super().__init__(app)
        self.app = app
        self.title("WhisperX — редактор субтитров")
        self.geometry("1000x680")
        self.minsize(850, 600)
        self.rows = []
        self.active = None
        self.variables = {}
        self.path = path
        self.words_path = None
        self.before_layout = None
        self.before_words = None
        self.fonts = {}
        self.build()
        self.refresh_fonts(show_status=False)
        if path:
            self.load(path)

    def build(self):
        toolbar = ttk.Frame(self, padding=10)
        toolbar.pack(fill="x")
        editing = ttk.Frame(self, padding=(10, 0, 10, 8))
        editing.pack(fill="x")
        for label, action in (
            ("Открыть SRT или проект", self.open_srt), ("Сохранить SRT", self.save_srt),
            ("Сохранить проект", self.save_project), ("Создать Text+ в Resolve", self.export),
        ):
            ttk.Button(toolbar, text=label, command=action).pack(side="left", padx=3)
        style_menu_button = ttk.Menubutton(toolbar, text="Оформление ▾")
        style_menu = tk.Menu(style_menu_button, tearoff=False)
        style_menu.add_command(label="Импортировать из Text+ под курсором", command=self.import_resolve_style)
        style_menu.add_command(label="Импортировать оформление из JSON…", command=self.import_style_preset)
        style_menu.add_command(label="Сохранить пресет оформления…", command=self.save_style_preset)
        style_menu.add_command(label="Подобрать стороны по кадру (Resolve)", command=self.auto_place_sides)
        style_menu.add_command(label="Снять фактуру с выбранной реплики", command=self.clear_reference_style)
        style_menu_button.configure(menu=style_menu)
        style_menu_button.pack(side="left", padx=3)
        self.text_behind_person = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            toolbar, text="Текст за персонажем (ИИ-маска)", variable=self.text_behind_person,
        ).pack(side="left", padx=8)
        for label, action in (
            ("Добавить", self.add), ("Разделить", self.split), ("Объединить со следующей", self.merge),
            ("Удалить", self.remove),
        ):
            ttk.Button(editing, text=label, command=action).pack(side="left", padx=3)
        self.preset_summary_var = tk.StringVar(value=f"Пресет: {DEFAULT_PRESET_LABEL}")
        ttk.Label(editing, textvariable=self.preset_summary_var).pack(side="right", padx=(8, 4))
        layout = ttk.Frame(self, padding=(10, 0, 10, 8))
        layout.pack(fill="x")
        self.per_line = tk.StringVar(value="3")
        self.line_count = tk.StringVar(value="2")
        for label, variable, maximum in (
            ("Слов в строке", self.per_line, 20), ("Строк в титре", self.line_count, 8),
        ):
            ttk.Label(layout, text=label).pack(side="left", padx=(3, 4))
            ttk.Spinbox(layout, from_=1, to=maximum, width=4, textvariable=variable).pack(side="left", padx=(0, 8))
        ttk.Button(layout, text="Разбить на блоки", command=self.apply_layout).pack(side="left", padx=3)
        ttk.Button(layout, text="Вернуть разбивку", command=self.undo_layout).pack(side="left", padx=3)
        word_modes = ttk.Frame(self, padding=(10, 0, 10, 8))
        word_modes.pack(fill="x")
        ttk.Button(word_modes, text="По одному слову", command=self.apply_word_timing).pack(side="left", padx=3)
        ttk.Button(word_modes, text="Накопление слов",
                   command=lambda: self.apply_word_timing(cumulative=True)).pack(side="left", padx=3)
        self.undo_words_button = ttk.Button(word_modes, text="Отменить режим по словам",
                                            command=self.undo_words, state="disabled")
        self.undo_words_button.pack(side="left", padx=3)
        hold_row = ttk.Frame(self, padding=(10, 0, 10, 8))
        hold_row.pack(fill="x")
        self.hold_mode = tk.StringVar(value=HOLD_SPEECH)
        self.hold_seconds = tk.StringVar(value="2,0")
        ttk.Label(hold_row, text="Длительность Text+").pack(side="left", padx=(3, 6))
        hold_picker = ttk.Combobox(hold_row, state="readonly", textvariable=self.hold_mode, width=22,
                                   values=(HOLD_FULL, HOLD_SPEECH, HOLD_MANUAL))
        hold_picker.pack(side="left", padx=(0, 6))
        self.hold_entry = ttk.Entry(hold_row, textvariable=self.hold_seconds, width=7, state="disabled")
        self.hold_entry.pack(side="left", padx=(0, 6))
        hold_picker.bind("<<ComboboxSelected>>", self.update_hold_input)
        ttk.Button(hold_row, text="Применить к выбранному",
                   command=lambda: self.apply_hold(selected_only=True)).pack(side="left", padx=3)
        ttk.Button(hold_row, text="Применить ко всем", command=self.apply_hold).pack(side="left", padx=3)
        body = ttk.Frame(self, padding=(10, 0, 10, 10))
        body.pack(fill="both", expand=True)
        self.table = ttk.Treeview(body, columns=("start", "end", "text"), show="headings", selectmode="browse")
        for key, title, width in (("start", "Начало", 110), ("end", "Конец", 110), ("text", "Реплика", 225)):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, stretch=key == "text")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.table.bind("<<TreeviewSelect>>", self.choose)
        form = ttk.Frame(body, padding=(12, 0, 0, 0))
        form.pack(side="right", fill="y")
        form.columnconfigure(1, weight=1)
        self.preset_var = tk.StringVar(value=DEFAULT_PRESET_LABEL)
        ttk.Label(form, text="Пресет").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=self.preset_var, state="readonly", width=22).grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=6,
        )
        for index, (key, label) in enumerate((
            ("start", "Начало"), ("end", "Конец"), ("font", "Шрифт"), ("style", "Начертание"),
            ("color", "Цвет"), ("size", "Размер"), ("side", "Сторона кадра"),
            ("x", "Позиция X (слева направо)"), ("y", "Позиция Y (снизу вверх)"),
            ("animation", "Анимация"), ("frames", "Кадров анимации"),
        ), start=1):
            variable = self.variables[key] = tk.StringVar()
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", pady=3)
            if key == "animation":
                field = ttk.Combobox(form, textvariable=variable, state="readonly", width=19,
                                     values=ANIMATIONS)
            elif key == "side":
                field = ttk.Combobox(form, textvariable=variable, state="readonly", width=19,
                                     values=tuple(SIDE_LABELS.values()))
                field.bind("<<ComboboxSelected>>", self.select_side)
            elif key in ("font", "style"):
                field = ttk.Combobox(form, textvariable=variable, width=21)
                if key == "font":
                    self.font_picker = field
                    field.bind("<<ComboboxSelected>>", self.select_font)
                else:
                    self.style_picker = field
            else:
                field = ttk.Entry(form, textvariable=variable, width=22)
            field.grid(row=index, column=1, sticky="ew", padx=6)
        ttk.Button(form, text="…", width=3, command=self.pick_color).grid(row=5, column=2)
        ttk.Button(form, text="↻", width=3, command=self.refresh_fonts).grid(row=3, column=2)
        self.words = tk.Text(form, height=5, width=37, wrap="word")
        self.words.grid(row=12, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        ttk.Label(form, text="Enter добавляет строку в текущую реплику.").grid(row=13, column=0, columnspan=3, sticky="w")
        ttk.Button(form, text="Применить оформление ко всем", command=self.apply_all).grid(row=14, column=0, columnspan=3, sticky="ew", pady=8)
        self.status = ttk.Label(self, padding=(12, 0, 12, 8), text="Откройте SRT или добавьте реплику.")
        self.status.pack(fill="x")

    def refresh_fonts(self, show_status=True):
        try:
            fonts = self.app.resolve_fonts()
            if not fonts:
                raise RuntimeError("Resolve вернул пустой список шрифтов.")
        except Exception:
            if show_status:
                self.status.configure(text="Список шрифтов недоступен. Откройте Resolve и нажмите ↻.")
            return
        self.fonts = fonts
        self.font_picker.configure(values=sorted(fonts, key=str.casefold), state="readonly")
        self.update_font_styles()
        if show_status:
            self.status.configure(text=f"Список шрифтов Resolve обновлён. Шрифтов: {len(fonts)}.")

    def update_font_styles(self, choose_default=False):
        styles = self.fonts.get(self.variables["font"].get(), ())
        self.style_picker.configure(values=styles, state="readonly" if styles else "normal")
        if choose_default and styles and self.variables["style"].get() not in styles:
            self.variables["style"].set("Regular" if "Regular" in styles else styles[0])

    def select_font(self, _event):
        self.update_font_styles(choose_default=True)

    def select_side(self, _event=None):
        if self.active is None:
            return
        side = LABEL_TO_SIDE.get(self.variables["side"].get().strip(), "auto")
        preset = self.rows[self.active].reference_preset
        if preset and side in ("left", "right"):
            layout = preset.get("layout") or {}
            if side in layout and "x" in layout[side]:
                self.variables["x"].set(str(layout[side]["x"]))

    def refresh(self, index=None):
        self.active = None
        self.table.delete(*self.table.get_children())
        for i, row in enumerate(self.rows):
            self.table.insert("", "end", iid=str(i), values=(time_text(row.start), time_text(row.end), row.text.replace("\n", " / ")))
        if index is not None:
            self.table.selection_set(str(index))
            self.table.see(str(index))
            self.show_row(index)
        else:
            for variable in self.variables.values():
                variable.set("")
            self.words.delete("1.0", "end")
            self.update_preset_indicator()

    def update_preset_indicator(self):
        if self.active is not None and 0 <= self.active < len(self.rows):
            active_label = caption_preset_label(self.rows[self.active])
        else:
            active_label = DEFAULT_PRESET_LABEL
        self.preset_var.set(active_label)
        if not self.rows:
            self.preset_summary_var.set(f"Пресет: {DEFAULT_PRESET_LABEL}")
            return
        labels = [caption_preset_label(row) for row in self.rows]
        same_count = sum(1 for lbl in labels if lbl == active_label)
        short = active_label if len(active_label) <= 44 else active_label[:43] + "…"
        if active_label == DEFAULT_PRESET_LABEL and same_count == len(self.rows):
            self.preset_summary_var.set(f"Пресет: {DEFAULT_PRESET_LABEL}")
        else:
            self.preset_summary_var.set(f"Пресет: {short} ({same_count}/{len(self.rows)})")

    def show_row(self, i):
        self.active = i
        row = self.rows[i]
        for key, variable in self.variables.items():
            value = getattr(row, key)
            if key in ("start", "end"):
                variable.set(time_text(value))
            elif key == "side":
                variable.set(SIDE_LABELS.get(value, SIDE_LABELS["auto"]))
            else:
                variable.set(str(value))
        self.update_font_styles()
        self.words.delete("1.0", "end")
        self.words.insert("1.0", row.text)
        self.update_preset_indicator()

    def commit(self):
        if self.active is None:
            return True
        try:
            data = {key: variable.get().strip() for key, variable in self.variables.items()}
            for key in ("start", "end"):
                data[key] = parse_time(data[key])
            data["side"] = LABEL_TO_SIDE.get(data["side"], data["side"].lower())
            if data["side"] not in SIDE_MODES:
                raise ValueError("Выберите корректную сторону кадра.")
            for key in ("x", "y", "size"):
                data[key] = float(data[key].replace(",", "."))
                if not 0 <= data[key] <= 1:
                    raise ValueError("Размер и координаты должны быть от 0 до 1.")
            data["frames"] = int(data["frames"])
            if data["size"] == 0 or data["frames"] < 1:
                raise ValueError("Размер и длительность анимации должны быть больше нуля.")
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", data["color"]):
                raise ValueError("Введите цвет в формате #RRGGBB.")
            data["text"] = self.words.get("1.0", "end-1c").strip()
            if data["end"] <= data["start"] or not data["text"] or not data["font"] or not data["style"]:
                raise ValueError("Проверьте текст, шрифт и время конца реплики.")
            if not all(line.strip() for line in data["text"].splitlines()):
                raise ValueError("Уберите пустые строки внутри реплики.")
            reveal = tuple(self.rows[self.active].reveal)
            if reveal and (len(data["text"].split()) != len(reveal)
                           or reveal[-1] >= data["end"] - data["start"]):
                raise ValueError("В режиме накопления сохраните число слов и оставьте время для последнего слова.")
            data["reveal"] = reveal
            data["speech_duration"] = self.rows[self.active].speech_duration
            data["full_duration"] = self.rows[self.active].full_duration
            data["reference_preset"] = self.rows[self.active].reference_preset
            data["preset_name"] = self.rows[self.active].preset_name
            row = self.rows[self.active] = Caption(**data)
            self.table.item(str(self.active), values=(time_text(row.start), time_text(row.end), row.text.replace("\n", " / ")))
            return True
        except ValueError as error:
            messagebox.showerror("Редактор субтитров", str(error), parent=self)
            return False

    def choose(self, _event):
        selected = self.table.selection()
        if selected and int(selected[0]) != self.active:
            if self.commit():
                self.show_row(int(selected[0]))
            else:
                self.table.selection_set(str(self.active))

    def pick_color(self):
        _, color = colorchooser.askcolor(parent=self)
        if color:
            self.variables["color"].set(color)

    def open_srt(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("Субтитры и проекты WhisperX", "*.srt *.json")])
        if path:
            self.load(path)

    def load(self, path):
        try:
            file = Path(path)
            skipped = 0
            words_path = None
            before_words = None
            if file.name.lower().endswith(".whisperx.json"):
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                if data["version"] != 1:
                    raise ValueError("Версия проекта не поддерживается.")
                rows = [Caption(**record) for record in data["captions"]]
                if data.get("before_words") is not None:
                    before_words = ([Caption(**record) for record in data["before_words"]], None, None)
                if data.get("words_path"):
                    words_path = file.parent / data["words_path"]
            elif file.suffix.lower() == ".json":
                rows, skipped = aligned_captions(file)
                if not rows:
                    raise ValueError("В JSON нет таймкодов слов. Создайте субтитры с включённым выравниванием.")
                words_path = file
            else:
                rows = read_srt(file)
                companion = file.with_suffix(".whisperx-words.json")
                if companion.is_file():
                    words_path = companion
                    try:
                        words, _ = aligned_captions(companion, trim_pauses=True)
                        rows = trim_srt_to_words(rows, words)
                    except (OSError, ValueError, KeyError, TypeError):
                        pass
            self.rows = rows
            self.words_path = words_path
            self.before_layout = None
            self.before_words = before_words
            self.undo_words_button.configure(state="normal" if before_words is not None else "disabled")
            self.path = path
            self.refresh(0 if self.rows else None)
            note = f" · Слов без таймкода пропущено: {skipped}" if skipped else ""
            self.status.configure(text=f"Открыто {len(self.rows)} реплик{note}")
        except (OSError, ValueError, KeyError, TypeError) as error:
            messagebox.showerror("Открыть субтитры", str(error), parent=self)

    def save_project(self):
        if not self.commit() or not self.rows:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".whisperx.json",
                                           filetypes=[("Проект WhisperX", "*.whisperx.json")])
        if path:
            try:
                data = {"version": 1, "captions": [asdict(row) for row in self.rows],
                        "words_path": str(self.words_path.resolve()) if self.words_path else None}
                if self.before_words is not None:
                    data["before_words"] = [asdict(row) for row in self.before_words[0]]
                Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                self.status.configure(text="Проект сохранён вместе с оформлением и анимацией.")
            except OSError as error:
                messagebox.showerror("Сохранить проект", str(error), parent=self)

    def save_srt(self):
        if not self.commit() or not self.rows:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".srt",
                                           initialfile=(Path(self.path).name.removesuffix(".whisperx.json").removesuffix(".srt") + ".srt")
                                           if self.path else "subtitles.srt",
                                           initialdir=self.app.destination.get().strip() or None,
                                           filetypes=[("SubRip", "*.srt")])
        if path:
            try:
                rows = sorted(srt_captions(self.rows), key=lambda row: row.start)
                Path(path).write_text("".join(f"{i}\n{time_text(r.start)} --> {time_text(r.end)}\n{r.text}\n\n"
                                              for i, r in enumerate(rows, 1)), encoding="utf-8")
                self.path = path
                self.status.configure(text="SRT сохранён. Оформление передаётся в Resolve через Text+.")
            except OSError as error:
                messagebox.showerror("Сохранить SRT", str(error), parent=self)

    def add(self):
        if self.commit():
            start = self.rows[self.active].end if self.active is not None else 0
            index = self.active + 1 if self.active is not None else len(self.rows)
            self.rows.insert(index, Caption(start, start + 1500, "Новая реплика"))
            self.refresh(index)

    def split(self):
        if self.active is None or not self.commit():
            return
        i = self.active
        row = self.rows[i]
        offset = len(self.words.get("1.0", "insert"))
        left, right = row.text[:offset].strip(), row.text[offset:].strip()
        if not left or not right or row.end - row.start < 2:
            messagebox.showinfo("Разделить", "Поставьте курсор в тексте в точку разделения.", parent=self)
            return
        middle = (row.start + row.end) // 2
        self.rows[i:i + 1] = [
            replace(row, end=middle, text=left, reveal=(), speech_duration=None, full_duration=None),
            replace(row, start=middle, text=right, reveal=(), speech_duration=None, full_duration=None),
        ]
        self.refresh(i + 1)

    def merge(self):
        if self.active is not None and self.active + 1 < len(self.rows) and self.commit():
            i = self.active
            row, next_row = self.rows[i:i + 2]
            self.rows[i:i + 2] = [replace(row, start=min(row.start, next_row.start), end=max(row.end, next_row.end),
                                          text=row.text + "\n" + next_row.text, reveal=(),
                                          speech_duration=None, full_duration=None)]
            self.refresh(i)

    def remove(self):
        if self.active is not None:
            i = self.active
            del self.rows[i]
            self.refresh(min(i, len(self.rows) - 1) if self.rows else None)

    def apply_all(self):
        if self.active is not None and self.commit():
            style = caption_appearance(self.rows[self.active])
            self.rows = [replace(row, **style) for row in self.rows]
            self.update_preset_indicator()
            self.status.configure(text="Оформление применено ко всем репликам.")

    def style_target_ready(self):
        if self.active is None:
            messagebox.showinfo("Оформление", "Выберите реплику в редакторе.", parent=self)
            return False
        return self.commit()

    def apply_imported_style(self, style, preset=None, preset_name=""):
        self.rows[self.active] = replace(
            self.rows[self.active], **style, reference_preset=preset, preset_name=preset_name,
        )
        self.show_row(self.active)
        label = caption_preset_label(self.rows[self.active])
        note = f"Пресет «{label}» применён к выбранной реплике." if preset or preset_name else "Оформление применено к выбранной реплике."
        self.status.configure(text=note)

    def clear_reference_style(self):
        if self.style_target_ready():
            self.apply_imported_style({}, preset=None, preset_name="")

    def auto_place_sides(self):
        if not self.rows or not self.commit():
            return
        if not any(row.reference_preset for row in self.rows):
            messagebox.showinfo("Подбор сторон", "Сначала примените JSON-пресет оформления хотя бы к одной реплике.", parent=self)
            return
        try:
            project, timeline = self.app.connect_resolve()
            fps = float(timeline.GetSetting("timelineFrameRate"))
            width = int(timeline.GetSetting("timelineResolutionWidth") or project.GetSetting("timelineResolutionWidth"))
            height = int(timeline.GetSetting("timelineResolutionHeight") or project.GetSetting("timelineResolutionHeight"))
            origin = timeline.GetStartFrame()
            end = timeline.GetEndFrame()
            ordered = sorted(range(len(self.rows)), key=lambda i: self.rows[i].start)
            spans = []
            for idx in ordered:
                row = self.rows[idx]
                first = max(origin, min(end - 1, origin + round(row.start * fps / 1000)))
                last = max(first + 1, min(end, origin + round(row.end * fps / 1000)))
                probe = replace(row, side="auto") if row.reference_preset else row
                spans.append((first, last, probe))
            self.configure(cursor="watch")
            self.status.configure(text="Анализ пустых зон кадра на таймлайне Resolve…")
            self.update_idletasks()
            matte_entries = []
            if self.text_behind_person.get():
                _, matte_entries = prepare_subject_mattes(
                    timeline, int(timeline.GetTrackCount("video") or 0), spans,
                )
            placements = resolve_caption_placements(
                timeline, spans, width, height, matte_entries=matte_entries or None,
            )
            left_count = 0
            right_count = 0
            updated = self.rows[:]
            for idx, place in zip(ordered, placements):
                row = updated[idx]
                if not row.reference_preset:
                    continue
                side = place["side"]
                updated[idx] = replace(row, side=side, x=place["x"], y=place["y"])
                if side == "left":
                    left_count += 1
                else:
                    right_count += 1
            self.rows = updated
            self.show_row(self.active if self.active is not None else 0)
            self.status.configure(
                text=f"Стороны подобраны по композиции кадра: слева {left_count}, справа {right_count}."
            )
        except Exception as error:
            self.status.configure(text="Не удалось подобрать стороны по кадру.")
            messagebox.showerror("Подбор сторон по кадру", str(error), parent=self)
        finally:
            self.configure(cursor="")

    def import_resolve_style(self):
        if not self.style_target_ready():
            return
        try:
            _, timeline = self.app.connect_resolve()
            self.apply_imported_style(read_resolve_text_style(timeline), preset_name="Из Resolve Text+")
        except Exception as error:
            messagebox.showerror("Импорт оформления из Resolve", str(error), parent=self)

    def import_style_preset(self):
        if not self.style_target_ready():
            return
        path = filedialog.askopenfilename(parent=self, title="Импортировать оформление из JSON",
                                          filetypes=[("Настройки оформления JSON", "*.json")])
        if not path:
            return
        try:
            file_path = Path(path)
            data = json.loads(file_path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict) and data.get("id") == PRESET_ID:
                name = str(data.get("title") or "").strip() or file_path.stem
                self.apply_imported_style(validate_style(parse_reference_preset(data)), data, preset_name=name)
                return
            changes = style_from_json(data)
            current = caption_style(self.rows[self.active])
            preset = data.get("reference_preset") if isinstance(data.get("style"), dict) else None
            if preset is not None:
                if not isinstance(preset, dict):
                    raise ValueError("В JSON повреждён пресет с потёртостью.")
                current.update(parse_reference_preset(preset))
            if "font" in changes and "style" not in changes and isinstance(changes["font"], str):
                styles = self.fonts.get(changes["font"], ())
                if styles and current["style"] not in styles:
                    changes["style"] = "Regular" if "Regular" in styles else styles[0]
            name = (
                str(data.get("name") or "").strip()
                or (str(preset.get("title") or "").strip() if isinstance(preset, dict) else "")
                or file_path.name.removesuffix(".whisperx-style.json").removesuffix(".json")
            )
            self.apply_imported_style(validate_style({**current, **changes}), preset, preset_name=name)
        except (OSError, ValueError) as error:
            messagebox.showerror("Импорт оформления из JSON", str(error), parent=self)

    def save_style_preset(self):
        if not self.style_target_ready():
            return
        path = filedialog.asksaveasfilename(parent=self, initialfile="Text+.whisperx-style.json",
                                           defaultextension=".whisperx-style.json", filetypes=STYLE_FILE_TYPE)
        if not path:
            return
        try:
            file_path = Path(path)
            row = self.rows[self.active]
            saved_name = file_path.name.removesuffix(".whisperx-style.json").removesuffix(".json")
            preset = {"version": 1, "name": saved_name, "style": caption_style(row)}
            if row.reference_preset:
                preset["reference_preset"] = row.reference_preset
            file_path.write_text(json.dumps(preset, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            if not row.preset_name:
                self.rows[self.active] = replace(row, preset_name=saved_name)
                self.update_preset_indicator()
            self.status.configure(text=f"Пресет оформления «{saved_name}» сохранён.")
        except OSError as error:
            messagebox.showerror("Сохранить пресет оформления", str(error), parent=self)

    def apply_layout(self):
        if not self.rows or not self.commit():
            return
        try:
            words, lines = int(self.per_line.get()), int(self.line_count.get())
            if not (1 <= words <= 20 and 1 <= lines <= 8):
                raise ValueError("Укажите 1–20 слов в строке и 1–8 строк в титре.")
            grouped = group_captions(self.rows, words, lines)
        except ValueError as error:
            messagebox.showerror("Разбивка", str(error), parent=self)
            return
        self.before_layout = self.rows[:]
        self.rows = grouped
        self.refresh(0 if self.rows else None)
        self.status.configure(text=f"Титров: {len(self.rows)}. При разбиении длинной реплики её время распределено пропорционально.")

    def update_hold_input(self, _event=None):
        self.hold_entry.configure(state="normal" if self.hold_mode.get() == HOLD_MANUAL else "disabled")

    def hold_settings(self):
        mode = self.hold_mode.get()
        if mode != HOLD_MANUAL:
            return mode, None
        try:
            seconds = float(self.hold_seconds.get().strip().replace(",", "."))
            milliseconds = round(seconds * 1000)
            if milliseconds > 0:
                return mode, milliseconds
        except (ValueError, OverflowError):
            pass
        raise ValueError("Введите длительность в секундах. Например, 1,5.")

    def apply_hold(self, selected_only=False):
        if not self.rows or not self.commit():
            return
        try:
            mode, manual_ms = self.hold_settings()
            if selected_only and self.active is None:
                raise ValueError("Выберите блок на редактирование.")
            if mode == HOLD_MANUAL or selected_only:
                targets = {self.active} if selected_only else set(range(len(self.rows)))
            else:
                targets = {i for i, row in enumerate(self.rows) if row.reveal}
            if not targets:
                raise ValueError("Сначала создайте блоки кнопкой «Накопление слов».")
            ordered = sorted(range(len(self.rows)), key=lambda i: self.rows[i].start)
            next_starts = {i: self.rows[j].start for i, j in zip(ordered, ordered[1:])}
            revised = self.rows[:]
            for i in targets:
                row = self.rows[i]
                next_start = next_starts.get(i)
                revised[i] = with_hold(row, mode, manual_ms,
                                       next_start if next_start is not None and next_start > row.start else None)
            self.rows = revised
            self.refresh(self.active)
            self.status.configure(text=f"Длительность обновлена. Блоков: {len(targets)}.")
        except ValueError as error:
            messagebox.showerror("Длительность Text+", str(error), parent=self)

    def apply_word_timing(self, cumulative=False):
        if not self.commit():
            return
        path = self.words_path
        if not path or not path.is_file():
            selected = filedialog.askopenfilename(
                parent=self, title="Выберите JSON WhisperX с таймкодами слов",
                filetypes=[("JSON WhisperX", "*.json")],
            )
            if not selected:
                return
            path = Path(selected)
        try:
            words, skipped = aligned_captions(path, trim_pauses=True)
            if not words:
                raise ValueError("В JSON нет таймкодов слов. Создайте субтитры с включённым выравниванием.")
            if cumulative:
                per_line, lines = int(self.per_line.get()), int(self.line_count.get())
                if not (1 <= per_line <= 20 and 1 <= lines <= 8):
                    raise ValueError("Укажите 1–20 слов в строке и 1–8 строк в титре.")
                hold, manual_ms = self.hold_settings()
            if self.rows:
                reference = self.rows[self.active if self.active is not None else 0]
                style = caption_appearance(reference)
                words = [replace(word, **style) for word in words]
            self.words_path = path
            if self.before_words is None:
                self.before_words = (self.rows[:], self.before_layout, self.active)
            self.before_layout = None
            self.undo_words_button.configure(state="normal")
            self.rows = cumulative_captions(words, per_line, lines, hold, manual_ms) if cumulative else words
            self.refresh(0)
            self.status.configure(text=f"Титров: {len(self.rows)} · Слов по таймкодам WhisperX: {len(words)} · Пропущено: {skipped}")
        except (OSError, ValueError, KeyError, TypeError) as error:
            messagebox.showerror("По одному слову", str(error), parent=self)

    def undo_words(self):
        if self.before_words is None:
            return
        self.rows, self.before_layout, index = self.before_words
        self.before_words = None
        self.undo_words_button.configure(state="disabled")
        self.refresh((index if index is not None else 0) if self.rows else None)
        self.status.configure(text="Режим по словам отменён. Предыдущие реплики восстановлены.")

    def undo_layout(self):
        if self.before_layout is not None:
            self.rows, self.before_layout = self.before_layout, None
            self.refresh(0 if self.rows else None)
            self.status.configure(text="Предыдущая разбивка восстановлена.")

    def export(self):
        if not self.commit() or not self.rows:
            return
        try:
            project, timeline = self.app.connect_resolve()
            if project.IsRenderingInProgress():
                raise RuntimeError("Дождитесь завершения рендера в Resolve.")
            if not self.app.resolve_app().OpenPage("edit"):
                raise RuntimeError("Не удалось открыть страницу монтажа в Resolve.")
            self.configure(cursor="watch")
            self.status.configure(text=f"Добавление Text+ на таймлайн «{timeline.GetName()}»…")
            self.update_idletasks()
            def update_status(message):
                self.status.configure(text=message)
                self.update_idletasks()
            name = export_titles(
                project, timeline, self.rows,
                behind_person=self.text_behind_person.get(),
                status_callback=update_status,
            )
            self.status.configure(text=f"Готово. Таймлайн Resolve «{name}».")
        except Exception as error:
            self.status.configure(text="Экспорт Text+ прерван.")
            messagebox.showerror("Создать Text+", str(error), parent=self)
        finally:
            self.configure(cursor="")
