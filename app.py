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


def process(video1, video2, speed1, speed2, feather_min, feather_max, seed, template):
    try:
        if not video1 or not video2:
            return None, "Please upload both videos first."

        if feather_min > feather_max:
            feather_min, feather_max = feather_max, feather_min

        seed_val = int(seed) if seed not in (None, "", 0) else None
        tpl = template or (DEFAULT_TEMPLATE if os.path.isfile(DEFAULT_TEMPLATE) else None)

        out_path, feather_used = merge_videos(
            video1, video2,
            speed1=speed1,
            speed2=speed2,
            feather_min=int(feather_min),
            feather_max=int(feather_max),
            seed=seed_val,
            template=tpl,
        )
        status = f"Done. Feather width used: {feather_used}px"
        if tpl:
            status += f" | Frame template: {os.path.basename(tpl)}"
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

    run_btn = gr.Button("Generate", variant="primary")
    template = gr.Image(
        label="Frame template (optional - uses banner.png from this folder by default)",
        type="filepath",
    )
    status = gr.Textbox(label="Status", interactive=False)
    output_video = gr.Video(label="Preview / Download")

    run_btn.click(
        fn=process,
        inputs=[video1, video2, speed1, speed2, feather_min, feather_max, seed, template],
        outputs=[output_video, status],
    )

if __name__ == "__main__":
    demo.launch()
