"""Generate a ProRes 4444 RGBA foreground person cutout using Robust Video Matting on CUDA."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch


FFMPEG_FALLBACK = (
    Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe" / "ffmpeg-9.1-full_build" / "bin" / "ffmpeg.exe"
)
FFPROBE_FALLBACK = FFMPEG_FALLBACK.with_name("ffprobe.exe")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SHOT_CUT_THRESHOLD = 0.16


def find_tool(name, fallback):
    found = shutil.which(name)
    if found:
        return found
    if fallback.is_file():
        return str(fallback)
    raise FileNotFoundError(f"Не найден {name}.")


def probe_video_size(ffprobe, path):
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", path,
    ]
    out = subprocess.check_output(cmd, creationflags=NO_WINDOW)
    info = json.loads(out.decode("utf-8"))
    stream = info["streams"][0]
    return int(stream["width"]), int(stream["height"])


def ffmpeg_fps_str(fps):
    for nominal, frac in ((23.976, "24000/1001"), (29.97, "30000/1001"), (59.94, "60000/1001")):
        if abs(fps - nominal) < 0.01:
            return frac
    return f"{fps:.6f}"


def cache_path_for(cache_dir, video_path, start_sec, duration_sec, fps, max_frames=0):
    stat = os.stat(video_path)
    key = f"{os.path.abspath(video_path)}|{stat.st_size}|{stat.st_mtime_ns}|{start_sec:.3f}|{duration_sec:.3f}|{fps:.4f}|{max_frames}|rvm_r50_v1"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return Path(cache_dir) / f"person_matte_{digest}.mov"


def load_rvm_model(device):
    # Prefer local hub cache without network checks if already downloaded.
    hub_dir = Path(torch.hub.get_dir())
    cached_repos = sorted(hub_dir.glob("PeterL1n_RobustVideoMatting*"))
    if cached_repos:
        model = torch.hub.load(str(cached_repos[0]), "resnet50", source="local")
    else:
        model = torch.hub.load("PeterL1n/RobustVideoMatting", "resnet50", trust_repo=True)
    return model.to(device).eval()


def generate_person_matte(video_path, start_sec, duration_sec, fps, output_path, max_frames=0):
    ffmpeg = find_tool("ffmpeg", FFMPEG_FALLBACK)
    ffprobe = find_tool("ffprobe", FFPROBE_FALLBACK)
    width, height = probe_video_size(ffprobe, video_path)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    use_amp = device.type == "cuda"
    model = load_rvm_model(device)

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".tmp.mov")

    # Backbone downsample ratio targeting ~640px longest side for crisp hair/shoulder edges + high speed.
    downsample_ratio = min(0.5, max(0.25, 640.0 / max(width, height)))
    frame_bytes = width * height * 3
    fps_str = ffmpeg_fps_str(fps)

    dec_cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, start_sec):.4f}",
        "-i", video_path,
        "-r", fps_str,
        "-fps_mode", "cfr",
    ]
    if max_frames > 0:
        dec_cmd += ["-frames:v", str(max_frames)]
    else:
        dec_cmd += ["-t", f"{max(0.05, duration_sec):.4f}"]
    dec_cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]

    enc_cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-s", f"{width}x{height}",
        "-r", fps_str,
        "-i", "-",
        "-c:v", "prores_ks",
        "-profile:v", "4444",
        "-pix_fmt", "yuva444p10le",
        "-vendor", "apl0",
        "-qscale:v", "11",
        "-an",
        str(tmp_path),
    ]

    decoder = subprocess.Popen(dec_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    encoder = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)

    rec = [None] * 4
    prev_small = None
    rgba_buf = np.empty((height, width, 4), dtype=np.uint8)
    frames_done = 0

    try:
        with torch.no_grad():
            while True:
                raw = decoder.stdout.read(frame_bytes)
                if len(raw) < frame_bytes:
                    break
                rgb = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
                src = torch.from_numpy(rgb.copy()).to(device, non_blocking=True).permute(2, 0, 1).unsqueeze(0).float() / 255.0

                # Reset temporal state on hard shot cuts to prevent ghosting.
                small = src[:, :, ::16, ::16]
                if prev_small is not None:
                    if float((small - prev_small).abs().mean().item()) > SHOT_CUT_THRESHOLD:
                        rec = [None] * 4
                prev_small = small

                if use_amp:
                    with torch.amp.autocast("cuda"):
                        _fgr, pha, *rec = model(src, *rec, downsample_ratio=downsample_ratio)
                else:
                    _fgr, pha, *rec = model(src, *rec, downsample_ratio=downsample_ratio)

                # Clean micro-noise in background (<0.06 -> 0) and solidify interior (>0.92 -> 1).
                alpha = ((pha[0, 0].float() - 0.06) / (0.92 - 0.06)).clamp_(0.0, 1.0)
                alpha_u8 = (alpha * 255.0).round().to(torch.uint8).cpu().numpy()

                rgba_buf[:, :, :3] = rgb
                rgba_buf[:, :, 3] = alpha_u8
                encoder.stdin.write(rgba_buf.tobytes())
                frames_done += 1
    finally:
        if decoder.stdout:
            decoder.stdout.close()
        if encoder.stdin:
            encoder.stdin.close()
        dec_err = decoder.stderr.read().decode("utf-8", "replace") if decoder.stderr else ""
        enc_err = encoder.stderr.read().decode("utf-8", "replace") if encoder.stderr else ""
        dec_rc = decoder.wait()
        enc_rc = encoder.wait()

    if dec_rc != 0 or enc_rc != 0 or frames_done == 0 or not tmp_path.is_file():
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Ошибка генерации маски персонажа (frames={frames_done}, dec={dec_rc}, enc={enc_rc}): {dec_err} {enc_err}".strip())

    os.replace(tmp_path, out_path)
    return str(out_path), frames_done


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--fps", type=float, required=True)
    parser.add_argument("--frames", type=int, default=0)
    parser.add_argument("--output", default="")
    parser.add_argument("--cache-dir", default=str(Path.home() / "AppData" / "Local" / "WhisperX" / "mattes"))
    args = parser.parse_args()

    out_path = Path(args.output) if args.output else cache_path_for(
        args.cache_dir, args.input, args.start, args.duration, args.fps, args.frames
    )
    if out_path.is_file() and out_path.stat().st_size > 1024:
        print(json.dumps({"output": str(out_path), "cached": True}, ensure_ascii=False))
        return

    rendered, frames = generate_person_matte(
        args.input, args.start, args.duration, args.fps, out_path, max_frames=args.frames
    )
    print(json.dumps({"output": rendered, "cached": False, "frames": frames}, ensure_ascii=False))


if __name__ == "__main__":
    main()
