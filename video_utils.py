"""
Core logic for the Split-Screen Feather Merge Tool.

Pipeline:
1. Download two YouTube Shorts via yt-dlp
2. Speed up video 1 slightly (left), slow down video 2 slightly (right)
3. Generate a horizontal gradient mask (feathered seam, random width 10-15px)
4. Use ffmpeg's maskedmerge to blend: left side = video1, right side = video2,
   with a soft feathered transition in the middle
"""

import glob
import os
import random
import shutil
import subprocess
import json
import numpy as np
from PIL import Image

WORKDIR = "workdir"
os.makedirs(WORKDIR, exist_ok=True)


def _ensure_ffmpeg_on_path():
    if shutil.which("ffmpeg"):
        return
    root = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages"
    )
    for base in glob.glob(os.path.join(root, "Gyan.FFmpeg*")):
        for bindir in glob.glob(os.path.join(base, "*", "bin")):
            if os.path.isfile(os.path.join(bindir, "ffmpeg.exe")):
                os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
                return


_ensure_ffmpeg_on_path()


def prepare_template(width: int, height: int, src: str = "banner.png") -> str:
    """
    Turn the frame image (banners + baked-in videos) into an overlay:
    everything except the top/bottom banners becomes transparent, so the
    merged videos show through the middle window. Banner bounds are
    auto-detected by scanning the left/right edge strips for the purple
    banner color.
    """
    img = Image.open(src).convert("RGBA")
    img = img.resize((width, height), Image.LANCZOS)
    a = np.asarray(img).astype(int)
    H, W = a.shape[:2]

    strips = np.concatenate([a[:, :12, :3], a[:, -12:, :3]], axis=1)
    purple = (
        (strips[:, :, 2] > 100)
        & (strips[:, :, 2] > strips[:, :, 1] + 40)
        & (strips[:, :, 0] > 40)
    )
    frac = purple.mean(axis=1)

    top_end = next(y for y in range(H) if y > H * 0.03 and frac[y] < 0.5)
    bottom_start = next(
        y for y in range(H - 1, -1, -1) if y < H * 0.97 and frac[y] < 0.5
    ) + 1

    margin = 2
    alpha = np.full((H, W), 255, dtype=np.uint8)
    alpha[max(0, top_end - margin):min(H, bottom_start + margin), :] = 0
    a[:, :, 3] = alpha

    out = os.path.join(WORKDIR, "template.png")
    Image.fromarray(a.astype(np.uint8), mode="RGBA").save(out)
    return out


def download_short(url: str, out_name: str) -> str:
    """Download a YouTube Shorts URL to an mp4 file. Returns the file path."""
    import yt_dlp

    out_path = os.path.join(WORKDIR, out_name)
    ydl_opts = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/mp4/best",
        "outtmpl": out_path,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return out_path


def probe_dimensions(path: str):
    """Get width/height of a video using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    stream = info["streams"][0]
    return stream["width"], stream["height"]


def generate_feather_mask(width: int, height: int, feather_min=45, feather_max=85,
                           seed=None) -> tuple[str, int]:
    """
    Create a black->white horizontal gradient mask image.
    Black (left) = video1 stays visible, white (right) = video2 shows through.
    The transition band sits exactly at the horizontal center and uses a
    smoothstep (cosine) ramp for a soft, natural seam like split-screen edits.
    """
    rng = random.Random(seed)
    lo, hi = sorted((int(feather_min), int(feather_max)))
    feather = max(1, min(rng.randint(lo, hi), width // 3))

    start = (width - feather) / 2.0
    x = np.arange(width, dtype=np.float32)
    t = np.clip((x - start) / feather, 0.0, 1.0)
    ramp = 0.5 * (1.0 - np.cos(np.pi * t))
    mask_2d = np.tile(ramp * 255.0, (height, 1)).astype(np.uint8)

    img = Image.fromarray(mask_2d, mode="L")
    mask_path = os.path.join(WORKDIR, "mask.png")
    img.save(mask_path)
    return mask_path, feather


def merge_videos(
    video1: str,
    video2: str,
    speed1: float = 1.08,   # video1 (left) - slightly faster
    speed2: float = 0.92,   # video2 (right) - slightly slower
    feather_min: int = 45,
    feather_max: int = 85,
    seed: int = None,
    out_name: str = "output.mp4",
    template: str = None,
) -> tuple[str, int]:
    """
    video1 -> left side, full length kept, its audio is the output audio.
    video2 -> right side, muted; if shorter than the left video it is
              looped seamlessly until it matches the left duration.
    Both are blended with a feathered vertical seam in the middle (width
    randomized between feather_min and feather_max px) using
    alphamerge+overlay so the gradient mask blends every plane cleanly.
    If `template` (frame image with banners) is given, it is overlaid on top
    with the video window made transparent.
    """
    w1, h1 = probe_dimensions(video1)
    W, H = w1, h1

    mask_path, feather = generate_feather_mask(W, H, feather_min, feather_max, seed)
    tpl_path = prepare_template(W, H, template) if template else None
    out_path = os.path.join(WORKDIR, out_name)

    fps = 30
    half = (W + feather) // 2
    x1 = W - half
    filter_complex = (
        f"[0:v]setpts=PTS/{speed1},scale={half}:{H},setsar=1,fps={fps},pad={W}:{H}:0:0[v0];"
        f"[1:v]setpts=PTS/{speed2},scale={half}:{H},setsar=1,fps={fps},pad={W}:{H}:{x1}:0[v1];"
        f"[2:v]scale={W}:{H},format=gray[msk];"
        f"[v1][msk]alphamerge[ovl];"
        f"[v0][ovl]overlay=format=auto[vout];"
        f"[0:a]atempo={speed1}[aout]"
    )

    inputs = [
        "-i", video1,
        "-stream_loop", "-1", "-i", video2,
        "-loop", "1", "-framerate", str(fps), "-i", mask_path,
    ]
    if tpl_path:
        filter_complex += f";[3:v]scale={W}:{H},format=rgba[tpl];[vout][tpl]overlay=format=auto[vfinal]"
        map_out = "[vfinal]"
        inputs += ["-i", tpl_path]
    else:
        map_out = "[vout]"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", map_out, "-map", "[aout]",
        "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out_path, feather