"""
CLI Runner for the YouTube Shorts Batch Downloader & Video Generation Pipeline.

Usage examples:
    # Full automatic pipeline:
    python run_pipeline.py --ch1 "https://www.youtube.com/@Channel1" --ch2 "https://www.youtube.com/@Channel2"

    # Download only:
    python run_pipeline.py --download-only --ch1 "https://www.youtube.com/@Channel1" --count1 50

    # Generate only from existing folders:
    python run_pipeline.py --gen-only --dir1 "downloads/first_videos" --dir2 "downloads/second_videos"
"""

import argparse
import os
import sys
import time

from downloader import download_channel_batch
from batch_pipeline import pipeline_instance


def main():
    parser = argparse.ArgumentParser(
        description="YouTube Popular Shorts Batch Downloader & Automated 50 Video Generator"
    )
    parser.add_argument("--ch1", type=str, help="Channel 1 URL (for 50 Popular Shorts)")
    parser.add_argument("--ch2", type=str, help="Channel 2 URL (for 20 Background Videos)")
    parser.add_argument("--count1", type=int, default=50, help="Number of videos to download from Channel 1 (default: 50)")
    parser.add_argument("--count2", type=int, default=20, help="Number of videos to download from Channel 2 (default: 20)")
    parser.add_argument("--sort1", type=str, default="popular", choices=["popular", "latest"], help="Sort for Channel 1")
    parser.add_argument("--sort2", type=str, default="popular", choices=["popular", "latest"], help="Sort for Channel 2")

    parser.add_argument("--dir1", type=str, default="downloads/first_videos", help="Folder for first videos")
    parser.add_argument("--dir2", type=str, default="downloads/second_videos", help="Folder for second videos")
    parser.add_argument("--out", type=str, default="output_videos", help="Output folder for generated videos")

    parser.add_argument("--speed1", type=float, default=1.08, help="Speed multiplier for video 1 (left)")
    parser.add_argument("--speed2", type=float, default=0.92, help="Speed multiplier for video 2 (right)")
    parser.add_argument("--feather-min", type=int, default=45, help="Feather min width px")
    parser.add_argument("--feather-max", type=int, default=85, help="Feather max width px")
    parser.add_argument("--template", type=str, default=None, help="Path to banner overlay image (default: banner.png)")
    parser.add_argument("--no-ad", action="store_true", help="Disable ad insertion")
    parser.add_argument("--ad-file", type=str, default=None, help="Ad video file (default: ads.mp4)")
    parser.add_argument("--ad-time", type=float, default=15.0, help="Ad insertion second")
    parser.add_argument("--ad-random", action="store_true", help="Randomize ad insertion point")
    parser.add_argument(
        "--audio-mode",
        type=str,
        default="Keep original",
        choices=["Keep original", "Remove BGM (keep voice - AI Demucs)", "Totally mute Video 1 (100%)"],
        help="Audio mode for video 1",
    )
    parser.add_argument("--pairing", type=str, default="cycle", choices=["cycle", "random"], help="Pairing strategy")

    parser.add_argument("--download-only", action="store_true", help="Only download videos, do not generate")
    parser.add_argument("--gen-only", action="store_true", help="Only generate videos from existing folders")

    args = parser.parse_args()

    print("=" * 60)
    print("  YouTube Popular Shorts Batch Downloader & Video Generator")
    print("=" * 60)

    # 1. Download Channel 1
    if not args.gen_only and args.ch1:
        print(f"\n[Step 1/3] Downloading {args.count1} popular shorts from Channel 1...")
        download_channel_batch(
            channel_url=args.ch1,
            output_dir=args.dir1,
            count=args.count1,
            sort=args.sort1,
            is_shorts=True,
            log_callback=print,
        )

    # 2. Download Channel 2
    if not args.gen_only and args.ch2:
        print(f"\n[Step 2/3] Downloading {args.count2} videos from Channel 2...")
        download_channel_batch(
            channel_url=args.ch2,
            output_dir=args.dir2,
            count=args.count2,
            sort=args.sort2,
            is_shorts=True,
            log_callback=print,
        )

    if args.download_only:
        print("\nDownload-only mode completed successfully!")
        return

    # 3. Batch Generation
    print(f"\n[Step 3/3] Starting Batch Generation into '{args.out}'...")
    success = pipeline_instance.start_batch(
        first_folder=args.dir1,
        second_folder=args.dir2,
        output_folder=args.out,
        speed1=args.speed1,
        speed2=args.speed2,
        feather_min=args.feather_min,
        feather_max=args.feather_max,
        template=args.template,
        enable_ad=not args.no_ad,
        ad_file=args.file if hasattr(args, 'file') else args.ad_file,
        ad_time=args.ad_time,
        ad_random=args.ad_random,
        audio_mode=args.audio_mode,
        pairing_mode=args.pairing,
    )

    if not success:
        print("Failed to start batch generator.")
        sys.exit(1)

    # Monitor progress in CLI
    last_idx = -1
    while pipeline_instance.is_running:
        state = pipeline_instance.get_state()
        curr_idx = state["current_index"]
        if curr_idx != last_idx and curr_idx > 0:
            pct = state["progress_percent"]
            title = state["current_title"]
            print(f"[{curr_idx}/{state['total_count']}] ({pct}%) Processing: {title}")
            last_idx = curr_idx
        time.sleep(1.0)

    final_state = pipeline_instance.get_state()
    print(f"\n{final_state['current_status']}")
    print(f"Total videos generated: {final_state['completed_count']}")
    print(f"Output directory: {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
