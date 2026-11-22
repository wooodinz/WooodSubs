"""Import and render the reference Text+ preset as editable Fusion nodes."""

import json
import math
import os
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path


PRESET_ID = "resolve_reference_distressed"
MELTING_PRESET_ID = "css_melting_text"
SUPPORTED_PRESET_IDS = (PRESET_ID, MELTING_PRESET_ID)
VARIANT_MOTIONS = {"stamp", "paper", "film", "outline_fill", "masked_rise"}
SIDES = ("left", "right")
SIDE_MODES = ("auto", "left", "right")
TEXT_INPUTS = {
    "VerticalTopCenterBottom", "CharacterSpacingClone", "CharacterSizeX", "Alpha1",
    "Start", "End", "Enabled2", "Enabled3", "Enabled4",
}
EFFECT_INPUTS = {
    "AnimatedInkWear": ("FastNoise", {"Detail", "Contrast", "Brightness", "XScale", "LockXY",
                                    "YScale", "Color1Alpha", "Color2Alpha", "Discontinuous"}),
    "DistressLettering": ("ChannelBoolean", {"Operation", "ToRed", "ToGreen", "ToBlue", "ToAlpha"}),
    "ReadingShadow": ("Shadow", {"ShadowOffset", "Softness", "Alpha", "OutputMode", "Red", "Green", "Blue"}),
}
SAMPLE_WIDTH = 96
SAMPLE_HEIGHT = 54
BATCH_FPS = 2.0
BATCH_MAX_SPAN_SEC = 180.0
SHOT_CUT_DIFF_THRESHOLD = 0.12
FFMPEG_FALLBACK = (
    Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe" / "ffmpeg-9.1-full_build" / "bin" / "ffmpeg.exe"
)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Параметры пресета должны содержать конечные числа.")
    return value


def point(values):
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError("В пресете ожидались три координаты точки.")
    return {i: number(value) for i, value in enumerate(values, 1)}


def rgb(inputs):
    values = [number(inputs[key]) for key in ("Red1", "Green1", "Blue1")]
    if any(not 0 <= value <= 1 for value in values):
        raise ValueError("Компоненты цвета пресета должны быть от 0 до 1.")
    return values


def hex_color(values):
    return "#" + "".join(f"{round(value * 255):02X}" for value in values)


def side_selection_config(document):
    layout = document.get("layout") or {}
    cfg = layout.get("side_selection")
    if cfg is None:
        return {}
    if not isinstance(cfg, dict):
        raise ValueError("Секция layout.side_selection должна быть объектом.")
    if "allowed_sides" in cfg:
        allowed = cfg["allowed_sides"]
        if not isinstance(allowed, list) or not allowed or any(s not in SIDES for s in allowed):
            raise ValueError("В layout.side_selection.allowed_sides указаны недопустимые стороны.")
    if "default_side" in cfg and cfg["default_side"] not in SIDES:
        raise ValueError("В layout.side_selection.default_side указана недопустимая сторона.")
    rules = cfg.get("composition_rules")
    if rules is not None:
        if not isinstance(rules, dict):
            raise ValueError("Секция composition_rules должна быть объектом.")
        if "center_deadzone_x" in rules:
            dz = rules["center_deadzone_x"]
            if not isinstance(dz, list) or len(dz) != 2:
                raise ValueError("center_deadzone_x должен содержать два числа.")
            lo, hi = number(dz[0]), number(dz[1])
            if not 0 <= lo <= hi <= 1:
                raise ValueError("center_deadzone_x должен лежать в диапазоне 0..1.")
        for key in ("subject_on_left_place_caption", "subject_on_right_place_caption"):
            if key in rules and rules[key] not in SIDES:
                raise ValueError(f"В {key} указана недопустимая сторона.")
        if "max_consecutive_same_side_when_centered" in rules:
            limit = rules["max_consecutive_same_side_when_centered"]
            if type(limit) is not int or limit < 1:
                raise ValueError("max_consecutive_same_side_when_centered должен быть целым >= 1.")
    fallback = cfg.get("fallback")
    if fallback is not None:
        if not isinstance(fallback, dict):
            raise ValueError("Секция fallback должна быть объектом.")
        if "sequence" in fallback:
            seq = fallback["sequence"]
            if not isinstance(seq, list) or not seq or any(s not in SIDES for s in seq):
                raise ValueError("В fallback.sequence указаны недопустимые стороны.")
    return cfg


def _hex_to_rgb01(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value.strip()):
        raise ValueError(f"Некорректный цвет градиента: {value!r}")
    s = value.strip()
    return [int(s[1:3], 16) / 255.0, int(s[3:5], 16) / 255.0, int(s[5:7], 16) / 255.0]


def parse_melting_preset(document):
    if document.get("id") != MELTING_PRESET_ID or document.get("schema_version") != 1:
        raise ValueError("Версия пресета Melting Text не поддерживается.")
    try:
        text = document["text"]
        font = text["font"]
        style = text["style"]
        if not isinstance(font, str) or not font.strip() or not isinstance(style, str) or not style.strip():
            raise ValueError("В пресете Melting Text отсутствует шрифт или начертание.")
        size = number(text["size"])
        x = number(text["x"])
        y = number(text["y"])
        if not (0 < size <= 1 and 0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("Размер и координаты Melting Text должны быть в диапазоне 0..1.")
        grad = document["gradient"]
        if not isinstance(grad, list) or len(grad) < 2:
            raise ValueError("В пресете Melting Text градиент должен содержать минимум 2 цвета.")
        for c in grad:
            _hex_to_rgb01(c)
        motion = document["motion"]
        if number(motion["period_seconds"]) <= 0:
            raise ValueError("period_seconds должен быть больше нуля.")
        number(motion["drop_pixels"])
        number(motion["blur_pixels"])
        number(motion["blurred_drip_opacity"])
        for pair_key in ("drip_scale_y", "drip_opacity"):
            pair = motion[pair_key]
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError(f"Поле {pair_key} должно содержать два числа.")
            number(pair[0])
            number(pair[1])
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("JSON пресета Melting Text заполнен не полностью.") from error
    return {
        "font": font.strip(),
        "style": style.strip(),
        "color": grad[0].strip().upper(),
        "size": float(size),
        "x": float(x),
        "y": float(y),
        "side": "auto",
    }


def parse_reference_preset(document):
    if isinstance(document, dict) and document.get("id") == MELTING_PRESET_ID:
        return parse_melting_preset(document)
    if document.get("id") != PRESET_ID or document.get("schema_version") != 1:
        raise ValueError("Версия пресета с потёртостью не поддерживается.")
    try:
        text = document["text_plus"]
        inputs = text["inputs"]
        if not all(isinstance(inputs[key], str) and inputs[key].strip() for key in ("Font", "Style")):
            raise ValueError("В пресете отсутствует шрифт или начертание.")
        for key in TEXT_INPUTS & inputs.keys():
            number(inputs[key])
        for role in ("supporting_line", "accent_line"):
            number(text[role]["size"])
            rgb(text[role]["inputs"])
        accent = text["accent_line"]
        number(accent["short_size"])
        number(accent["short_max_characters"])
        layout = document["layout"]
        for key in ("top_y", "cap_height_factor", "line_gap_y", "max_width_fraction"):
            number(layout[key])
        side_cfg = side_selection_config(document)
        default_side = side_cfg.get("default_side", layout.get("side", document.get("example", {}).get("side", "right")))
        if default_side not in SIDES:
            raise ValueError("В пресете указана неизвестная сторона кадра.")
        sides_to_check = set(SIDES) if side_cfg else {default_side}
        for s in sides_to_check:
            for key in ("x", "HorizontalLeftCenterRight"):
                number(layout[s][key])
        opacity = number(document["reveal"]["first_visible_frame_opacity"])
        if not 0 <= opacity <= 1:
            raise ValueError("Непрозрачность первого кадра должна быть от 0 до 1.")
        motion = document.get("line_motion")
        if motion is not None:
            if not isinstance(motion, dict) or motion.get("type") not in VARIANT_MOTIONS:
                raise ValueError("В пресете указана неизвестная анимация строки.")
            frames = motion.get("frames")
            if type(frames) is not int or not 1 <= frames <= 30:
                raise ValueError("Анимация строки должна занимать от 1 до 30 кадров.")
            if motion["type"] in {"stamp", "film", "masked_rise"}:
                if not 0 <= number(motion["amount"]) <= 0.5:
                    raise ValueError("Амплитуда анимации должна быть от 0 до 0,5.")
            if motion["type"] == "paper":
                rgb(motion["paper"])
        for name, (tool_id, allowed) in EFFECT_INPUTS.items():
            spec = document["fusion"]["nodes"][name]
            if spec["type"] != tool_id:
                raise ValueError("В пресете указан неподдерживаемый узел Fusion.")
            for key in allowed & spec["inputs"].keys():
                (point if key == "ShadowOffset" else number)(spec["inputs"][key])
        noise_parameters(document)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("JSON пресета с потёртостью заполнен не полностью.") from error
    initial_side = "auto" if side_cfg.get("mode") == "frame_composition" else default_side
    return {
        "font": inputs["Font"], "style": inputs["Style"],
        "color": hex_color(rgb(text["supporting_line"]["inputs"])),
        "size": text["supporting_line"]["size"],
        "x": layout[default_side]["x"], "y": layout["top_y"],
        "side": initial_side,
    }


def noise_parameters(preset):
    noise = preset["fusion"]["nodes"]["AnimatedInkWear"]
    step = re.fullmatch(r"floor\(time / (\d+)\) \* ([\d.]+) \+ \{seed\}", noise["expression"])
    seed = re.fullmatch(r"caption_index \* ([\d.]+)", noise["seed_formula"])
    if not step or not seed or int(step[1]) < 1:
        raise ValueError("Пресет использует неподдерживаемый рисунок анимации потёртости.")
    return int(step[1]), number(float(step[2])), number(float(seed[1]))


def find_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    if FFMPEG_FALLBACK.is_file():
        return str(FFMPEG_FALLBACK)
    return None


def _clip_source_range(clip, media_pool_item):
    start_time = None
    end_time = None
    get_src_start = getattr(clip, "GetSourceStartTime", None)
    get_src_end = getattr(clip, "GetSourceEndTime", None)
    if get_src_start and get_src_end:
        try:
            s_val = float(get_src_start())
            e_val = float(get_src_end())
            if math.isfinite(s_val) and math.isfinite(e_val) and e_val > s_val >= 0:
                start_time, end_time = s_val, e_val
        except (TypeError, ValueError):
            pass
    if start_time is None:
        try:
            fps = float(media_pool_item.GetClipProperty("FPS") or 0)
            left_off = float(clip.GetLeftOffset() or 0)
            dur = float(clip.GetDuration() or 0)
            if fps > 0 and dur > 0:
                start_time = max(0.0, left_off / fps)
                end_time = start_time + dur / fps
        except (TypeError, ValueError):
            return None
    if start_time is None or end_time is None:
        return None
    return start_time, end_time


def _collect_video_clips(timeline):
    """Collect playable video clips ordered from highest track down to V1."""
    clips = []
    try:
        track_count = int(timeline.GetTrackCount("video") or 0)
    except (TypeError, ValueError):
        return clips
    for track in range(track_count, 0, -1):
        try:
            if hasattr(timeline, "GetIsTrackEnabled") and not timeline.GetIsTrackEnabled("video", track):
                continue
            items = timeline.GetItemListInTrack("video", track) or []
        except Exception:
            continue
        for item in items:
            try:
                mp = item.GetMediaPoolItem()
                if not mp:
                    continue
                path = mp.GetClipProperty("File Path")
                if not path or not os.path.isfile(path):
                    continue
                clip_start = int(item.GetStart())
                clip_end = int(item.GetEnd())
                if clip_end <= clip_start:
                    continue
                src_range = _clip_source_range(item, mp)
                if not src_range:
                    continue
                clips.append((track, clip_start, clip_end, path, src_range[0], src_range[1]))
            except Exception:
                continue
    return clips


def _sample_time_for_frame(clips, frame):
    """Map a timeline frame to (video_path, source_time_sec) from the highest visible video track."""
    for _track, c_start, c_end, path, src_start, src_end in clips:
        if c_start <= frame < c_end:
            ratio = (frame - c_start) / (c_end - c_start)
            return path, src_start + ratio * (src_end - src_start)
    return None


def _decode_frames_for_requests(requests):
    """Decode sampled RGB24 frames (SAMPLE_WIDTH x SAMPLE_HEIGHT) via ffmpeg.

    requests: dict[int, tuple[str, float]] mapping caption_index -> (video_path, source_time_sec)
    returns: dict[int, bytes]
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg or not requests:
        return {}
    frame_bytes = SAMPLE_WIDTH * SAMPLE_HEIGHT * 3
    by_video = defaultdict(list)
    for idx, (path, t_sec) in requests.items():
        by_video[path].append((idx, max(0.0, t_sec)))
    decoded = {}
    for path, entries in by_video.items():
        times = [t for _, t in entries]
        t_min, t_max = min(times), max(times)
        span = t_max - t_min
        if len(entries) >= 4 and span <= BATCH_MAX_SPAN_SEC:
            pad_start = max(0.0, t_min - 0.25)
            pad_dur = span + 0.75
            cmd = [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-ss", f"{pad_start:.3f}", "-t", f"{pad_dur:.3f}",
                "-i", path,
                "-vf", f"fps={BATCH_FPS},scale={SAMPLE_WIDTH}:{SAMPLE_HEIGHT}",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, check=True, timeout=45, creationflags=NO_WINDOW)
                n_frames = len(proc.stdout) // frame_bytes
                if n_frames > 0:
                    for idx, t_sec in entries:
                        fi = int(round((t_sec - pad_start) * BATCH_FPS))
                        fi = max(0, min(n_frames - 1, fi))
                        decoded[idx] = proc.stdout[fi * frame_bytes:(fi + 1) * frame_bytes]
                    continue
            except Exception:
                pass
        for idx, t_sec in entries:
            cmd = [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-ss", f"{t_sec:.3f}", "-i", path,
                "-frames:v", "1",
                "-vf", f"scale={SAMPLE_WIDTH}:{SAMPLE_HEIGHT}",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, check=True, timeout=15, creationflags=NO_WINDOW)
                if len(proc.stdout) >= frame_bytes:
                    decoded[idx] = proc.stdout[:frame_bytes]
            except Exception:
                continue
    return decoded


def analyze_frame_composition(rgb_bytes, width=SAMPLE_WIDTH, height=SAMPLE_HEIGHT):
    """Estimate horizontal subject center-of-mass (0..1) and left/right visual busyness."""
    n = width * height
    luma = [0.0] * n
    skin = [0.0] * n
    for i in range(n):
        r = rgb_bytes[i * 3]
        g = rgb_bytes[i * 3 + 1]
        b = rgb_bytes[i * 3 + 2]
        y = 0.299 * r + 0.587 * g + 0.114 * b
        luma[i] = y
        cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
        cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
        if 40.0 < y < 235.0 and 77.0 <= cb <= 127.0 and 133.0 <= cr <= 173.0:
            skin[i] = 1.0

    edge = [0.0] * n
    for y in range(1, height - 1):
        row = y * width
        for x in range(1, width - 1):
            idx = row + x
            gx = abs(luma[idx + 1] - luma[idx - 1])
            gy = abs(luma[idx + width] - luma[idx - width])
            edge[idx] = (gx + gy) / 510.0

    y0, y1 = int(height * 0.12), int(height * 0.78)
    x0, x1 = int(width * 0.08), int(width * 0.92)
    face_split = int(height * 0.58)
    total_w = 0.0
    weighted_x = 0.0
    left_busy = 0.0
    right_busy = 0.0
    for y in range(y0, y1):
        row = y * width
        y_weight = 1.4 if y < face_split else 0.75
        for x in range(x0, x1):
            idx = row + x
            e = edge[idx]
            loc_e = (e + edge[idx - 1] + edge[idx + 1] + edge[idx - width] + edge[idx + width]) * 0.2
            sharp_skin = skin[idx] * min(1.0, loc_e * 8.0)
            saliency = y_weight * (e * 1.2 + sharp_skin * 3.5)
            nx = (x + 0.5) / width
            total_w += saliency
            weighted_x += saliency * nx
            if nx < 0.45:
                left_busy += saliency
            elif nx > 0.55:
                right_busy += saliency

    cx = (weighted_x / total_w) if total_w > 1e-6 else 0.5
    return cx, left_busy, right_busy


def _frame_diff(buf_a, buf_b):
    step = 12
    total = 0
    count = 0
    for i in range(0, min(len(buf_a), len(buf_b)), step):
        total += abs(buf_a[i] - buf_b[i])
        count += 1
    return total / (count * 255.0) if count else 0.0


def resolve_caption_sides(timeline, spans):
    """Resolve 'left' or 'right' for each (first_frame, last_frame, caption) span.

    Priority matches layout.side_selection:
      1. Manual override on caption.side ('left' or 'right')
      2. Frame composition analysis (opposite subject / negative space + shot stability)
      3. Fallback alternation sequence (when frame analysis is unavailable)
    """
    auto_requests = {}
    clips = None
    for idx, (first, last, caption) in enumerate(spans):
        preset = caption.reference_preset
        if not preset:
            continue
        manual_side = getattr(caption, "side", "auto")
        if manual_side in SIDES:
            continue
        side_cfg = side_selection_config(preset)
        if side_cfg.get("mode") != "frame_composition":
            continue
        if clips is None:
            clips = _collect_video_clips(timeline) if timeline is not None else []
        if not clips:
            continue
        rules = side_cfg.get("composition_rules") or {}
        sample_mode = rules.get("sample_frame", "caption_midpoint_or_start")
        sample_frame = first if sample_mode == "caption_start" else (first + last) // 2
        located = _sample_time_for_frame(clips, sample_frame)
        if located is None and sample_frame != first:
            located = _sample_time_for_frame(clips, first)
        if located is not None:
            auto_requests[idx] = located

    frames = _decode_frames_for_requests(auto_requests) if auto_requests else {}

    resolved = []
    prev_side = None
    prev_frame = None
    same_count = 0
    fallback_step = 0

    for idx, (_first, _last, caption) in enumerate(spans):
        preset = caption.reference_preset
        if not preset:
            side = "left" if caption.x < 0.5 else "right"
            resolved.append(side)
            continue

        side_cfg = side_selection_config(preset)
        default_side = side_cfg.get(
            "default_side",
            preset.get("layout", {}).get("side", preset.get("example", {}).get("side", "right")),
        )
        manual_side = getattr(caption, "side", "auto")
        if manual_side in SIDES:
            side = manual_side
            prev_frame = frames.get(idx, prev_frame)
        elif side_cfg.get("mode") == "frame_composition" and idx in frames:
            buf = frames[idx]
            rules = side_cfg.get("composition_rules") or {}
            dz = rules.get("center_deadzone_x", [0.44, 0.56])
            dz_lo, dz_hi = float(dz[0]), float(dz[1])
            on_left = rules.get("subject_on_left_place_caption", "right")
            on_right = rules.get("subject_on_right_place_caption", "left")
            keep_same_shot = bool(rules.get("keep_side_within_same_shot", True))
            prefer_open = bool(rules.get("prefer_open_background", True))
            max_same = int(rules.get("max_consecutive_same_side_when_centered", 4))

            cx, left_busy, right_busy = analyze_frame_composition(buf)
            is_cut = prev_frame is None or _frame_diff(prev_frame, buf) > SHOT_CUT_DIFF_THRESHOLD
            busy_ratio = (left_busy - right_busy) / max(left_busy + right_busy, 1e-6)

            if cx < dz_lo:
                side = on_left
                clear_subject = True
            elif cx > dz_hi:
                side = on_right
                clear_subject = True
            else:
                clear_subject = False
                if prefer_open and abs(busy_ratio) >= 0.14:
                    side = on_left if busy_ratio > 0 else on_right
                elif prev_side is None:
                    side = default_side
                elif is_cut or same_count >= max_same:
                    side = "left" if prev_side == "right" else "right"
                else:
                    side = prev_side

            if (
                keep_same_shot
                and not is_cut
                and not clear_subject
                and prev_side is not None
                and side != prev_side
                and same_count < max_same
            ):
                side = prev_side
            prev_frame = buf
        else:
            fallback = side_cfg.get("fallback") or {}
            if fallback.get("when_no_frame_analysis") == "alternate":
                seq = fallback.get("sequence") or [default_side, "left" if default_side == "right" else "right"]
                side = seq[fallback_step % len(seq)]
                fallback_step += 1
            else:
                side = default_side if manual_side == "auto" else ("left" if caption.x < 0.5 else "right")

        same_count = 1 if side != prev_side else same_count + 1
        prev_side = side
        resolved.append(side)

    return resolved


def connect(tool, input_name, source):
    if not tool.ConnectInput(input_name, source):
        raise RuntimeError(f"Resolve не удалось подключить {tool.Name}.{input_name}.")


def set_inputs(tool, values, frame):
    for key, value in values.items():
        tool.SetInput(key, value, frame)


def animate_variant(comp, node, values, bounds, motion, onset, frame, width, height, opacity, accent):
    kind = motion["type"]
    duration = motion["frames"]
    age = f"(time - ({onset}))"
    progress = f"min(1, max(0, {age} / {duration}))"
    rest = f"(1 - {progress})"
    x, y = values["Center"][1], values["Center"][2]
    if kind == "stamp":
        node.Size.SetExpression(f"{values['Size']} * (1 + {motion['amount']} * {rest} ^ 2)")
    elif kind == "film":
        amount = motion["amount"]
        node.Center.SetExpression(
            f"Point({x} + {amount} * sin({age} * 2.4) * {rest}, "
            f"{y} + {amount} * cos({age} * 1.7) * {rest})"
        )
        ghost = comp.AddTool("TextPlus", False, 2, 1)
        set_inputs(ghost, {**values, "Center": {1: x - amount, 2: y + amount / 2, 3: 0}}, frame)
        ghost.Opacity1.SetExpression(f"iif(time < {onset}, 0, 0.32 * max(0, 1 - {age} / 3))")
        merge = comp.AddTool("Merge", False, 3, 1)
        connect(merge, "Background", ghost)
        connect(merge, "Foreground", node)
        return merge
    elif kind == "masked_rise":
        cutoff = max(0, y - values["Size"] * width / height * .27)
        mask = comp.AddTool("RectangleMask", False, 2, 1)
        set_inputs(mask, {"Center": {1: .5, 2: (1 + cutoff) / 2}, "Width": 1,
                          "Height": 1 - cutoff}, frame)
        connect(node, "EffectMask", mask)
        node.Center.SetExpression(f"Point({x}, {y} - {motion['amount']} * {rest} ^ 2)")
    elif kind == "outline_fill" and accent:
        outline = comp.AddTool("TextPlus", False, 2, 1)
        set_inputs(outline, {**values, "Enabled1": 0, "Enabled2": 1, "ElementShape2": 1,
                             "Thickness2": .026, "Alpha2": 1, "Red2": values["Red1"],
                             "Green2": values["Green1"], "Blue2": values["Blue1"]}, frame)
        outline.Opacity2.SetExpression(f"iif(time < {onset}, 0, 1)")
        node.End.SetExpression(progress)
        merge = comp.AddTool("Merge", False, 3, 1)
        connect(merge, "Background", outline)
        connect(merge, "Foreground", node)
        return merge
    elif kind == "paper" and accent:
        left, right = (bounds[1] / width, bounds[3] / width) if bounds and 1 in bounds and 3 in bounds else (x, x)
        padding = .014
        paper_width = min(.63, max(.13, right - left + padding * 2))
        paper_height = values["Size"] * width / height * .65
        direction = values["HorizontalLeftCenterRight"]
        paper_x = x - direction * (paper_width / 2 - padding)
        paper_y = y
        mask = comp.AddTool("RectangleMask", False, 2, 1)
        set_inputs(mask, {"Width": paper_width, "Height": paper_height, "Angle": -2,
                          "Center": {1: paper_x, 2: paper_y}}, frame)
        mask.Center.SetExpression(f"Point({paper_x} + {direction} * .025 * {rest} ^ 2, {paper_y})")
        node.Center.SetExpression(f"Point({x} + {direction} * .025 * {rest} ^ 2, {y})")
        paper = comp.AddTool("Background", False, 2, 2)
        red, green, blue = rgb(motion["paper"])
        set_inputs(paper, {"TopLeftRed": red, "TopLeftGreen": green, "TopLeftBlue": blue}, frame)
        paper.TopLeftAlpha.SetExpression(f"iif(time < {onset}, 0, iif(time == {onset}, {opacity}, 1))")
        connect(paper, "EffectMask", mask)
        merge = comp.AddTool("Merge", False, 3, 1)
        connect(merge, "Background", paper)
        connect(merge, "Foreground", node)
        return merge
    return node


def render_melting_style(comp, template, row, reveals, context):
    preset = row.reference_preset
    parse_melting_preset(preset)
    if len(context) >= 6:
        width, height, _caption_index, elapsed, _total_frames, _resolved = context[:6]
    else:
        width, height, _caption_index, elapsed, _total_frames = context

    text_cfg = preset["text"]
    motion = preset["motion"]
    grad = preset["gradient"]
    frame = int(comp.GetAttrs().get("COMPN_GlobalStart", 0))

    raw_text = row.text.upper() if text_cfg.get("uppercase") else row.text
    side = getattr(row, "side", "auto")
    h_align = -1 if side == "left" else (1 if side == "right" else 0)

    set_inputs(template, {
        "StyledText": raw_text,
        "Font": row.font,
        "Style": row.style,
        "Size": row.size,
        "Center": {1: row.x, 2: row.y, 3: 0},
        "HorizontalLeftCenterRight": h_align,
        "Red1": 1.0, "Green1": 1.0, "Blue1": 1.0, "Alpha1": 1.0,
    }, frame)
    if template.GetInput("StyledText", frame) != raw_text:
        raise RuntimeError("Resolve не удалось записать текст Melting Text.")

    if reveals:
        progress = {0: 0.0}
        for r_frame, prefix in reveals:
            if r_frame >= 0:
                progress[max(0, r_frame)] = len(prefix) / max(1, len(raw_text))
        template.End = comp.BezierSpline()
        prev_val = progress[0]
        for r_frame, val in progress.items():
            if r_frame:
                template.SetInput("End", prev_val, frame + r_frame - 1)
            template.SetInput("End", val, frame + r_frame)
            prev_val = val

    # Gradient fill masked by TextPlus alpha
    c0 = _hex_to_rgb01(grad[0])
    c1 = _hex_to_rgb01(grad[1])
    c2 = _hex_to_rgb01(grad[2]) if len(grad) >= 3 else c1
    grad_bg = comp.AddTool("Background", False, 2, 1)
    grad_bg.SetAttrs({"TOOLS_Name": "MeltingGradient"})
    set_inputs(grad_bg, {
        "Type": "Four Corner",
        "TopLeftRed": c0[0], "TopLeftGreen": c0[1], "TopLeftBlue": c0[2], "TopLeftAlpha": 1.0,
        "TopRightRed": c2[0], "TopRightGreen": c2[1], "TopRightBlue": c2[2], "TopRightAlpha": 1.0,
        "BottomLeftRed": c1[0], "BottomLeftGreen": c1[1], "BottomLeftBlue": c1[2], "BottomLeftAlpha": 1.0,
        "BottomRightRed": c1[0], "BottomRightGreen": c1[1], "BottomRightBlue": c1[2], "BottomRightAlpha": 1.0,
    }, frame)
    connect(grad_bg, "EffectMask", template)

    fps = 24.0
    period_frames = max(1.0, float(motion["period_seconds"]) * fps)
    drop_norm = float(motion["drop_pixels"]) / max(1.0, float(height))
    sy0, sy1 = float(motion["drip_scale_y"][0]), float(motion["drip_scale_y"][1])
    op0, op1 = float(motion["drip_opacity"][0]), float(motion["drip_opacity"][1])
    blur_px = float(motion["blur_pixels"])
    blur_op = float(motion["blurred_drip_opacity"])

    wave_expr = f"((1 - cos(2 * pi * (time + {elapsed}) / {period_frames:.3f})) / 2)"

    # Layer 1: Sharp dripping copy
    drip_xf = comp.AddTool("Transform", False, 3, 2)
    drip_xf.SetAttrs({"TOOLS_Name": "MeltingDrip"})
    set_inputs(drip_xf, {"UseSizeAndAspect": 0, "Pivot": {1: row.x, 2: row.y}}, frame)
    drip_xf.Center.SetExpression(f"Point(0.5, 0.5 - {drop_norm:.6f} * {wave_expr})")
    drip_xf.YSize.SetExpression(f"{sy0:.4f} + ({sy1 - sy0:.4f}) * {wave_expr}")
    connect(drip_xf, "Input", grad_bg)

    # Layer 2: Blurred dripping glow copy
    blur_node = comp.AddTool("Blur", False, 3, 3)
    blur_node.SetAttrs({"TOOLS_Name": "MeltingBlur"})
    set_inputs(blur_node, {"XBlurSize": blur_px, "YBlurSize": blur_px}, frame)
    connect(blur_node, "Input", drip_xf)

    blur_gain = comp.AddTool("BrightnessContrast", False, 4, 3)
    blur_gain.SetAttrs({"TOOLS_Name": "MeltingBlurOpacity"})
    set_inputs(blur_gain, {"Alpha": 1, "Gain": blur_op}, frame)
    connect(blur_gain, "Input", blur_node)

    # Composite: Blurred Drip -> Sharp Drip -> Crisp Main Text -> MediaOut
    merge_drip = comp.AddTool("Merge", False, 5, 2)
    merge_drip.SetAttrs({"TOOLS_Name": "MergeMeltingDrip"})
    merge_drip.Blend.SetExpression(f"{op0:.4f} + ({op1 - op0:.4f}) * {wave_expr}")
    connect(merge_drip, "Background", blur_gain)
    connect(merge_drip, "Foreground", drip_xf)

    merge_main = comp.AddTool("Merge", False, 6, 1)
    merge_main.SetAttrs({"TOOLS_Name": "MergeMeltingMain"})
    connect(merge_main, "Background", merge_drip)
    connect(merge_main, "Foreground", grad_bg)

    output = next(node for node in comp.GetToolList(False).values() if node.ID == "MediaOut")
    connect(output, "Input", merge_main)


def render_reference_style(comp, template, row, reveals, context):
    preset = row.reference_preset
    if isinstance(preset, dict) and preset.get("id") == MELTING_PRESET_ID:
        render_melting_style(comp, template, row, reveals, context)
        return
    defaults = parse_reference_preset(preset)
    if len(context) >= 6:
        width, height, caption_index, elapsed, total_frames, resolved_placement = context[:6]
    else:
        width, height, caption_index, elapsed, total_frames = context
        resolved_placement = None
    text_style = preset["text_plus"]
    layout = preset["layout"]
    row_side = getattr(row, "side", "auto")
    top = row.y
    h_align = None
    if isinstance(resolved_placement, dict):
        side = resolved_placement.get("side", "right")
        if side not in SIDES:
            side = "left" if resolved_placement.get("x", row.x) < 0.5 else "right"
        if row_side == "auto":
            x_pos = resolved_placement["x"]
            top = resolved_placement.get("y", row.y)
            h_align = resolved_placement.get("h_align")
        else:
            side = row_side
            x_pos = row.x
    else:
        if resolved_placement in SIDES:
            side = resolved_placement
        elif row_side in SIDES:
            side = row_side
        else:
            side = "left" if row.x < .5 else "right"
        x_pos = layout[side]["x"] if row_side == "auto" else row.x
    if h_align is None:
        h_align = layout[side]["HorizontalLeftCenterRight"]
    frame = int(comp.GetAttrs().get("COMPN_GlobalStart", 0))
    common = {key: text_style["inputs"][key] for key in TEXT_INPUTS if key in text_style["inputs"]}
    common.update(Font=row.font, Style=row.style, Start=0, End=1,
                  HorizontalLeftCenterRight=h_align)
    lines = [line.strip() for line in row.text.splitlines() if line.strip()]
    stack = None
    word_index = 0
    motion = preset.get("line_motion")
    for i, line in enumerate(lines):
        node = template if i == 0 else comp.AddTool("TextPlus", False, 1, i + 1)
        role = text_style["accent_line" if i == 0 else "supporting_line"]
        text = line.upper() if text_style["uppercase"] else line
        size = role["size"]
        if i == 0 and len(text) <= role["short_max_characters"]:
            size = role["short_size"]
        size *= row.size / defaults["size"]
        color = rgb(role["inputs"])
        if i > 0 and row.color.upper() != defaults["color"]:
            color = [int(row.color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        values = {**common, "StyledText": text, "Size": size, "Opacity1": 1,
                  **dict(zip(("Red1", "Green1", "Blue1"), color))}
        height_in_frame = size * layout["cap_height_factor"] * width / height
        values["Center"] = {1: x_pos, 2: top - height_in_frame / 2, 3: 0}
        set_inputs(node, values, frame)
        if node.GetInput("StyledText", frame) != text:
            raise RuntimeError("Resolve не удалось записать текст строки.")
        bounds = node.Output.GetDoD(frame)
        if bounds and 1 in bounds and 3 in bounds:
            natural_width = bounds[3] - bounds[1]
            max_width = layout["max_width_fraction"] * width
            if natural_width > max_width:
                values["CharacterSizeX"] = common.get("CharacterSizeX", 1) * max_width / natural_width
                node.SetInput("CharacterSizeX", values["CharacterSizeX"], frame)
                bounds = node.Output.GetDoD(frame)
        if word_index < len(reveals):
            onset = reveals[word_index][0]
        else:
            onset = min(total_frames - 1, round(i * total_frames / len(lines))) - elapsed
        onset = frame + onset
        opacity = preset["reveal"]["first_visible_frame_opacity"]
        node.Opacity1.SetExpression(f"iif(time < {onset}, 0, iif(time == {onset}, {opacity}, 1))")
        line_output = (animate_variant(comp, node, values, bounds, motion, onset, frame,
                                       width, height, opacity, i == 0) if motion else node)
        if stack is not None:
            merge = comp.AddTool("Merge", False, 3, i + 1)
            connect(merge, "Background", stack)
            connect(merge, "Foreground", line_output)
            stack = merge
        else:
            stack = line_output
        word_index += len(line.split())
        top -= height_in_frame + layout["line_gap_y"]

    effects = {}
    for index, (name, (tool_id, allowed)) in enumerate(EFFECT_INPUTS.items()):
        node = comp.AddTool(tool_id, False, 5 + index, 2)
        node.SetAttrs({"TOOLS_Name": name})
        values = preset["fusion"]["nodes"][name]["inputs"]
        set_inputs(node, {key: point(value) if key == "ShadowOffset" else value
                          for key, value in values.items() if key in allowed}, frame)
        effects[name] = node
    period, step, seed_step = noise_parameters(preset)
    seed = round(caption_index * seed_step, 8)
    effects["AnimatedInkWear"].Seethe.SetExpression(f"floor((time + {elapsed}) / {period}) * {step} + {seed}")
    output = next(node for node in comp.GetToolList(False).values() if node.ID == "MediaOut")
    connect(effects["DistressLettering"], "Background", stack)
    connect(effects["DistressLettering"], "Foreground", effects["AnimatedInkWear"])
    connect(effects["ReadingShadow"], "Input", effects["DistressLettering"])
    connect(output, "Input", effects["ReadingShadow"])


WHISPERX_PYTHON = Path.home() / "AppData" / "Local" / "Programs" / "WhisperX" / "Scripts" / "python.exe"
TRANSFORM_PROPS = (
    "Pan", "Tilt", "ZoomX", "ZoomY", "ZoomGang", "RotationAngle",
    "AnchorPointX", "AnchorPointY", "Pitch", "Yaw", "FlipX", "FlipY",
    "CropLeft", "CropRight", "CropTop", "CropBottom", "CropSoftness", "CropRetain",
)


def _find_or_create_matte_bin(media_pool):
    root = media_pool.GetRootFolder()
    for sub in root.GetSubFolderList() or []:
        if sub.GetName() == "WhisperX Mattes":
            return sub
    return media_pool.AddSubFolder(root, "WhisperX Mattes") or root


def _import_or_reuse_matte(media_pool, matte_folder, matte_path):
    target = os.path.normcase(os.path.abspath(matte_path))
    for item in matte_folder.GetClipList() or []:
        try:
            fp = item.GetClipProperty("File Path")
            if fp and os.path.normcase(os.path.abspath(fp)) == target:
                return item
        except Exception:
            continue
    prev_folder = media_pool.GetCurrentFolder()
    try:
        media_pool.SetCurrentFolder(matte_folder)
        imported = media_pool.ImportMedia([str(matte_path)]) or []
        return imported[0] if imported else None
    finally:
        if prev_folder:
            media_pool.SetCurrentFolder(prev_folder)


def _select_subject_track_clips(timeline, max_track, span_start, span_end):
    """Pick the highest enabled non-title, non-matte video track overlapping [span_start, span_end)."""
    for track in range(max_track, 0, -1):
        try:
            name = str(timeline.GetTrackName("video", track) or "")
            if name.startswith("WhisperX Персонаж"):
                continue
            if hasattr(timeline, "GetIsTrackEnabled") and not timeline.GetIsTrackEnabled("video", track):
                continue
            items = timeline.GetItemListInTrack("video", track) or []
        except Exception:
            continue
        matched = []
        for item in items:
            try:
                mp = item.GetMediaPoolItem()
                if not mp:
                    continue
                path = mp.GetClipProperty("File Path")
                if not path or not os.path.isfile(path):
                    continue
                c_start, c_end = int(item.GetStart()), int(item.GetEnd())
                if c_end <= span_start or c_start >= span_end:
                    continue
                src_range = _clip_source_range(item, mp)
                if not src_range:
                    continue
                matched.append((item, c_start, c_end, path, src_range[0], src_range[1]))
            except Exception:
                continue
        if matched:
            return track, matched
    return None, []


def _find_subject_matte_script():
    import sys as _sys
    candidates = [
        Path(__file__).resolve().with_name("subject_matte.py"),
    ]
    if getattr(_sys, "frozen", False) and hasattr(_sys, "_MEIPASS"):
        meipass = Path(_sys._MEIPASS)
        candidates.insert(0, meipass / "runtime_scripts" / "subject_matte.py")
        candidates.insert(1, meipass / "backend" / "resolve_tools" / "subject_matte.py")
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


def render_subject_matte(video_path, start_sec, duration_sec, fps, frames):
    if not WHISPERX_PYTHON.is_file():
        raise RuntimeError(f"Не найден Python окружения WhisperX: {WHISPERX_PYTHON}")
    script = _find_subject_matte_script()
    if not script.is_file():
        raise RuntimeError(f"Не найден модуль выделения персонажа: {script}")
    cmd = [
        str(WHISPERX_PYTHON), str(script),
        "--input", str(video_path),
        "--start", f"{start_sec:.6f}",
        "--duration", f"{duration_sec:.6f}",
        "--fps", f"{fps:.6f}",
        "--frames", str(int(frames)),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(f"Ошибка ИИ-маски персонажа:\n{(proc.stderr or proc.stdout).strip()}")
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Скрипт маски персонажа не вернул путь к файлу.")
    info = json.loads(lines[-1])
    return info["output"]


def prepare_subject_mattes(timeline, max_source_track, spans, status_callback=None):
    """Render or fetch cached ProRes 4444 person mattes for clips overlapping spans."""
    if not spans:
        return None, []
    span_start = min(first for first, _, _ in spans)
    span_end = max(last for _, last, _ in spans)
    src_track, clips = _select_subject_track_clips(timeline, max_source_track, span_start, span_end)
    if not clips:
        return None, []

    fps = float(timeline.GetSetting("timelineFrameRate"))
    matte_entries = []
    for idx, (src_item, c_start, c_end, video_path, src_start, src_end) in enumerate(clips, 1):
        if status_callback:
            status_callback(f"Выделение персонажа (ИИ-маска) {idx}/{len(clips)}…")
        clip_frames = c_end - c_start
        matte_path = render_subject_matte(video_path, src_start, src_end - src_start, fps, clip_frames)
        matte_entries.append((src_item, c_start, c_end, matte_path))
    return src_track, matte_entries


def place_foreground_person_track(project, timeline, matte_entries):
    """Add the foreground person cutout track above Text+ using prepared mattes."""
    if not matte_entries:
        return None, []
    media_pool = project.GetMediaPool()
    matte_folder = _find_or_create_matte_bin(media_pool)

    if not timeline.AddTrack("video"):
        raise RuntimeError("Не удалось добавить дорожку персонажа поверх Text+.")
    fg_track = int(timeline.GetTrackCount("video"))
    timeline.SetTrackName("video", fg_track, f"WhisperX Персонаж (V{fg_track})")

    placed_items = []
    for src_item, c_start, c_end, matte_path in matte_entries:
        clip_frames = c_end - c_start
        mp_item = _import_or_reuse_matte(media_pool, matte_folder, matte_path)
        if not mp_item:
            raise RuntimeError(f"Resolve не смог импортировать маску персонажа: {matte_path}")
        try:
            mp_item.SetClipProperty("Alpha Mode", "Straight")
        except Exception:
            pass
        appended = media_pool.AppendToTimeline([{
            "mediaPoolItem": mp_item,
            "startFrame": 0,
            "endFrame": max(0, clip_frames - 1),
            "trackIndex": fg_track,
            "recordFrame": c_start,
            "mediaType": 1,
        }]) or []
        if not appended:
            raise RuntimeError("Resolve не смог разместить слой персонажа на таймлайне.")
        fg_item = appended[0]
        placed_items.append(fg_item)
        for prop in TRANSFORM_PROPS:
            try:
                val = src_item.GetProperty(prop)
                if val is not None:
                    fg_item.SetProperty(prop, val)
            except Exception:
                continue
    return fg_track, placed_items


def add_foreground_person_track(project, timeline, max_source_track, spans, status_callback=None):
    """Create a foreground person cutout track above Text+ so characters overlap subtitles."""
    _, matte_entries = prepare_subject_mattes(
        timeline, max_source_track, spans, status_callback=status_callback,
    )
    return place_foreground_person_track(project, timeline, matte_entries)


OCC_W = 72
OCC_H = 40


def _parse_resolution(res_str, fallback_w, fallback_h):
    if isinstance(res_str, str) and "x" in res_str.lower():
        parts = res_str.lower().split("x", 1)
        try:
            w, h = int(parts[0].strip()), int(parts[1].strip())
            if w > 0 and h > 0:
                return w, h
        except ValueError:
            pass
    return fallback_w, fallback_h


def _clip_viewport_geometry(src_item, tl_w, tl_h):
    """Compute visible timeline bounds [vx0,vx1]x[vy0,vy1] (Fusion Y-up) and source crop [sx0,sx1]x[sy0,sy1]."""
    try:
        mp = src_item.GetMediaPoolItem()
        res_str = mp.GetClipProperty("Resolution") if mp else ""
    except Exception:
        res_str = ""
    sw, sh = _parse_resolution(res_str, tl_w, tl_h)
    try:
        zoom_x = float(src_item.GetProperty("ZoomX") or 1.0)
        zoom_y = float(src_item.GetProperty("ZoomY") or 1.0)
        pan = float(src_item.GetProperty("Pan") or 0.0)
        tilt = float(src_item.GetProperty("Tilt") or 0.0)
    except Exception:
        zoom_x, zoom_y, pan, tilt = 1.0, 1.0, 0.0, 0.0

    base_scale = max(tl_w / sw, tl_h / sh)
    disp_w = max(1.0, sw * base_scale * max(0.05, zoom_x))
    disp_h = max(1.0, sh * base_scale * max(0.05, zoom_y))
    cx_tl = tl_w / 2.0 + pan
    cy_tl = tl_h / 2.0 + tilt

    vx0 = max(0.0, min(1.0, (cx_tl - disp_w / 2.0) / tl_w))
    vx1 = max(0.0, min(1.0, (cx_tl + disp_w / 2.0) / tl_w))
    vy0 = max(0.0, min(1.0, (cy_tl - disp_h / 2.0) / tl_h))
    vy1 = max(0.0, min(1.0, (cy_tl + disp_h / 2.0) / tl_h))

    sx0 = max(0.0, min(1.0, 0.5 + (vx0 * tl_w - cx_tl) / disp_w))
    sx1 = max(0.0, min(1.0, 0.5 + (vx1 * tl_w - cx_tl) / disp_w))
    sy0 = max(0.0, min(1.0, 0.5 + ((1.0 - vy1) * tl_h - (tl_h - cy_tl)) / disp_h))
    sy1 = max(0.0, min(1.0, 0.5 + ((1.0 - vy0) * tl_h - (tl_h - cy_tl)) / disp_h))
    return (vx0, vx1, vy0, vy1), (sx0, sx1, sy0, sy1)


def _decode_matte_occupancy_grids(matte_entries, spans, fps, tl_w, tl_h):
    """Extract projected person-alpha occupancy grids (OCC_W x OCC_H, top-to-bottom) per caption."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg or not matte_entries:
        return {}
    frame_bytes = OCC_W * OCC_H
    results = {}
    for src_item, c_start, c_end, matte_path in matte_entries:
        if not os.path.isfile(matte_path):
            continue
        viewport, (sx0, sx1, sy0, sy1) = _clip_viewport_geometry(src_item, tl_w, tl_h)
        cw, ch = max(0.05, sx1 - sx0), max(0.05, sy1 - sy0)
        crop_filter = f"alphaextract,crop=iw*{cw:.5f}:ih*{ch:.5f}:iw*{sx0:.5f}:ih*{sy0:.5f},fps={BATCH_FPS},scale={OCC_W}:{OCC_H}"
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", str(matte_path),
            "-vf", crop_filter,
            "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, check=True, timeout=45, creationflags=NO_WINDOW)
        except Exception:
            continue
        n_frames = len(proc.stdout) // frame_bytes
        if n_frames <= 0:
            continue
        for idx, (first, last, _caption) in enumerate(spans):
            if last <= c_start or first >= c_end:
                continue
            sample_frames = (first, (first + last) // 2, max(first, last - 1))
            max_grid = [0.0] * frame_bytes
            mid_buf = None
            for s_pos, sf in enumerate(sample_frames):
                rel_sec = max(0.0, (sf - c_start) / fps)
                fi = max(0, min(n_frames - 1, int(round(rel_sec * BATCH_FPS))))
                buf = proc.stdout[fi * frame_bytes:(fi + 1) * frame_bytes]
                if s_pos == 1:
                    mid_buf = buf
                for p in range(frame_bytes):
                    v = buf[p] / 255.0
                    if v > max_grid[p]:
                        max_grid[p] = v
            results[idx] = (max_grid, mid_buf, viewport)
    return results


def _saliency_grid_from_rgb(rgb_bytes, width, height):
    """Build a 0..1 visual-busyness / person-saliency grid from an RGB24 frame."""
    n = width * height
    luma = [0.0] * n
    skin = [0.0] * n
    for i in range(n):
        r, g, b = rgb_bytes[i * 3], rgb_bytes[i * 3 + 1], rgb_bytes[i * 3 + 2]
        y = 0.299 * r + 0.587 * g + 0.114 * b
        luma[i] = y
        cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
        cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
        if 40.0 < y < 235.0 and 77.0 <= cb <= 127.0 and 133.0 <= cr <= 173.0:
            skin[i] = 1.0
    edge = [0.0] * n
    for y in range(1, height - 1):
        row = y * width
        for x in range(1, width - 1):
            idx = row + x
            edge[idx] = (abs(luma[idx + 1] - luma[idx - 1]) + abs(luma[idx + width] - luma[idx - width])) / 510.0
    grid = [0.0] * n
    for y in range(1, height - 1):
        row = y * width
        for x in range(1, width - 1):
            idx = row + x
            e = edge[idx]
            loc_e = (e + edge[idx - 1] + edge[idx + 1] + edge[idx - width] + edge[idx + width]) * 0.2
            sharp_skin = skin[idx] * min(1.0, loc_e * 8.0)
            grid[idx] = min(1.0, e * 1.8 + sharp_skin * 1.6)
    return grid


def _decode_rgb_occupancy_grids(timeline, spans, tl_w, tl_h):
    """Fallback occupancy grids from source RGB video cropped to exact timeline viewport."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg or timeline is None or not spans:
        return {}
    span_start = min(first for first, _, _ in spans)
    span_end = max(last for _, last, _ in spans)
    track_count = int(timeline.GetTrackCount("video") or 0)
    _, clips = _select_subject_track_clips(timeline, track_count, span_start, span_end)
    if not clips:
        return {}
    frame_bytes = OCC_W * OCC_H * 3
    results = {}
    for src_item, c_start, c_end, video_path, src_start, src_end in clips:
        viewport, (sx0, sx1, sy0, sy1) = _clip_viewport_geometry(src_item, tl_w, tl_h)
        cw, ch = max(0.05, sx1 - sx0), max(0.05, sy1 - sy0)
        pad_dur = max(0.5, src_end - src_start + 0.5)
        crop_filter = f"crop=iw*{cw:.5f}:ih*{ch:.5f}:iw*{sx0:.5f}:ih*{sy0:.5f},fps={BATCH_FPS},scale={OCC_W}:{OCC_H}"
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, src_start):.3f}", "-t", f"{pad_dur:.3f}",
            "-i", str(video_path),
            "-vf", crop_filter,
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, check=True, timeout=45, creationflags=NO_WINDOW)
        except Exception:
            continue
        n_frames = len(proc.stdout) // frame_bytes
        if n_frames <= 0:
            continue
        for idx, (first, last, _caption) in enumerate(spans):
            if last <= c_start or first >= c_end:
                continue
            mid = (first + last) / 2.0
            rel_sec = (mid - c_start) * (src_end - src_start) / max(1, c_end - c_start)
            fi = max(0, min(n_frames - 1, int(round(rel_sec * BATCH_FPS))))
            buf = proc.stdout[fi * frame_bytes:(fi + 1) * frame_bytes]
            results[idx] = (_saliency_grid_from_rgb(buf, OCC_W, OCC_H), buf, viewport)
    return results


def _estimate_caption_box(text_str, preset, size_scale, tl_w, tl_h):
    """Estimate normalized (box_w, box_h) of a Text+ caption block in timeline coordinates."""
    text_style = preset["text_plus"]
    layout = preset["layout"]
    lines = [line.strip() for line in (text_str or "").splitlines() if line.strip()]
    if not lines:
        return 0.25, 0.05
    max_w_frac = float(layout.get("max_width_fraction", 0.58))
    cap_h_factor = float(layout.get("cap_height_factor", 0.485))
    line_gap = float(layout.get("line_gap_y", 0.0027))
    char_scale = float(text_style["inputs"].get("CharacterSizeX", 1.14)) * float(text_style["inputs"].get("CharacterSpacingClone", 1.085))
    total_h = 0.0
    max_w = 0.0
    for i, line in enumerate(lines):
        role = text_style["accent_line" if i == 0 else "supporting_line"]
        size = role["size"]
        if i == 0 and len(line) <= role["short_max_characters"]:
            size = role["short_size"]
        size *= size_scale
        h_i = size * cap_h_factor * tl_w / tl_h
        # Druk Condensed Cyr Super glyph width factor ~0.34 of Size * char_scale
        w_i = min(max_w_frac, max(0.10, len(line) * size * 0.34 * char_scale))
        total_h += h_i
        if w_i > max_w:
            max_w = w_i
    total_h += line_gap * max(0, len(lines) - 1)
    return min(max_w_frac, max(0.14, max_w)), min(0.26, max(0.03, total_h))


def _build_integral(grid, gw, gh):
    sat = [[0.0] * (gw + 1) for _ in range(gh + 1)]
    for y in range(gh):
        row_sum = 0.0
        g_off = y * gw
        prev_row = sat[y]
        cur_row = sat[y + 1]
        for x in range(gw):
            row_sum += grid[g_off + x]
            cur_row[x + 1] = prev_row[x + 1] + row_sum
    return sat


def _box_mean(sat, gw, gh, x0, x1, y0_top, y1_top):
    ix0 = max(0, min(gw - 1, int(math.floor(x0 * gw))))
    ix1 = max(ix0 + 1, min(gw, int(math.ceil(x1 * gw))))
    iy0 = max(0, min(gh - 1, int(math.floor(y0_top * gh))))
    iy1 = max(iy0 + 1, min(gh, int(math.ceil(y1_top * gh))))
    area = (ix1 - ix0) * (iy1 - iy0)
    total = sat[iy1][ix1] - sat[iy0][ix1] - sat[iy1][ix0] + sat[iy0][ix0]
    return total / area


def _find_emptiest_placement(grid, viewport, box_w, box_h, prev_choice, is_cut):
    """Find the (side, x, top_y, h_align, occ) in the visible video area with minimum person occlusion."""
    vx0, vx1, vy0, vy1 = viewport
    vw = max(0.1, vx1 - vx0)
    vh = max(0.1, vy1 - vy0)
    sat = _build_integral(grid, OCC_W, OCC_H)

    # Safe margins strictly inside the visible video area
    margin_x = 0.045 * vw
    margin_top = 0.045 * vh
    margin_bot = 0.055 * vh

    min_bx0 = vx0 + margin_x
    max_bx0 = max(min_bx0, vx1 - margin_x - box_w)
    min_top_y = min(vy1 - margin_top, vy0 + margin_bot + box_h)
    max_top_y = max(min_top_y, vy1 - margin_top)

    # Prefer upper-middle of the visible video (open background space above shoulders)
    prefer_y = vy0 + vh * 0.78
    prefer_y = max(min_top_y, min(max_top_y, prefer_y))

    nx_steps = 13
    ny_steps = 9
    pad_x = 0.030
    pad_y = 0.020

    best = None
    for iy in range(ny_steps):
        fy = iy / max(1, ny_steps - 1)
        top_y = min_top_y + fy * (max_top_y - min_top_y)
        bot_y = top_y - box_h
        # Convert Fusion Y-up [bot_y, top_y] to viewport-relative top-down [0..1]
        ry0 = (vy1 - top_y) / vh
        ry1 = (vy1 - bot_y) / vh
        for ix in range(nx_steps):
            fx = ix / max(1, nx_steps - 1)
            bx0 = min_bx0 + fx * (max_bx0 - min_bx0)
            bx1 = bx0 + box_w
            rx0 = (bx0 - vx0) / vw
            rx1 = (bx1 - vx0) / vw

            occ_inner = _box_mean(sat, OCC_W, OCC_H, rx0, rx1, ry0, ry1)
            occ_pad = _box_mean(
                sat, OCC_W, OCC_H,
                max(0.0, rx0 - pad_x), min(1.0, rx1 + pad_x),
                max(0.0, ry0 - pad_y), min(1.0, ry1 + pad_y),
            )
            box_cx = (bx0 + bx1) / 2.0

            # Gentle stability within the same shot so captions don't jump needlessly when still unobstructed
            stability = 0.0
            if not is_cut and prev_choice is not None:
                prev_cx, prev_y = prev_choice
                stability = 0.35 * (abs(box_cx - prev_cx) + abs(top_y - prev_y))

            # Mild preference for editorial upper-middle zone when occlusion is similarly low
            vert_pref = 0.32 * abs(top_y - prefer_y) / vh
            score = occ_inner * 14.0 + occ_pad * 2.8 + vert_pref + stability

            if best is None or score < best[0]:
                if box_cx < 0.44:
                    side, anchor_x, h_align = "left", bx0, -1
                elif box_cx > 0.56:
                    side, anchor_x, h_align = "right", bx1, 1
                else:
                    side = "right" if box_cx >= 0.5 else "left"
                    anchor_x, h_align = box_cx, 0
                best = (score, side, anchor_x, top_y, h_align, occ_inner, box_cx)

    _, side, anchor_x, top_y, h_align, occ_inner, box_cx = best
    return {
        "side": side,
        "x": round(anchor_x, 4),
        "y": round(top_y, 4),
        "h_align": h_align,
        "occlusion": round(occ_inner, 4),
        "box_cx": box_cx,
    }


def resolve_caption_placements(timeline, spans, tl_w, tl_h, matte_entries=None):
    """Resolve optimal 2D (side, x, y, h_align) placements in empty areas of the video for each caption.

    Uses exact RVM person-alpha mattes when matte_entries are provided; falls back to
    viewport-cropped RGB saliency, and finally to fallback alternation if no video is available.
    """
    if not spans:
        return []
    fps = 24.0
    if timeline is not None:
        try:
            fps = float(timeline.GetSetting("timelineFrameRate") or 24.0)
        except Exception:
            fps = 24.0

    needs_auto = any(
        caption.reference_preset
        and caption.reference_preset.get("id") == PRESET_ID
        and getattr(caption, "side", "auto") == "auto"
        for _, _, caption in spans
    )
    occ_grids = {}
    if needs_auto:
        if matte_entries:
            occ_grids = _decode_matte_occupancy_grids(matte_entries, spans, fps, tl_w, tl_h)
        if not occ_grids and timeline is not None:
            occ_grids = _decode_rgb_occupancy_grids(timeline, spans, tl_w, tl_h)

    placements = []
    prev_choice = None
    prev_buf = None
    fallback_step = 0

    for idx, (_first, _last, caption) in enumerate(spans):
        preset = caption.reference_preset
        if not preset or preset.get("id") != PRESET_ID:
            manual_side = getattr(caption, "side", "auto")
            side = manual_side if manual_side in SIDES else ("left" if caption.x < 0.5 else "right")
            h_al = -1 if manual_side == "left" else (1 if manual_side == "right" else 0)
            placements.append({"side": side, "x": caption.x, "y": caption.y, "h_align": h_al})
            continue

        defaults = parse_reference_preset(preset)
        layout = preset["layout"]
        manual_side = getattr(caption, "side", "auto")
        if manual_side in SIDES:
            placements.append({
                "side": manual_side,
                "x": caption.x,
                "y": caption.y,
                "h_align": layout[manual_side]["HorizontalLeftCenterRight"],
            })
            prev_choice = (caption.x, caption.y)
            continue

        if idx in occ_grids:
            grid, mid_buf, viewport = occ_grids[idx]
            is_cut = (
                prev_buf is None
                or mid_buf is None
                or _frame_diff(prev_buf, mid_buf) > SHOT_CUT_DIFF_THRESHOLD
            )
            size_scale = caption.size / defaults["size"] if defaults["size"] else 1.0
            box_w, box_h = _estimate_caption_box(caption.text, preset, size_scale, tl_w, tl_h)
            choice = _find_emptiest_placement(grid, viewport, box_w, box_h, prev_choice, is_cut)
            placements.append(choice)
            prev_choice = (choice["box_cx"], choice["y"])
            prev_buf = mid_buf
        else:
            side_cfg = side_selection_config(preset)
            default_side = side_cfg.get("default_side", layout.get("side", "right"))
            fallback = side_cfg.get("fallback") or {}
            seq = fallback.get("sequence") or [default_side, "left" if default_side == "right" else "right"]
            side = seq[fallback_step % len(seq)] if fallback.get("when_no_frame_analysis") == "alternate" else default_side
            fallback_step += 1
            placements.append({
                "side": side,
                "x": layout[side]["x"],
                "y": caption.y,
                "h_align": layout[side]["HorizontalLeftCenterRight"],
            })
            prev_choice = (layout[side]["x"], caption.y)

    return placements
