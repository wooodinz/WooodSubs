import os
import sys
from pathlib import Path

import webview

from backend.api import DesktopAPI
from backend.engine import WhisperXEngine
from backend.media_server import LocalMediaServer
from backend.storage import LocalStore


def get_bundle_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def main() -> None:
    root = get_bundle_root()
    ui_dir = root / "ui"

    store = LocalStore()
    media_server = LocalMediaServer(ui_dir)
    url = media_server.start()

    engine = WhisperXEngine(store, media_server)
    api = DesktopAPI(store, engine)

    window = webview.create_window(
        title="WooodSubs — wooodestic subtitles",
        url=url,
        js_api=api,
        width=1060,
        height=760,
        min_size=(840, 600),
        background_color="#111317",
        text_select=True,
    )
    api.attach_window(window)

    try:
        webview.start(debug=False)
    finally:
        media_server.stop()


if __name__ == "__main__":
    main()
