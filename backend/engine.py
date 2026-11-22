import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.media_server import LocalMediaServer
from backend.resolve_bridge import (
    cached_model_source,
    connect_resolve,
    get_timeline_status,
    render_timeline_audio,
    timeline_seconds,
)
from backend.storage import LocalStore

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
LOCAL_APP = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
FFMPEG7_DIR = LOCAL_APP / "Programs" / "FFmpeg7Shared" / "ffmpeg-7.1.1-full_build-shared" / "bin"
FFMPEG9_DIR = LOCAL_APP / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe" / "ffmpeg-9.1-full_build" / "bin"


def get_bundle_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def find_ffmpeg_dir() -> Optional[Path]:
    if (FFMPEG7_DIR / "ffmpeg.exe").is_file():
        return FFMPEG7_DIR
    if (FFMPEG9_DIR / "ffmpeg.exe").is_file():
        return FFMPEG9_DIR
    found = shutil.which("ffmpeg")
    if found:
        return Path(found).parent
    return None


def find_whisperx_binary(configured_cmd: str = "whisperx") -> str:
    if configured_cmd:
        if Path(configured_cmd).is_file():
            return str(Path(configured_cmd))
        found = shutil.which(configured_cmd)
        if found:
            return found
    found_default = shutil.which("whisperx")
    if found_default:
        return found_default
    home = Path.home()
    prog_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    for p in (
        LOCAL_APP / "Programs" / "WhisperX" / "Scripts" / "whisperx.exe",
        LOCAL_APP / "WhisperX" / "Scripts" / "whisperx.exe",
        prog_files / "WhisperX" / "Scripts" / "whisperx.exe",
        home / "anaconda3" / "Scripts" / "whisperx.exe",
        home / "miniconda3" / "Scripts" / "whisperx.exe",
    ):
        if p.is_file():
            return str(p)
    return ""


def find_ytdlp_binary(configured_cmd: str = "yt-dlp") -> str:
    if configured_cmd:
        if Path(configured_cmd).is_file():
            return str(Path(configured_cmd))
        found = shutil.which(configured_cmd)
        if found:
            return found
    found_default = shutil.which("yt-dlp")
    if found_default:
        return found_default
    home = Path.home()
    prog_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    for p in (
        LOCAL_APP / "Programs" / "WhisperX" / "Scripts" / "yt-dlp.exe",
        LOCAL_APP / "Microsoft" / "WinGet" / "Links" / "yt-dlp.exe",
        home / "Shutter Encoder" / "Library" / "yt-dlp.exe",
        home / "yt-dlp" / "yt-dlp.exe",
        prog_files / "yt-dlp" / "yt-dlp.exe",
        Path(r"C:\yt-dlp\yt-dlp.exe"),
    ):
        if p.is_file():
            return str(p)
    return ""


def find_gui_python() -> str:
    """Find pythonw.exe / python.exe with Tkinter to launch CaptionEditor cleanly."""
    candidates = [
        LOCAL_APP / "Programs" / "Python" / "Python311" / "pythonw.exe",
        LOCAL_APP / "Programs" / "Python" / "Python312" / "pythonw.exe",
        LOCAL_APP / "Programs" / "WhisperX" / "Scripts" / "pythonw.exe",
        LOCAL_APP / "Programs" / "WhisperX" / "Scripts" / "python.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    for name in ("pythonw", "python"):
        found = shutil.which(name)
        if found:
            return found
    return sys.executable


def _run_quiet(cmd: List[str], timeout: int = 8) -> Optional[str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return None


def detect_environment(whisperx_cmd: str = "whisperx", ytdlp_cmd: str = "yt-dlp") -> Dict[str, Any]:
    gpu_name = None
    vram_gb = None
    smi = _run_quiet(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if smi:
        parts = smi.splitlines()[0].split(",")
        if len(parts) >= 2:
            gpu_name = parts[0].strip()
            try:
                vram_gb = round(float(parts[1].strip()) / 1024.0, 1)
            except ValueError:
                pass

    ff_dir = find_ffmpeg_dir()
    wx_path = find_whisperx_binary(whisperx_cmd)
    yt_path = find_ytdlp_binary(ytdlp_cmd)

    return {
        "gpu": gpu_name or "CPU",
        "hasCuda": bool(gpu_name),
        "vramGb": vram_gb,
        "ffmpeg": bool(ff_dir),
        "ffmpegDir": str(ff_dir) if ff_dir else "",
        "whisperx": bool(wx_path),
        "whisperxPath": wx_path,
        "ytdlp": bool(yt_path),
        "ytdlpPath": yt_path,
    }


def parse_srt_file(srt_path: str) -> Dict[str, Any]:
    p = Path(srt_path)
    if not p.is_file():
        return {"srtPath": srt_path, "title": p.stem, "segments": [], "duration": 0.0}

    text = p.read_text(encoding="utf-8-sig", errors="replace")
    blocks = re.split(r"\r?\n\r?\n+", text.strip())
    time_re = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")

    def to_sec(h: str, m: str, s: str, ms: str) -> float:
        return round(int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0, 2)

    segments = []
    max_end = 0.0
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        t_idx = 1 if time_re.search(lines[1]) else (0 if time_re.search(lines[0]) else -1)
        if t_idx < 0:
            continue
        m = time_re.search(lines[t_idx])
        if not m:
            continue
        start = to_sec(*m.groups()[:4])
        end = to_sec(*m.groups()[4:])
        max_end = max(max_end, end)
        body = " ".join(lines[t_idx + 1 :]).strip()
        segments.append({"start": start, "end": end, "text": body, "words": []})

    # Enrich with companion .whisperx-words.json if present
    words_json = p.with_suffix(".whisperx-words.json")
    if words_json.is_file():
        try:
            doc = json.loads(words_json.read_text(encoding="utf-8"))
            raw_segs = doc.get("segments", [])
            if raw_segs:
                enriched = []
                for s in raw_segs:
                    st = round(float(s.get("start", 0.0)), 2)
                    en = round(float(s.get("end", 0.0)), 2)
                    max_end = max(max_end, en)
                    words = [
                        {
                            "word": w.get("word", ""),
                            "start": round(float(w.get("start", st)), 2),
                            "end": round(float(w.get("end", en)), 2),
                            "score": round(float(w.get("score", 0.95)), 2),
                        }
                        for w in s.get("words", [])
                    ]
                    enriched.append({
                        "start": st,
                        "end": en,
                        "speaker": s.get("speaker", ""),
                        "text": s.get("text", "").strip(),
                        "words": words,
                    })
                segments = enriched
        except Exception:
            pass

    return {
        "srtPath": str(p),
        "title": p.stem,
        "segments": segments,
        "duration": round(max(max_end, 1.0), 2),
        "hasWordsJson": words_json.is_file(),
    }


def extract_waveform_bars(file_path: str, num_bars: int = 120) -> Optional[List[float]]:
    ff_dir = find_ffmpeg_dir()
    if not ff_dir or not file_path or not Path(file_path).is_file():
        return None
    ffmpeg_exe = str(ff_dir / "ffmpeg.exe") if (ff_dir / "ffmpeg.exe").is_file() else "ffmpeg"
    try:
        proc = subprocess.run(
            [ffmpeg_exe, "-v", "error", "-i", file_path, "-ac", "1", "-ar", "4000", "-f", "s16le", "-"],
            capture_output=True,
            timeout=25,
            creationflags=CREATE_NO_WINDOW,
        )
        raw = proc.stdout
        if len(raw) < 4:
            return None
        total = len(raw) // 2
        step = max(1, total // num_bars)
        vals = []
        peak = 1.0
        for i in range(num_bars):
            s0 = i * step
            s1 = min(total, s0 + step)
            chunk = raw[s0 * 2 : s1 * 2]
            cnt = len(chunk) // 2
            if cnt <= 0:
                vals.append(0.05)
                continue
            stride = max(1, cnt // 180)
            acc = 0.0
            n = 0
            for j in range(0, cnt, stride):
                v = struct.unpack_from("<h", chunk, j * 2)[0]
                acc += v * v
                n += 1
            rms = math.sqrt(acc / max(n, 1))
            vals.append(rms)
            if rms > peak:
                peak = rms
        return [round(max(0.06, min(0.96, (v / peak) * 0.94)), 2) for v in vals]
    except Exception:
        return None


class WhisperXEngine:
    def __init__(self, store: LocalStore, media_server: LocalMediaServer) -> None:
        self.store = store
        self.media_server = media_server
        self.env = self.refresh_env()
        self.timeline = get_timeline_status()

        self._lock = threading.RLock()
        self.running = False
        self.cancel_requested = False
        self.process: Optional[subprocess.Popen] = None
        self.status_text = "Готово к запуску"
        self.progress = 0
        self.stage = "idle"  # idle | resolve | download | whisperx | done | error
        self.logs: List[str] = []
        self.active_subtitle: Optional[Dict[str, Any]] = None

        # Preload last SRT if it still exists
        last_srt = store.data["settings"].get("lastSrt", "")
        if last_srt and Path(last_srt).is_file():
            self.active_subtitle = parse_srt_file(last_srt)

    def refresh_env(self) -> Dict[str, Any]:
        s = self.store.data["settings"]
        self.env = detect_environment(s.get("whisperxCmd", "whisperx"), s.get("ytdlpCmd", "yt-dlp"))
        return self.env

    def refresh_timeline(self) -> Dict[str, Any]:
        self.timeline = get_timeline_status()
        return self.timeline

    def _log(self, msg: str) -> None:
        with self._lock:
            self.logs.append(msg)
            if len(self.logs) > 300:
                self.logs = self.logs[-300:]

    def get_live_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "statusText": self.status_text,
                "progress": self.progress,
                "stage": self.stage,
                "logs": list(self.logs[-120:]),
                "timeline": self.timeline,
                "activeSubtitle": self.active_subtitle,
                "history": list(self.store.data["history"]),
                "lastSrt": self.store.data["settings"].get("lastSrt", ""),
            }

    def load_srt(self, srt_path: str, audio_path: str = "") -> Dict[str, Any]:
        parsed = parse_srt_file(srt_path)
        if audio_path and Path(audio_path).is_file():
            media_id = "active"
            self.media_server.register_media(media_id, audio_path)
            parsed["audioUrl"] = f"/media/{media_id}?t={int(datetime.now().timestamp())}"
            parsed["audioPath"] = audio_path
            parsed["bars"] = extract_waveform_bars(audio_path) or []
        else:
            parsed["audioUrl"] = ""
            parsed["audioPath"] = ""
            parsed["bars"] = []
        with self._lock:
            self.active_subtitle = parsed
            self.store.update_settings({"lastSrt": srt_path})
        return parsed

    def launch_caption_editor(self, srt_path: str = "") -> Dict[str, Any]:
        target_srt = srt_path or self.store.data["settings"].get("lastSrt", "")
        dest = self.store.data["settings"].get("destination", str(self.store.data_dir / "subtitles"))
        launcher = get_bundle_root() / "backend" / "resolve_tools" / "editor_launcher.py"
        if not launcher.is_file():
            return {"ok": False, "error": f"Не найден модуль редактора: {launcher}"}
        py_bin = find_gui_python()
        cmd = [py_bin, str(launcher), "--dest", dest]
        if target_srt and Path(target_srt).is_file():
            cmd.extend(["--srt", target_srt])
        subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW)
        return {"ok": True}

    def start_job(self, opts: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if self.running:
                return {"ok": False, "error": "Обработка уже выполняется."}

        mode = opts.get("sourceMode", "timeline")
        dest_str = (opts.get("destination") or "").strip()
        if not dest_str:
            return {"ok": False, "error": "Укажите папку сохранения SRT в меню настроек."}
        dest = Path(dest_str)
        dest.mkdir(parents=True, exist_ok=True)

        self.refresh_env()
        if not self.env["whisperx"]:
            return {"ok": False, "error": "WhisperX не найден. Проверьте путь к whisperx.exe в меню настроек."}
        if not self.env["ffmpeg"]:
            return {"ok": False, "error": "FFmpeg не найден."}

        source_file = (opts.get("selectedFile") or "").strip()
        source_url = (opts.get("selectedUrl") or "").strip()
        if mode == "file" and (not source_file or not Path(source_file).is_file()):
            return {"ok": False, "error": "Выберите существующий видео- или аудиофайл."}
        if mode == "url":
            if not source_url.startswith(("http://", "https://")):
                return {"ok": False, "error": "Введите корректную ссылку YouTube / URL."}
            if not self.env["ytdlp"]:
                return {"ok": False, "error": "yt-dlp.exe не найден. Укажите путь в меню настроек."}

        timeline_identity = None
        if mode == "timeline":
            try:
                project, timeline = connect_resolve()
                timeline_identity = (project.GetName(), timeline.GetName(), timeline.GetUniqueId())
                secs = timeline_seconds(timeline)
                dur = f"{int(secs // 3600):02}:{int(secs % 3600 // 60):02}:{int(secs % 60):02}"
                self.timeline = {
                    "connected": True,
                    "project": timeline_identity[0],
                    "timeline": timeline_identity[1],
                    "uniqueId": timeline_identity[2],
                    "seconds": round(secs, 2),
                    "duration": dur,
                    "label": f"{timeline_identity[0]} / {timeline_identity[1]} ({dur})",
                    "error": "",
                }
                if project.IsRenderingInProgress():
                    return {"ok": False, "error": "В DaVinci Resolve уже идёт рендер. Дождитесь его завершения."}
            except Exception as exc:
                self.timeline = get_timeline_status()
                return {"ok": False, "error": str(exc)}

        self.store.update_settings({
            "sourceMode": mode,
            "selectedFile": source_file,
            "selectedUrl": source_url,
            "destination": str(dest),
            "model": opts.get("model", "medium"),
            "language": opts.get("language", "ru"),
            "batch": int(opts.get("batch", 4)),
            "align": bool(opts.get("align", True)),
            "diarize": bool(opts.get("diarize", False)),
            "maxSpeakers": int(opts.get("maxSpeakers", 4)),
        })

        with self._lock:
            self.running = True
            self.cancel_requested = False
            self.progress = 3
            self.stage = "resolve" if mode == "timeline" else ("download" if mode == "url" else "whisperx")
            self.status_text = "Подготовка к обработке..."
            self.logs = []

        threading.Thread(
            target=self._worker,
            args=(dict(opts), dest, timeline_identity),
            daemon=True,
        ).start()
        return {"ok": True}

    def stop_job(self) -> Dict[str, Any]:
        with self._lock:
            self.cancel_requested = True
            proc = self.process
        if proc and proc.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )
        with self._lock:
            self.status_text = "Остановлено пользователем"
            self.stage = "idle"
        self._log("⛔ Остановлено пользователем")
        return {"ok": True}

    def _worker(self, opts: Dict[str, Any], dest: Path, timeline_identity: Optional[tuple]) -> None:
        temp_dir = None
        try:
            temp_dir = tempfile.TemporaryDirectory(prefix="wx20_")
            tmp_path = Path(temp_dir.name)
            mode = opts.get("sourceMode", "timeline")
            settings = self.store.data["settings"]

            audio_input = ""
            result_name = "subtitles"
            playback_audio_path = ""

            if mode == "timeline" and timeline_identity:
                self._log(f"🎬 Таймлайн Resolve: {timeline_identity[0]} / {timeline_identity[1]}")
                with self._lock:
                    self.stage = "resolve"
                    self.status_text = "Resolve: экспорт звука 0%"
                wav_file = render_timeline_audio(
                    tmp_path,
                    "timeline_audio",
                    timeline_identity,
                    is_cancelled=lambda: self.cancel_requested,
                    on_status=lambda txt, pct: self._set_progress(txt, max(5, int(pct * 0.35)), "resolve"),
                )
                audio_input = str(wav_file)
                result_name = timeline_identity[1]
            elif mode == "url":
                url = opts.get("selectedUrl", "").strip()
                self._log(f"🌐 Скачивание аудио: {url}")
                self._set_progress("yt-dlp: загрузка аудио...", 12, "download")
                yt_bin = self.env["ytdlpPath"]
                dl_dir = self.store.data_dir / "downloads"
                out_tpl = str(dl_dir / "%(title).60s.%(ext)s")
                dl_cmd = [yt_bin, "-x", "--audio-format", "mp3", "--print", "after_move:filepath", "-o", out_tpl, url]
                if self.env["ffmpegDir"]:
                    dl_cmd.extend(["--ffmpeg-location", self.env["ffmpegDir"]])
                res = subprocess.run(dl_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
                if res.returncode != 0:
                    raise RuntimeError(f"yt-dlp ошибка: {res.stderr.strip()[:200]}")
                lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip() and Path(ln.strip()).is_file()]
                if not lines:
                    raise RuntimeError("yt-dlp не вернул путь к скачанному файлу.")
                audio_input = lines[-1]
                playback_audio_path = audio_input
                result_name = Path(audio_input).stem
            else:
                audio_input = opts.get("selectedFile", "").strip()
                playback_audio_path = audio_input
                result_name = Path(audio_input).stem

            if self.cancel_requested:
                raise RuntimeError("Остановлено пользователем")

            model = opts.get("model", "medium")
            selected_model = cached_model_source(model)
            model_label = model + (" (из локального кэша)" if selected_model != model else "")
            compute = settings.get("computeType", "float16" if self.env["hasCuda"] else "int8")
            if compute not in ("default", "float16", "float32", "int8"):
                compute = "float16" if self.env["hasCuda"] else "int8"

            self._log(f"⚡ Модель: {model_label} · Язык: {opts.get('language', 'ru')} · Batch: {opts.get('batch', 4)}")
            self._log(f"📁 Папка SRT: {dest}")

            wx_exe = Path(self.env["whisperxPath"])
            wx_python = wx_exe.with_name("python.exe")
            bundle_root = get_bundle_root()
            speech_script = bundle_root / "runtime_scripts" / "whisperx_speech.py"
            if not speech_script.is_file():
                speech_script = bundle_root / "backend" / "resolve_tools" / "whisperx_speech.py"

            args = [
                audio_input,
                "--model", selected_model,
                "--device", "cuda" if self.env["hasCuda"] else "cpu",
                "--compute_type", compute,
                "--batch_size", str(opts.get("batch", 4)),
                "--output_format", "all",
                "--output_dir", str(tmp_path),
                "--verbose", "False",
                "--print_progress", "True",
            ]
            lang = opts.get("language", "ru")
            if lang and lang != "auto":
                args += ["--language", lang]
            if not opts.get("align", True):
                args.append("--no_align")
            if opts.get("diarize", False) and settings.get("hfToken"):
                args += ["--diarize", "--hf_token", settings["hfToken"], "--max_speakers", str(opts.get("maxSpeakers", 4))]

            if wx_python.is_file() and speech_script.is_file():
                runner = [str(wx_python), str(speech_script), *args]
            else:
                runner = [str(wx_exe), *args]

            env = os.environ.copy()
            if self.env["ffmpegDir"]:
                env["PATH"] = self.env["ffmpegDir"] + os.pathsep + env.get("PATH", "")
            env["PYTHONIOENCODING"] = "utf-8"

            self._set_progress("WhisperX: распознавание речи...", 40, "whisperx")
            proc = subprocess.Popen(
                runner,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=CREATE_NO_WINDOW,
            )
            with self._lock:
                self.process = proc

            pct_re = re.compile(r"Progress:\s*(\d+(?:\.\d+)?)%", re.IGNORECASE)
            if proc.stdout:
                for line in proc.stdout:
                    clean = line.rstrip()
                    if clean:
                        self._log(clean)
                    low = clean.lower()
                    if "performing alignment" in low:
                        self._set_progress("WhisperX: выравнивание слов...", 78, "whisperx")
                    elif "performing diarization" in low:
                        self._set_progress("WhisperX: разделение спикеров...", 88, "whisperx")
                    m = pct_re.search(clean)
                    if m:
                        p_val = int(40 + float(m.group(1)) * 0.52)
                        self._set_progress(f"WhisperX: {int(float(m.group(1)))}%", min(95, p_val), "whisperx")

            rc = proc.wait()
            if self.cancel_requested:
                raise RuntimeError("Остановлено пользователем")
            if rc != 0:
                raise RuntimeError(f"WhisperX завершился с кодом {rc}. Подробности в журнале.")

            safe_name = "".join(c if c not in '<>:"/\\|?*' else "_" for c in result_name).strip() or "subtitles"
            final_srt = dest / f"{safe_name}.srt"
            final_words = final_srt.with_suffix(".whisperx-words.json")

            stem = Path(audio_input).stem
            src_srt = tmp_path / f"{stem}.srt"
            src_json = tmp_path / f"{stem}.json"
            if not src_srt.is_file():
                srts = list(tmp_path.glob("*.srt"))
                if not srts:
                    raise RuntimeError("WhisperX завершился, но SRT-файл не найден.")
                src_srt = srts[0]
            shutil.copyfile(src_srt, final_srt)
            if src_json.is_file():
                shutil.copyfile(src_json, final_words)

            parsed = self.load_srt(str(final_srt), playback_audio_path)
            self.store.add_history({
                "title": safe_name,
                "srtPath": str(final_srt),
                "audioPath": playback_audio_path,
                "sourceMode": mode,
                "model": model,
                "date": datetime.now().strftime("%d.%m, %H:%M"),
                "segmentsCount": len(parsed["segments"]),
            })
            self._log(f"✅ Готово: {final_srt}")
            self._set_progress(f"Готово: {final_srt.name}", 100, "done")
        except Exception as exc:
            self._log(f"❌ {exc}")
            self._set_progress(f"Ошибка: {exc}", 0, "error")
        finally:
            with self._lock:
                self.running = False
                self.process = None
            if temp_dir:
                try:
                    temp_dir.cleanup()
                except Exception:
                    pass

    def _set_progress(self, text: str, pct: int, stage: str) -> None:
        with self._lock:
            self.status_text = text
            self.progress = pct
            self.stage = stage
