"""DaVinci Resolve Scripting API bridge & cached WhisperX model helper."""
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

RESOLVE_MODULES = Path(r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules")
DEFAULT_SUBTITLE_DIR = Path(r"C:\ishodniki\subtitles")


def resolve_app():
    if str(RESOLVE_MODULES) not in sys.path:
        sys.path.insert(0, str(RESOLVE_MODULES))
    if "RESOLVE_SCRIPT_LIB" not in os.environ:
        candidates = [
            Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"),
            Path.home() / "Desktop" / "davinci" / "Resolve" / "App" / "DaVinci Resolve" / "fusionscript.dll",
        ]
        for candidate in candidates:
            if candidate.is_file():
                os.environ["RESOLVE_SCRIPT_LIB"] = str(candidate)
                break
    try:
        import DaVinciResolveScript as dvr
    except Exception as exc:
        raise RuntimeError(
            "Модули DaVinci Resolve Scripting не найдены. Убедитесь, что DaVinci Resolve установлен."
        ) from exc
    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        raise RuntimeError("DaVinci Resolve не отвечает через API. Откройте DaVinci Resolve и повторите.")
    return resolve


def connect_resolve():
    resolve = resolve_app()
    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline() if project else None
    if not timeline:
        raise RuntimeError("В DaVinci Resolve нет открытого таймлайна.")
    return project, timeline


def timeline_seconds(timeline) -> float:
    fps = float(timeline.GetSetting("timelineFrameRate") or 24.0)
    return max(0.0, (timeline.GetEndFrame() - timeline.GetStartFrame()) / max(fps, 1.0))


def get_timeline_status() -> Dict[str, Any]:
    try:
        project, timeline = connect_resolve()
        secs = timeline_seconds(timeline)
        dur = f"{int(secs // 3600):02}:{int(secs % 3600 // 60):02}:{int(secs % 60):02}"
        return {
            "connected": True,
            "project": project.GetName(),
            "timeline": timeline.GetName(),
            "uniqueId": timeline.GetUniqueId(),
            "seconds": round(secs, 2),
            "duration": dur,
            "label": f"{project.GetName()} / {timeline.GetName()} ({dur})",
            "error": "",
        }
    except Exception as exc:
        return {
            "connected": False,
            "project": "",
            "timeline": "",
            "uniqueId": "",
            "seconds": 0.0,
            "duration": "00:00:00",
            "label": "Не подключено",
            "error": str(exc),
        }


def render_timeline_audio(
    folder: Path,
    name: str,
    identity: Tuple[str, str, str],
    is_cancelled: Callable[[], bool],
    on_status: Callable[[str, int], None],
) -> Path:
    project, timeline = connect_resolve()
    if (project.GetName(), timeline.GetName(), timeline.GetUniqueId()) != identity:
        raise RuntimeError("Открытый таймлайн изменился. Запустите обработку заново.")
    if project.IsRenderingInProgress():
        raise RuntimeError("В Resolve уже идёт рендер. Дождитесь его завершения.")
    if timeline.GetTrackCount("audio") < 1:
        raise RuntimeError("На таймлайне нет аудиодорожек.")

    if not project.SetCurrentRenderFormatAndCodec("mp4", "H264"):
        raise RuntimeError("Resolve не смог выбрать формат экспорта.")
    if not project.SetRenderSettings({
        "SelectAllFrames": True,
        "TargetDir": str(folder),
        "CustomName": name,
        "ExportVideo": False,
        "ExportAudio": True,
        "AudioFormat": "wav",
        "AudioCodec": "lpcm",
        "AudioSampleRate": 48000,
        "AudioBitDepth": 16,
    }):
        raise RuntimeError("Resolve не принял настройки экспорта звука.")

    job = project.AddRenderJob()
    if not job:
        raise RuntimeError("Не удалось добавить рендер звука в очередь Resolve.")
    try:
        if not project.StartRendering([job], False):
            raise RuntimeError("Resolve не начал рендер звука.")
        while True:
            status = project.GetRenderJobStatus(job) or {}
            st = status.get("JobStatus", "")
            if st == "Complete":
                break
            if st in ("Failed", "Cancelled", "Background Render Cancelled"):
                raise RuntimeError(f"Рендер звука: {st}. {status.get('Error', '')}")
            if is_cancelled():
                project.StopRendering()
                raise RuntimeError("Остановлено пользователем")
            pct = int(status.get("CompletionPercentage", 0) or 0)
            on_status(f"Resolve: экспорт звука {pct}%", pct)
            time.sleep(0.45)
    finally:
        try:
            project.DeleteRenderJob(job)
        except Exception:
            pass

    files = sorted(folder.glob(name + "*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise RuntimeError("Resolve завершил рендер, но WAV не найден.")
    return files[0]


def cached_model_source(model: str) -> str:
    """Reuse verified local large-v3 snapshot cache offline, matching original WhisperX GUI."""
    if model != "large-v3":
        return model
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    hf_home = Path(os.environ.get("HF_HOME", cache_home / "huggingface"))
    hub = Path(os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE") or hf_home / "hub")
    repo = hub / "models--Systran--faster-whisper-large-v3"
    try:
        revision = (repo / "refs" / "main").read_text(encoding="utf-8").strip()
        snapshot = repo / "snapshots" / revision
        required = ("model.bin", "config.json", "tokenizer.json", "vocabulary.json")
        if all((snapshot / n).is_file() for n in required) and (snapshot / "model.bin").stat().st_size == 3087284237:
            return str(snapshot.resolve())
    except OSError:
        pass
    return model
