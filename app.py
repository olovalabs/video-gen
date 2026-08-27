"""
Split-Screen Feather Merge Tool - local Gradio app.

Run with:
    python app.py

Then open the local URL it prints (usually http://127.0.0.1:7860).
Upload two videos, tweak speeds/feather if you want, click Generate,
preview the result inline, and hit the download button on the video
player to save it.
"""

import os
import traceback
import gradio as gr
from video_utils import merge_videos

DEFAULT_TEMPLATE = "banner.png"
DEFAULT_AD = "ads.mp4"


def _unwrap_file(x):
    """Gradio 6 returns {'path': '...'} dict for Video/Image; older returns str. Normalize to path string."""
    if x is None:
        return None
    if isinstance(x, dict):
        return x.get("path") or x.get("name")
    if isinstance(x, (list, tuple)) and x:
        # sometimes wrapped
        return _unwrap_file(x[0])
    return x


def process(video1, video2, speed1, speed2, feather_min, feather_max, seed, template,
            enable_ad=True, ad_file=None, ad_time=15, ad_random=False, audio_mode="Keep original"):
    # Backward-compat: handle stale browser that still sends only 8 inputs
    # Gradio validates input count before calling, so this also needs a hard refresh,
    # but keep defaults so API calls with fewer args don't crash.
    # audio_mode: "Keep original" | "Remove BGM (keep voice - AI Demucs)" | "Totally mute Video 1 (100%)"
    # also accept legacy remove_bgm bool
    if isinstance(audio_mode, bool):
        # legacy checkbox
        audio_mode = "Remove BGM (keep voice - AI Demucs)" if audio_mode else "Keep original"
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

        # Resolve ad path
        ad_path = None
        if enable_ad:
            # uploaded ad takes priority, else default ads.mp4
            if ad_file:
                ad_path = ad_file
            elif os.path.isfile(DEFAULT_AD):
                ad_path = DEFAULT_AD
            if ad_path and not os.path.isfile(ad_path):
                ad_path = None

        # map audio_mode to flags
        if isinstance(audio_mode, str) and "Totally mute" in audio_mode:
            remove_bgm_flag = False
            mute_flag = True
            audio_status = " | Video1 TOTALLY MUTED (100% no music)"
        elif isinstance(audio_mode, str) and "Remove BGM" in audio_mode:
            remove_bgm_flag = True
            mute_flag = False
            audio_status = " | Video1 BGM removed - vocals kept (Demucs AI htdemucs_ft)"
        else:
            remove_bgm_flag = False
            mute_flag = False
            audio_status = ""

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
        status = f"Done. Feather width used: {feather_used}px"
        if tpl:
            status += f" | Frame template: {os.path.basename(tpl)}"
        if ad_path:
            mode = "random >= " if ad_random else "at "
            status += f" | Ad inserted {mode}{float(ad_time):.1f}s ({os.path.basename(ad_path)})"
        status += audio_status
        return out_path, status
    except Exception as e:
        return None, f"Error: {e}\n\n{traceback.format_exc()}"


with gr.Blocks(title="Split-Screen Feather Merge Tool") as demo:
    gr.Markdown("## Split-Screen Feather Merge Tool\n"
                "Video 1 -> left half (full video visible, its audio is kept). "
                "Video 2 -> right half (full video visible, muted, loops to match left length). "
                "The two overlap slightly at the center with a soft feathered seam.")

    with gr.Row():
        video1 = gr.Video(label="Video 1 - LEFT (faster)")
        video2 = gr.Video(label="Video 2 - RIGHT (slower)")

    with gr.Row():
        speed1 = gr.Slider(1.0, 1.5, value=1.08, step=0.01, label="Video 1 speed (left)")
        speed2 = gr.Slider(0.5, 1.0, value=0.92, step=0.01, label="Video 2 speed (right)")

    with gr.Row():
        feather_min = gr.Slider(10, 200, value=45, step=1, label="Feather min (px)")
        feather_max = gr.Slider(10, 200, value=85, step=1, label="Feather max (px)")
        seed = gr.Number(value=0, label="Random seed (0 = random each time)")

    with gr.Group():
        gr.Markdown("### Ad Insertion (cut & insert ads.mp4)")
        with gr.Row():
            enable_ad = gr.Checkbox(value=True, label="Insert ad (ads.mp4)")
            ad_time = gr.Number(value=15, label="Insert after (seconds)", minimum=1, maximum=300)
            ad_random = gr.Checkbox(value=False, label="Random after that time")
        ad_file = gr.Video(label="Ad video (optional override - default is ads.mp4 in folder)")

    with gr.Group():
        gr.Markdown("### Audio - Video 1")
        audio_mode = gr.Radio(
            choices=[
                "Keep original",
                "Remove BGM (keep voice - AI Demucs)",
                "Totally mute Video 1 (100%)",
            ],
            value="Keep original",
            label="Video 1 audio handling"
        )
        gr.Markdown("*`Remove BGM` uses **Demucs htdemucs_ft** (best quality, ~95% removal, keeps voice). `Totally mute` sets **volume=0** -> **100% guaranteed no music** (also mutes voice). Demucs already installed.*",
                    elem_classes=["markdown-sm"])

    run_btn = gr.Button("Generate", variant="primary")
    template = gr.Image(
        label="Frame template (optional - uses banner.png from this folder by default)",
        type="filepath",
    )
    status = gr.Textbox(label="Status", interactive=False)
    output_video = gr.Video(label="Preview / Download")

    run_btn.click(
        fn=process,
        inputs=[video1, video2, speed1, speed2, feather_min, feather_max, seed, template,
                enable_ad, ad_file, ad_time, ad_random, audio_mode],
        outputs=[output_video, status],
    )

if __name__ == "__main__":
    demo.launch()
