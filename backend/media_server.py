import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import unquote


class LocalMediaServer:
    """Serves offline UI assets and registered local audio/video files with HTTP Range support."""

    def __init__(self, ui_dir: Path) -> None:
        self.ui_dir = ui_dir.resolve()
        self._media_map: Dict[str, Path] = {}
        self._lock = threading.RLock()
        self.server: Optional[ThreadingHTTPServer] = None
        self.port: int = 0

    def register_media(self, media_id: str, file_path: str) -> None:
        if not file_path:
            return
        p = Path(file_path)
        if p.exists() and p.is_file():
            with self._lock:
                self._media_map[media_id] = p

    def get_media_path(self, media_id: str) -> Optional[Path]:
        with self._lock:
            return self._media_map.get(media_id)

    def start(self) -> str:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:
                return  # keep console silent in desktop mode

            def do_GET(self) -> None:
                path_only = unquote(self.path.split("?", 1)[0])
                if path_only.startswith("/media/"):
                    media_id = path_only[len("/media/"):].strip("/")
                    fp = parent.get_media_path(media_id)
                    if not fp or not fp.exists():
                        self.send_error(404, "Media not found")
                        return
                    self._serve_file(fp, allow_range=True)
                    return

                rel = "index.html" if path_only in ("", "/") else path_only.lstrip("/")
                target = (parent.ui_dir / rel).resolve()
                if not str(target).startswith(str(parent.ui_dir)) or not target.exists() or not target.is_file():
                    self.send_error(404, "Not found")
                    return
                self._serve_file(target, allow_range=False)

            def _serve_file(self, fp: Path, allow_range: bool) -> None:
                ctype, _ = mimetypes.guess_type(str(fp))
                if fp.suffix.lower() == ".js":
                    ctype = "application/javascript; charset=utf-8"
                elif fp.suffix.lower() == ".css":
                    ctype = "text/css; charset=utf-8"
                elif fp.suffix.lower() == ".html":
                    ctype = "text/html; charset=utf-8"
                elif not ctype:
                    ctype = "application/octet-stream"

                size = fp.stat().st_size
                range_hdr = self.headers.get("Range") if allow_range else None

                if range_hdr and range_hdr.startswith("bytes="):
                    try:
                        spec = range_hdr.replace("bytes=", "").split(",")[0].strip()
                        start_s, end_s = spec.split("-")
                        start = int(start_s) if start_s else 0
                        end = int(end_s) if end_s else size - 1
                        end = min(end, size - 1)
                        length = max(0, end - start + 1)

                        self.send_response(206)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                        self.send_header("Content-Length", str(length))
                        self.end_headers()
                        with open(fp, "rb") as f:
                            f.seek(start)
                            remaining = length
                            while remaining > 0:
                                chunk = f.read(min(65536, remaining))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
                        return
                    except Exception:
                        pass

                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                if allow_range:
                    self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                with open(fp, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        t = threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        return f"http://127.0.0.1:{self.port}/"

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
