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


def probe_duration(path: str) -> float:
    """Get duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def has_audio_stream(path: str) -> bool:
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0",
           "-show_entries", "stream=codec_name", "-of", "csv=p=0", path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return bool(r.stdout.strip())


# --- Background music removal for Video 1 ---

def _demucs_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("demucs") is not None or shutil.which("demucs") is not None


def _run_demucs_vocals(in_path: str, out_wav: str, model: str = "htdemucs") -> bool:
    """
    Try Facebook Demucs to extract vocals (voice) and drop bg music.
    Uses htdemucs_ft (fine-tuned, best) with fallback to htdemucs.
    Returns True on success and writes out_wav (48k wav).
    Falls back to False if demucs not installed or fails.
    """
    if not _demucs_available():
        return False
    # Try cached best model first (htdemucs already downloaded), then try better variants
    for try_model in (model, "htdemucs_ft", "mdx_extra"):
        try:
            tmp_dir = os.path.join(WORKDIR, "_demucs_out")
            if os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir)
            os.makedirs(tmp_dir, exist_ok=True)
            import sys
            py_exe = sys.executable  # robust on Windows (py launcher)
            cmd = [
                py_exe, "-m", "demucs",
                "--two-stems=vocals", "-n", try_model,
                "-d", "cpu",
                "-o", tmp_dir, in_path,
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
            except Exception:
                cmd2 = ["demucs", "--two-stems=vocals", "-n", try_model, "-d", "cpu", "-o", tmp_dir, in_path]
                subprocess.run(cmd2, check=True, capture_output=True, text=True, timeout=600)

            found = glob.glob(os.path.join(tmp_dir, "**", "vocals.wav"), recursive=True)
            if not found:
                print(f"[music-remover] model {try_model} produced no vocals.wav, trying next")
                continue
            cand = found[0]
            # convert to 48k stereo wav for muxing
            subprocess.run([
                "ffmpeg", "-y", "-i", cand,
                "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", out_wav
            ], check=True, capture_output=True)
            if os.path.isfile(out_wav):
                print(f"[music-remover] Demucs {try_model} success -> {out_wav}")
                return True
        except Exception as e:
            print(f"[music-remover] Demucs {try_model} failed: {e}")
            continue
    return False


def _prepare_video1_without_bgm(video1: str, workdir: str = WORKDIR) -> str:
    """
    Create a copy of video1 where bg music is removed (vocals kept).
    Tries Demucs (AI) first; falls back to lightweight FFmpeg vocal isolation.
    Returns path to new video file (or original if no audio / failed).
    Caller must use the returned path as input 0 for merging.
    """
    if not has_audio_stream(video1):
        return video1

    # Try AI separation -> produce clean vocals wav, then mux
    vocals_wav = os.path.join(workdir, "_v1_vocals.wav")
    if _run_demucs_vocals(video1, vocals_wav):
        out_path = os.path.join(workdir, "_v1_nobgm.mp4")
        # mux original video + cleaned vocals (ignore original audio)
        try:
            subprocess.run([
                "ffmpeg", "-y",
                "-i", video1,
                "-i", vocals_wav,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest",
                out_path
            ], check=True, capture_output=True, text=True)
            if os.path.isfile(out_path):
                print(f"[music-remover] Demucs vocals isolated -> {out_path}")
                return out_path
        except subprocess.CalledProcessError as e:
            print(f"[music-remover] mux failed: {e.stderr[-400:]}")

    # Fallback will be handled inline via ffmpeg filter (no pre-processing needed).
    # We return original and signal caller to apply FFmpeg filter.
    # To indicate fallback needed, we return original path - caller checks _demucs_available() result
    # via a sentinel file. Simpler: return original, and merge_videos will apply filter branch.
    return video1


# For fallback, this audio filter keeps voice band and suppresses music
# Center vocal + bandpass 120-3000Hz + noise suppress + compress -> stereo
FF_FALLBACK_VOCAL_FILTER = (
    "aformat=channel_layouts=mono,"
    "highpass=f=120:width_type=o:width=1,"
    "lowpass=f=3000:width_type=o:width=1,"
    "afftdn=nf=-20:tn=1:nr=12,"
    "acompressor=threshold=-20dB:ratio=6:attack=20:release=250,"
    "aresample=48000,"
    "pan=stereo|c0=c0|c1=c0"
)


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


def _insert_ad(
    base_path: str,
    ad_path: str,
    insert_sec: float,
    out_path: str,
    W: int,
    H: int,
    fps: int = 30,
) -> str:
    """
    Insert ad_path at insert_sec into base_path (which is already rendered).
    Produces: [0:00 -> insert_sec] + [ad full] + [insert_sec -> end]
    Ad is scaled/padded to W x H and its audio is kept.
    """
    total_dur = probe_duration(base_path)
    if total_dur <= insert_sec + 0.5:
        # Too short to insert - just copy base to out_path
        shutil.copy2(base_path, out_path)
        return out_path

    # Clamp insert point
    insert_sec = max(1.0, min(insert_sec, total_dur - 1.0))

    # All audio segments are normalized to same format so concat works
    filter_complex = (
        f"[0:v]trim=start=0:end={insert_sec},setpts=PTS-STARTPTS[pre_v];"
        f"[0:a]atrim=start=0:end={insert_sec},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[pre_a];"
        f"[0:v]trim=start={insert_sec},setpts=PTS-STARTPTS[post_v];"
        f"[0:a]atrim=start={insert_sec},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[post_a];"
        f"[1:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p[ad_v0];"
        f"[ad_v0]fps={fps}[ad_v];"
        f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[ad_a];"
        f"[pre_v][pre_a][ad_v][ad_a][post_v][post_a]concat=n=3:v=1:a=1[vout][aout]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", base_path,
        "-i", ad_path,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out_path


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
    ad_path: str = None,
    ad_insert_sec: float = 15.0,
    ad_random: bool = False,
    remove_bgm: bool = False,
    mute_video1: bool = False,
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

    Ad insertion:
        if ad_path is provided and exists, the merged video is cut at
        ad_insert_sec (or random point >= ad_insert_sec if ad_random=True)
        and the ad is inserted fullscreen (scaled to W x H) with its own audio.
        Flow: [merged 0 -> insert] + [ad full] + [merged insert -> end]

    BGM removal:
        if remove_bgm=True, Video 1 audio is processed to remove background music
        and keep voice. Tries AI Demucs (htdemucs_ft -> htdemucs) first; if not
        installed falls back to an FFmpeg vocal band + denoise filter.
        Install AI: pip install demucs torch --index-url https://download.pytorch.org/whl/cpu
        If mute_video1=True, Video 1 audio is totally muted (volume=0) -> 100% music removal.
    """
    # --- Optional: audio handling for Video 1 ---
    original_v1 = video1
    use_ff_fallback = False
    # 1) Total mute has priority (100% removal)
    if mute_video1:
        # will be handled via audio filter volume=0 later, no pre-processing needed
        print("[merge] Video1 will be TOTALLY MUTED (volume=0) -> 100% music removal")
    elif remove_bgm:
        cleaned = _prepare_video1_without_bgm(video1)
        if cleaned != video1 and os.path.isfile(cleaned):
            print(f"[merge] Using AI-cleaned Video1: {cleaned}")
            video1 = cleaned
        else:
            # Demucs not available -> will use FFmpeg vocal filter inline
            if has_audio_stream(video1):
                use_ff_fallback = True
                print("[merge] Demucs not available, using FFmpeg vocal fallback filter")

    w1, h1 = probe_dimensions(video1)
    W, H = w1, h1

    mask_path, feather = generate_feather_mask(W, H, feather_min, feather_max, seed)
    tpl_path = prepare_template(W, H, template) if template else None
    out_path = os.path.join(WORKDIR, out_name)

    fps = 30
    half = (W + feather) // 2
    x1 = W - half
    # Audio chain for Video 1: mute > vocal-isolation > plain
    if mute_video1:
        # total mute: volume 0 then atempo, still keeps sync but silence
        audio_filter = f"[0:a]volume=0,atempo={speed1}[aout]"
    elif use_ff_fallback:
        audio_filter = f"[0:a]{FF_FALLBACK_VOCAL_FILTER},atempo={speed1}[aout]"
    else:
        audio_filter = f"[0:a]atempo={speed1}[aout]"

    filter_complex = (
        f"[0:v]setpts=PTS/{speed1},scale={half}:{H},setsar=1,fps={fps},pad={W}:{H}:0:0[v0];"
        f"[1:v]setpts=PTS/{speed2},scale={half}:{H},setsar=1,fps={fps},pad={W}:{H}:{x1}:0[v1];"
        f"[2:v]scale={W}:{H},format=gray[msk];"
        f"[v1][msk]alphamerge[ovl];"
        f"[v0][ovl]overlay=format=auto[vout];"
        f"{audio_filter}"
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

    # Render to a temp file first so we can optionally insert an ad
    tmp_out = out_path if not ad_path else os.path.join(WORKDIR, "_merged_tmp.mp4")
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", map_out, "-map", "[aout]",
        "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac",
        tmp_out,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    # --- Ad insertion ---
    if ad_path and os.path.isfile(ad_path):
        insert_sec = float(ad_insert_sec) if ad_insert_sec else 15.0
        if ad_random:
            dur = probe_duration(tmp_out)
            # random between insert_sec and dur-2s (keep at least 2s tail)
            max_start = max(insert_sec, dur - 2.0)
            if max_start > insert_sec:
                rng = random.Random(seed if seed not in (None, 0) else None)
                insert_sec = rng.uniform(insert_sec, max_start)
        try:
            _insert_ad(tmp_out, ad_path, insert_sec, out_path, W, H, fps=fps)
            # cleanup temp
            if tmp_out != out_path and os.path.isfile(tmp_out):
                try:
                    os.remove(tmp_out)
                except OSError:
                    pass
        except subprocess.CalledProcessError as e:
            # Fallback: return base video if ad insertion fails
            print(f"Ad insertion failed, returning base video: {e.stderr[-500:]}")
            if tmp_out != out_path:
                shutil.copy2(tmp_out, out_path)
    else:
        if tmp_out != out_path:
            shutil.move(tmp_out, out_path)

    return out_path, feather