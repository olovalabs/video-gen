"""
YouTube Shorts Batch Downloader & Automated 50-Video Split-Screen Studio.
Runs locally with Gradio.
"""

import os
import sys

# Force UTF-8 across all Python processes on Windows
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

import time
import traceback
import gradio as gr

from downloader import download_channel_batch
from batch_pipeline import pipeline_instance
from video_utils import merge_videos

DEFAULT_TEMPLATE = "banner.png"
DEFAULT_AD = "ads.mp4"
DEFAULT_CH1_DIR = os.path.abspath("downloads/first_videos")
DEFAULT_CH2_DIR = os.path.abspath("downloads/second_videos")
DEFAULT_OUT_DIR = os.path.abspath("output_videos")

os.makedirs(DEFAULT_CH1_DIR, exist_ok=True)
os.makedirs(DEFAULT_CH2_DIR, exist_ok=True)
os.makedirs(DEFAULT_OUT_DIR, exist_ok=True)


def _unwrap_file(x):
    """Normalize Gradio video/image inputs (can be dict, list, or path string)."""
    if x is None:
        return None
    if isinstance(x, dict):
        return x.get("path") or x.get("name")
    if isinstance(x, (list, tuple)) and x:
        return _unwrap_file(x[0])
    return x


def get_folder_summary(dir1: str, dir2: str, out_dir: str) -> str:
    """Return status summary of the 3 folders."""
    exts = (".mp4", ".mkv", ".mov", ".webm")
    c1 = len([f for f in os.listdir(dir1) if f.lower().endswith(exts)]) if os.path.isdir(dir1) else 0
    c2 = len([f for f in os.listdir(dir2) if f.lower().endswith(exts)]) if os.path.isdir(dir2) else 0
    co = len([f for f in os.listdir(out_dir) if f.lower().endswith(exts)]) if os.path.isdir(out_dir) else 0
    return f"📂 **First Videos:** {c1} available  |  📂 **Second Videos:** {c2} available  |  🎬 **Output Videos:** {co} generated"


# -------------------------------------------------------------
# TAB 1: 1-Click Complete Pipeline
# -------------------------------------------------------------

def run_full_pipeline(
    ch1_url: str,
    ch1_count: int,
    ch1_sort: str,
    ch2_url: str,
    ch2_count: int,
    ch2_sort: str,
    speed1: float,
    speed2: float,
    feather_min: int,
    feather_max: int,
    seed: int,
    template,
    enable_ad: bool,
    ad_file,
    ad_time: float,
    ad_random: bool,
    audio_mode: str,
    pairing_mode: str,
    progress=gr.Progress(track_tqdm=True),
):
    if not ch1_url or not ch1_url.strip():
        yield None, "❌ Please provide a YouTube Channel URL for Channel 1 (First Videos).", "Error: Missing Channel 1 URL"
        return
    if not ch2_url or not ch2_url.strip():
        yield None, "❌ Please provide a YouTube Channel URL for Channel 2 (Second Videos).", "Error: Missing Channel 2 URL"
        return

    tpl = _unwrap_file(template) or (DEFAULT_TEMPLATE if os.path.isfile(DEFAULT_TEMPLATE) else None)
    ad_path = _unwrap_file(ad_file) or (DEFAULT_AD if os.path.isfile(DEFAULT_AD) else None)

    accumulated_logs = []

    def log(msg: str):
        timestamp = time.strftime("%H:%M:%S")
        accumulated_logs.append(f"[{timestamp}] {msg}")
        return "\n".join(accumulated_logs[-30:])

    # --- Phase 1: Download Channel 1 ---
    progress(0.05, desc="[1/3] Downloading Channel 1 Popular Shorts...")
    yield None, log(f"🚀 Starting Channel 1 download ({ch1_count} {ch1_sort} shorts)..."), "Downloading Channel 1..."

    download_channel_batch(
        channel_url=ch1_url,
        output_dir=DEFAULT_CH1_DIR,
        count=int(ch1_count),
        sort="popular" if "popular" in ch1_sort.lower() else "latest",
        is_shorts=True,
        log_callback=lambda m: log(m),
    )

    # --- Phase 2: Download Channel 2 ---
    progress(0.20, desc="[2/3] Downloading Channel 2 Videos...")
    yield None, log(f"🚀 Starting Channel 2 download ({ch2_count} {ch2_sort} videos)..."), "Downloading Channel 2..."

    download_channel_batch(
        channel_url=ch2_url,
        output_dir=DEFAULT_CH2_DIR,
        count=int(ch2_count),
        sort="popular" if "popular" in ch2_sort.lower() else "latest",
        is_shorts=True,
        log_callback=lambda m: log(m),
    )

    # --- Phase 3: Batch Video Generation ---
    progress(0.35, desc="[3/3] Starting Batch Video Generation...")
    yield None, log(f"🎬 Initializing sequential video generation queue..."), "Starting Generation..."

    started = pipeline_instance.start_batch(
        first_folder=DEFAULT_CH1_DIR,
        second_folder=DEFAULT_CH2_DIR,
        output_folder=DEFAULT_OUT_DIR,
        speed1=speed1,
        speed2=speed2,
        feather_min=feather_min,
        feather_max=feather_max,
        seed=seed,
        template=tpl,
        enable_ad=enable_ad,
        ad_file=ad_path,
        ad_time=ad_time,
        ad_random=ad_random,
        audio_mode=audio_mode,
        pairing_mode=pairing_mode,
    )

    if not started:
        yield None, log("❌ Failed to launch batch generator (it may already be running)."), "Error launching generator"
        return

    last_rendered = None
    while pipeline_instance.is_running:
        state = pipeline_instance.get_state()
        cur_idx = state["current_index"]
        tot = state["total_count"]
        if tot > 0:
            # Map batch generation from 0.35 to 1.0 progress
            p = 0.35 + (cur_idx / tot) * 0.65
            progress(p, desc=f"Rendering {cur_idx}/{tot}: {state['current_title'][:30]}")

        if state["last_rendered_file"] != last_rendered and state["last_rendered_file"]:
            last_rendered = state["last_rendered_file"]

        logs_txt = "\n".join(state["logs"][-30:])
        status_txt = f"{state['current_status']} ({cur_idx}/{tot})"
        yield last_rendered, logs_txt, status_txt
        time.sleep(1.0)

    final_state = pipeline_instance.get_state()
    final_logs = "\n".join(final_state["logs"][-30:])
    final_status = f"✅ Finished! {final_state['completed_count']} videos generated into '{DEFAULT_OUT_DIR}'"
    yield final_state["last_rendered_file"], final_logs, final_status


# -------------------------------------------------------------
# TAB 2: Downloader Callbacks
# -------------------------------------------------------------

def run_download_ch1(url: str, count: int, sort: str, progress=gr.Progress()):
    if not url or not url.strip():
        return "❌ Please enter a Channel 1 URL", None
    logs = []
    def _log(m):
        logs.append(m)
    progress(0.1, desc="Fetching channel shorts...")
    download_channel_batch(
        channel_url=url,
        output_dir=DEFAULT_CH1_DIR,
        count=int(count),
        sort="popular" if "popular" in sort.lower() else "latest",
        is_shorts=True,
        log_callback=_log,
    )
    summary = get_folder_summary(DEFAULT_CH1_DIR, DEFAULT_CH2_DIR, DEFAULT_OUT_DIR)
    return "\n".join(logs[-25:]), summary


def run_download_ch2(url: str, count: int, sort: str, progress=gr.Progress()):
    if not url or not url.strip():
        return "❌ Please enter a Channel 2 URL", None
    logs = []
    def _log(m):
        logs.append(m)
    progress(0.1, desc="Fetching channel videos...")
    download_channel_batch(
        channel_url=url,
        output_dir=DEFAULT_CH2_DIR,
        count=int(count),
        sort="popular" if "popular" in sort.lower() else "latest",
        is_shorts=True,
        log_callback=_log,
    )
    summary = get_folder_summary(DEFAULT_CH1_DIR, DEFAULT_CH2_DIR, DEFAULT_OUT_DIR)
    return "\n".join(logs[-25:]), summary


# -------------------------------------------------------------
# TAB 3: Batch Generator Callbacks
# -------------------------------------------------------------

def start_standalone_batch(
    dir1: str,
    dir2: str,
    out_dir: str,
    speed1: float,
    speed2: float,
    feather_min: int,
    feather_max: int,
    seed: int,
    template,
    enable_ad: bool,
    ad_file,
    ad_time: float,
    ad_random: bool,
    audio_mode: str,
    pairing_mode: str,
    progress=gr.Progress(),
):
    dir1 = dir1 or DEFAULT_CH1_DIR
    dir2 = dir2 or DEFAULT_CH2_DIR
    out_dir = out_dir or DEFAULT_OUT_DIR

    tpl = _unwrap_file(template) or (DEFAULT_TEMPLATE if os.path.isfile(DEFAULT_TEMPLATE) else None)
    ad_path = _unwrap_file(ad_file) or (DEFAULT_AD if os.path.isfile(DEFAULT_AD) else None)

    started = pipeline_instance.start_batch(
        first_folder=dir1,
        second_folder=dir2,
        output_folder=out_dir,
        speed1=speed1,
        speed2=speed2,
        feather_min=feather_min,
        feather_max=feather_max,
        seed=seed,
        template=tpl,
        enable_ad=enable_ad,
        ad_file=ad_path,
        ad_time=ad_time,
        ad_random=ad_random,
        audio_mode=audio_mode,
        pairing_mode=pairing_mode,
    )

    if not started:
        yield None, "❌ Batch is already running!", "Already running"
        return

    last_rendered = None
    while pipeline_instance.is_running:
        state = pipeline_instance.get_state()
        cur = state["current_index"]
        tot = state["total_count"]
        if tot > 0:
            progress(cur / tot, desc=f"[{cur}/{tot}] {state['current_title'][:30]}")
        if state["last_rendered_file"] != last_rendered and state["last_rendered_file"]:
            last_rendered = state["last_rendered_file"]
        logs_txt = "\n".join(state["logs"][-30:])
        status_txt = f"{state['current_status']}"
        yield last_rendered, logs_txt, status_txt
        time.sleep(1.0)

    final_state = pipeline_instance.get_state()
    yield final_state["last_rendered_file"], "\n".join(final_state["logs"][-30:]), final_state["current_status"]


def stop_batch_queue():
    pipeline_instance.stop()
    return "Queue stop requested."


def pause_batch_queue():
    pipeline_instance.pause()
    return "Queue paused."


def resume_batch_queue():
    pipeline_instance.resume()
    return "Queue resumed."


# -------------------------------------------------------------
# TAB 4: Single Video Merge (Original studio feature preserved)
# -------------------------------------------------------------

def process_single(
    video1, video2, speed1, speed2, feather_min, feather_max, seed, template,
    enable_ad=True, ad_file=None, ad_time=15, ad_random=False, audio_mode="Keep original"
):
    try:
        video1 = _unwrap_file(video1)
        video2 = _unwrap_file(video2)
        template = _unwrap_file(template)
        ad_file = _unwrap_file(ad_file)
        if not video1 or not video2:
            return None, "Please upload both videos first."

        if feather_min > feather_max:
            feather_min, feather_max = feather_max, feather_min

        seed_val = int(seed) if seed not in (None, "", 0) else None
        tpl = template or (DEFAULT_TEMPLATE if os.path.isfile(DEFAULT_TEMPLATE) else None)

        ad_path = None
        if enable_ad:
            if ad_file and os.path.isfile(ad_file):
                ad_path = ad_file
            elif os.path.isfile(DEFAULT_AD):
                ad_path = DEFAULT_AD

        mute_flag = "Totally mute" in str(audio_mode)
        remove_bgm_flag = "Remove BGM" in str(audio_mode)

        out_path, feather_used = merge_videos(
            video1, video2,
            speed1=speed1,
            speed2=speed2,
            feather_min=int(feather_min),
            feather_max=int(feather_max),
            seed=seed_val,
            template=tpl,
            ad_path=ad_path,
            ad_insert_sec=float(ad_time) if ad_time else 15.0,
            ad_random=bool(ad_random),
            remove_bgm=remove_bgm_flag,
            mute_video1=mute_flag,
        )
        status = f"Done! Feather width: {feather_used}px"
        return out_path, status
    except Exception as e:
        return None, f"Error: {e}\n\n{traceback.format_exc()}"


# -------------------------------------------------------------
# GRADIO UI DEFINITION
# -------------------------------------------------------------

custom_css = """
/* Modern Dark Glassmorphism Theme */
body, .gradio-container {
    background-color: #0b0f19 !important;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
    color: #e2e8f0 !important;
}

.gradio-container h1, .gradio-container h2, .gradio-container h3 {
    color: #f8fafc !important;
    font-weight: 700 !important;
    letter-spacing: -0.02em !important;
}

/* Glass Card containers */
.glass-card {
    background: rgba(17, 24, 39, 0.75) !important;
    backdrop-filter: blur(12px) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    border-radius: 14px !important;
    padding: 18px !important;
    box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5) !important;
    margin-bottom: 14px !important;
}

/* Vibrant Primary Buttons */
.btn-primary {
    background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%) !important;
    border: none !important;
    color: white !important;
    font-weight: 600 !important;
    border-radius: 10px !important;
    box-shadow: 0 4px 14px 0 rgba(79, 70, 229, 0.4) !important;
    transition: all 0.2s ease !important;
}

.btn-primary:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 6px 20px 0 rgba(79, 70, 229, 0.6) !important;
}

/* Stop Button */
.btn-danger {
    background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%) !important;
    border: none !important;
    color: white !important;
    font-weight: 600 !important;
    border-radius: 10px !important;
}

/* Badges and tags */
.badge-ready {
    display: inline-block;
    background: rgba(16, 185, 129, 0.15);
    border: 1px solid #10b981;
    color: #34d399;
    padding: 4px 10px;
    border-radius: 9999px;
    font-size: 0.82rem;
    font-weight: 600;
}

/* Terminal log box */
.terminal-box textarea {
    font-family: "Consolas", "Fira Code", monospace !important;
    background: #060911 !important;
    color: #38bdf8 !important;
    border: 1px solid rgba(56, 189, 248, 0.2) !important;
    border-radius: 8px !important;
    font-size: 0.85rem !important;
    line-height: 1.4 !important;
}
"""

with gr.Blocks(title="YouTube Shorts Studio & 50-Video Batch Generator") as demo:
    gr.Markdown(
        """
        # 🎬 YouTube Shorts Batch Downloader & 50-Video Generator
        **Download the 50 most popular Shorts at 720x1280 HD MP4, pair with 20 background videos, and batch generate merged videos sequentially.**
        """
    )

    with gr.Tabs():

        # =========================================================
        # TAB 1: 1-Click Automated Pipeline
        # =========================================================
        with gr.TabItem("⚡ 1-Click Complete Pipeline"):
            with gr.Row():
                with gr.Column(scale=6):
                    with gr.Group(elem_classes=["glass-card"]):
                        gr.Markdown("### 1. YouTube Channels Setup")
                        with gr.Row():
                            auto_ch1 = gr.Textbox(
                                label="Channel 1 URL (First Videos - Shorts to Download)",
                                placeholder="e.g. https://www.youtube.com/@Channel1",
                                value="",
                            )
                            auto_ch1_count = gr.Slider(5, 100, value=50, step=1, label="Shorts Count (Default: 50)")
                            auto_ch1_sort = gr.Radio(
                                choices=["Most Popular (Highest Views)", "Latest Videos"],
                                value="Most Popular (Highest Views)",
                                label="Sort Order",
                            )

                        with gr.Row():
                            auto_ch2 = gr.Textbox(
                                label="Channel 2 URL (Second Videos - Background Clips)",
                                placeholder="e.g. https://www.youtube.com/@GameplayChannel",
                                value="",
                            )
                            auto_ch2_count = gr.Slider(5, 50, value=20, step=1, label="Background Count (Default: 20)")
                            auto_ch2_sort = gr.Radio(
                                choices=["Most Popular (Highest Views)", "Latest Videos"],
                                value="Most Popular (Highest Views)",
                                label="Sort Order",
                            )

                    with gr.Accordion("⚙️ Video Merge & Audio Settings (Expand to customize)", open=False):
                        with gr.Row():
                            auto_speed1 = gr.Slider(1.0, 1.5, value=1.08, step=0.01, label="Video 1 Speed (Left)")
                            auto_speed2 = gr.Slider(0.5, 1.0, value=0.92, step=0.01, label="Video 2 Speed (Right)")
                        with gr.Row():
                            auto_feather_min = gr.Slider(10, 200, value=45, step=1, label="Feather Min (px)")
                            auto_feather_max = gr.Slider(10, 200, value=85, step=1, label="Feather Max (px)")
                            auto_seed = gr.Number(value=0, label="Random Seed (0 = auto)")
                        with gr.Row():
                            auto_pairing = gr.Radio(
                                choices=["cycle", "random"],
                                value="cycle",
                                label="Pairing Mode (cycle = Video 1 to 20 looped; random = random pick)",
                            )
                            auto_audio_mode = gr.Radio(
                                choices=[
                                    "Keep original",
                                    "Remove BGM (keep voice - AI Demucs)",
                                    "Totally mute Video 1 (100%)",
                                ],
                                value="Keep original",
                                label="Audio Mode for Video 1",
                            )
                        with gr.Row():
                            auto_enable_ad = gr.Checkbox(value=True, label="Insert Ad (ads.mp4)")
                            auto_ad_time = gr.Number(value=15, label="Insert after (seconds)")
                            auto_ad_random = gr.Checkbox(value=False, label="Random point >= seconds")
                        with gr.Row():
                            auto_template = gr.Image(label="Template Banner (Default: banner.png)", type="filepath")
                            auto_ad_file = gr.Video(label="Ad Video File (Default: ads.mp4)")

                    with gr.Row():
                        run_pipeline_btn = gr.Button(
                            "⚡ Start Complete Pipeline (Download + Generate 50 Videos)",
                            variant="primary",
                            elem_classes=["btn-primary"],
                            scale=3,
                        )
                        stop_pipeline_btn = gr.Button("⏹️ Stop Queue", elem_classes=["btn-danger"], scale=1)

                with gr.Column(scale=5):
                    with gr.Group(elem_classes=["glass-card"]):
                        gr.Markdown("### 📺 Live Preview & Queue Monitor")
                        auto_status_box = gr.Textbox(label="Current Status", value="Idle", interactive=False)
                        auto_preview_player = gr.Video(label="Most Recently Generated Video (Inline Preview & Download)")
                        auto_logs_box = gr.Textbox(
                            label="Live Process Logs",
                            lines=10,
                            interactive=False,
                            elem_classes=["terminal-box"],
                        )

            run_pipeline_btn.click(
                fn=run_full_pipeline,
                inputs=[
                    auto_ch1, auto_ch1_count, auto_ch1_sort,
                    auto_ch2, auto_ch2_count, auto_ch2_sort,
                    auto_speed1, auto_speed2, auto_feather_min, auto_feather_max, auto_seed,
                    auto_template, auto_enable_ad, auto_ad_file, auto_ad_time, auto_ad_random,
                    auto_audio_mode, auto_pairing,
                ],
                outputs=[auto_preview_player, auto_logs_box, auto_status_box],
            )

            stop_pipeline_btn.click(
                fn=stop_batch_queue,
                inputs=[],
                outputs=[auto_status_box],
            )

        # =========================================================
        # TAB 2: YouTube Channel Downloader
        # =========================================================
        with gr.TabItem("📥 YouTube Channel Downloader"):
            gr.Markdown("### Download YouTube Shorts / Videos conformed directly to MP4 (720x1280 HD)")

            with gr.Row():
                with gr.Column():
                    with gr.Group(elem_classes=["glass-card"]):
                        gr.Markdown("#### 📥 Channel 1: First Videos (Target: 50 Popular Shorts)")
                        dl_ch1_url = gr.Textbox(label="Channel URL", placeholder="https://www.youtube.com/@Channel1")
                        with gr.Row():
                            dl_ch1_count = gr.Number(value=50, label="Video Count", precision=0)
                            dl_ch1_sort = gr.Radio(
                                choices=["Most Popular (Highest Views)", "Latest Videos"],
                                value="Most Popular (Highest Views)",
                                label="Sorting",
                            )
                        dl_ch1_btn = gr.Button("📥 Download Channel 1 Shorts (720x1280)", variant="primary", elem_classes=["btn-primary"])
                        dl_ch1_logs = gr.Textbox(label="Download Logs", lines=6, interactive=False, elem_classes=["terminal-box"])

                with gr.Column():
                    with gr.Group(elem_classes=["glass-card"]):
                        gr.Markdown("#### 📥 Channel 2: Second Videos (Target: 20 Background Clips)")
                        dl_ch2_url = gr.Textbox(label="Channel URL", placeholder="https://www.youtube.com/@GameplayChannel")
                        with gr.Row():
                            dl_ch2_count = gr.Number(value=20, label="Video Count", precision=0)
                            dl_ch2_sort = gr.Radio(
                                choices=["Most Popular (Highest Views)", "Latest Videos"],
                                value="Most Popular (Highest Views)",
                                label="Sorting",
                            )
                        dl_ch2_btn = gr.Button("📥 Download Channel 2 Videos (720x1280)", variant="primary", elem_classes=["btn-primary"])
                        dl_ch2_logs = gr.Textbox(label="Download Logs", lines=6, interactive=False, elem_classes=["terminal-box"])

            dl_summary_markdown = gr.Markdown(get_folder_summary(DEFAULT_CH1_DIR, DEFAULT_CH2_DIR, DEFAULT_OUT_DIR))

            dl_ch1_btn.click(
                fn=run_download_ch1,
                inputs=[dl_ch1_url, dl_ch1_count, dl_ch1_sort],
                outputs=[dl_ch1_logs, dl_summary_markdown],
            )
            dl_ch2_btn.click(
                fn=run_download_ch2,
                inputs=[dl_ch2_url, dl_ch2_count, dl_ch2_sort],
                outputs=[dl_ch2_logs, dl_summary_markdown],
            )

        # =========================================================
        # TAB 3: Batch Video Generator
        # =========================================================
        with gr.TabItem("⚙️ Batch Video Generator"):
            with gr.Row():
                with gr.Column(scale=6):
                    with gr.Group(elem_classes=["glass-card"]):
                        gr.Markdown("### Folder Selection & Pairing")
                        batch_dir1 = gr.Textbox(label="First Videos Folder (50 Shorts)", value=DEFAULT_CH1_DIR)
                        batch_dir2 = gr.Textbox(label="Second Videos Folder (20 Clips)", value=DEFAULT_CH2_DIR)
                        batch_out = gr.Textbox(label="Output Folder", value=DEFAULT_OUT_DIR)
                        batch_pairing = gr.Radio(
                            choices=["cycle", "random"],
                            value="cycle",
                            label="Pairing Strategy (cycle: Video 1 through 20 looped; random: random selection)",
                        )

                    with gr.Group(elem_classes=["glass-card"]):
                        gr.Markdown("### Video & Audio Adjustments")
                        with gr.Row():
                            b_speed1 = gr.Slider(1.0, 1.5, value=1.08, step=0.01, label="Video 1 Speed (Left)")
                            b_speed2 = gr.Slider(0.5, 1.0, value=0.92, step=0.01, label="Video 2 Speed (Right)")
                        with gr.Row():
                            b_feather_min = gr.Slider(10, 200, value=45, step=1, label="Feather Min (px)")
                            b_feather_max = gr.Slider(10, 200, value=85, step=1, label="Feather Max (px)")
                            b_seed = gr.Number(value=0, label="Random Seed (0 = auto)")

                        b_audio_mode = gr.Radio(
                            choices=[
                                "Keep original",
                                "Remove BGM (keep voice - AI Demucs)",
                                "Totally mute Video 1 (100%)",
                            ],
                            value="Keep original",
                            label="Video 1 Audio Mode",
                        )

                        with gr.Row():
                            b_enable_ad = gr.Checkbox(value=True, label="Insert Ad")
                            b_ad_time = gr.Number(value=15, label="Insert Second")
                            b_ad_random = gr.Checkbox(value=False, label="Randomize Point")

                        with gr.Row():
                            b_template = gr.Image(label="Template Frame Overlay", type="filepath")
                            b_ad_file = gr.Video(label="Ad Video File")

                    with gr.Row():
                        start_batch_btn = gr.Button("▶️ Start Batch Generation", variant="primary", elem_classes=["btn-primary"], scale=2)
                        pause_batch_btn = gr.Button("⏸️ Pause", scale=1)
                        resume_batch_btn = gr.Button("▶️ Resume", scale=1)
                        stop_batch_btn = gr.Button("⏹️ Stop", elem_classes=["btn-danger"], scale=1)

                with gr.Column(scale=5):
                    with gr.Group(elem_classes=["glass-card"]):
                        gr.Markdown("### 📊 Generation Progress & Live Preview")
                        batch_status_box = gr.Textbox(label="Status", value="Idle", interactive=False)
                        batch_preview_player = gr.Video(label="Latest Generated Video (Title Named)")
                        batch_logs_box = gr.Textbox(
                            label="Queue Logs",
                            lines=10,
                            interactive=False,
                            elem_classes=["terminal-box"],
                        )

            start_batch_btn.click(
                fn=start_standalone_batch,
                inputs=[
                    batch_dir1, batch_dir2, batch_out,
                    b_speed1, b_speed2, b_feather_min, b_feather_max, b_seed,
                    b_template, b_enable_ad, b_ad_file, b_ad_time, b_ad_random,
                    b_audio_mode, batch_pairing,
                ],
                outputs=[batch_preview_player, batch_logs_box, batch_status_box],
            )
            pause_batch_btn.click(fn=pause_batch_queue, inputs=[], outputs=[batch_status_box])
            resume_batch_btn.click(fn=resume_batch_queue, inputs=[], outputs=[batch_status_box])
            stop_batch_btn.click(fn=stop_batch_queue, inputs=[], outputs=[batch_status_box])

        # =========================================================
        # TAB 4: Single Video Merge Studio
        # =========================================================
        with gr.TabItem("🎬 Single Video Studio"):
            gr.Markdown("### Manually merge 2 videos to test adjustments inline")
            with gr.Row():
                s_video1 = gr.Video(label="Video 1 - LEFT (Audio Kept)")
                s_video2 = gr.Video(label="Video 2 - RIGHT (Muted & Looped)")

            with gr.Row():
                s_speed1 = gr.Slider(1.0, 1.5, value=1.08, step=0.01, label="Video 1 Speed")
                s_speed2 = gr.Slider(0.5, 1.0, value=0.92, step=0.01, label="Video 2 Speed")

            with gr.Row():
                s_feather_min = gr.Slider(10, 200, value=45, step=1, label="Feather Min (px)")
                s_feather_max = gr.Slider(10, 200, value=85, step=1, label="Feather Max (px)")
                s_seed = gr.Number(value=0, label="Random Seed")

            with gr.Row():
                s_enable_ad = gr.Checkbox(value=True, label="Insert ad (ads.mp4)")
                s_ad_time = gr.Number(value=15, label="Insert after (seconds)")
                s_ad_random = gr.Checkbox(value=False, label="Random after that time")

            s_audio_mode = gr.Radio(
                choices=[
                    "Keep original",
                    "Remove BGM (keep voice - AI Demucs)",
                    "Totally mute Video 1 (100%)",
                ],
                value="Keep original",
                label="Video 1 Audio Mode",
            )

            s_template = gr.Image(label="Template Frame Overlay (banner.png)", type="filepath")
            s_ad_file = gr.Video(label="Ad File (ads.mp4)")

            s_run_btn = gr.Button("Generate Single Video", variant="primary", elem_classes=["btn-primary"])

            with gr.Row():
                s_status = gr.Textbox(label="Status", interactive=False)
                s_output_video = gr.Video(label="Output Preview")

            s_run_btn.click(
                fn=process_single,
                inputs=[
                    s_video1, s_video2, s_speed1, s_speed2, s_feather_min, s_feather_max,
                    s_seed, s_template, s_enable_ad, s_ad_file, s_ad_time, s_ad_random, s_audio_mode
                ],
                outputs=[s_output_video, s_status],
            )


if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860, inbrowser=False, css=custom_css)
