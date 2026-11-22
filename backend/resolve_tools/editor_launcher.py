"""Standalone launcher for the full DaVinci Resolve CaptionEditor window."""
import argparse
import os
import sys
import tkinter as tk
from pathlib import Path

RESOLVE_MODULES = Path(r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules")
DEFAULT_SUBTITLE_DIR = Path(r"C:\ishodniki\subtitles")


class EditorHost(tk.Tk):
    def __init__(self, destination: str, srt_path: str | None) -> None:
        super().__init__()
        self.withdraw()
        self.destination = tk.StringVar(value=destination or str(DEFAULT_SUBTITLE_DIR))

        tools_dir = str(Path(__file__).resolve().parent)
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        from caption_editor import CaptionEditor

        self.editor = CaptionEditor(self, srt_path or None)
        self.editor.protocol("WM_DELETE_WINDOW", self._on_close)
        self.editor.lift()
        self.editor.focus_force()

    def _on_close(self) -> None:
        try:
            self.editor.destroy()
        finally:
            self.destroy()

    def resolve_app(self):
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
        import DaVinciResolveScript as dvr
        resolve = dvr.scriptapp("Resolve")
        if not resolve:
            raise RuntimeError("DaVinci Resolve не отвечает через API. Проверьте, что программа открыта.")
        return resolve

    def resolve_fonts(self):
        fonts = self.resolve_app().Fusion().FontManager.GetFontList()
        return {
            family: tuple(sorted(styles, key=str.casefold))
            for family, styles in fonts.items()
            if family and styles
        }

    def connect_resolve(self):
        resolve = self.resolve_app()
        project = resolve.GetProjectManager().GetCurrentProject()
        timeline = project.GetCurrentTimeline() if project else None
        if not timeline:
            raise RuntimeError("В Resolve нет открытого таймлайна.")
        return project, timeline

    @staticmethod
    def timeline_seconds(timeline):
        fps = float(timeline.GetSetting("timelineFrameRate"))
        return max(0, timeline.GetEndFrame() - timeline.GetStartFrame()) / fps


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--srt", default="")
    parser.add_argument("--dest", default=str(DEFAULT_SUBTITLE_DIR))
    args = parser.parse_args()
    app = EditorHost(args.dest, args.srt or None)
    app.mainloop()


if __name__ == "__main__":
    main()
