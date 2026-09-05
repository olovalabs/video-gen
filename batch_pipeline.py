"""
Batch Pipeline Module for Sequential Async Video Generation.
Pairs Video 1 (Shorts) with Video 2 (Backgrounds), processing 1 by 1 sequentially,
naming each output video with its original title and saving into output_dir.
"""

import collections
import glob
import json
import os
import random
import threading
import time
from typing import Callable, Dict, List, Optional

from video_utils import merge_videos, sanitize_filename, WORKDIR


class BatchPipeline:
    def __init__(self):
        self.lock = threading.Lock()
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()  # Not paused by default

        self.is_running = False
        self.current_index = 0
        self.total_count = 0
        self.current_title = ""
        self.current_status = "Idle"
        self.last_rendered_file: Optional[str] = None
        self.logs = collections.deque(maxlen=100)
        self.completed_videos: List[str] = []

    def log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self.logs.append(entry)
        print(f"[BatchPipeline] {entry}")

    def get_state(self) -> Dict:
        with self.lock:
            pct = 0.0
            if self.total_count > 0:
                pct = round((self.current_index / self.total_count) * 100, 1)
            return {
                "is_running": self.is_running,
                "is_paused": not self.pause_event.is_set(),
                "current_index": self.current_index,
                "total_count": self.total_count,
                "current_title": self.current_title,
                "current_status": self.current_status,
                "progress_percent": pct,
                "last_rendered_file": self.last_rendered_file,
                "completed_count": len(self.completed_videos),
                "logs": list(self.logs),
            }

    def stop(self):
        """Signal the worker thread to stop."""
        self.stop_event.set()
        self.pause_event.set()  # Unpause so it can exit
        self.current_status = "Stopping..."
        self.log("Stopping batch generation queue...")

    def pause(self):
        """Pause execution."""
        self.pause_event.clear()
        self.current_status = "Paused"
        self.log("Batch queue paused.")

    def resume(self):
        """Resume execution."""
        self.pause_event.set()
        self.current_status = "Processing..."
        self.log("Batch queue resumed.")

    def scan_folder_videos(self, folder: str) -> List[Dict]:
        """
        Scan a folder for video files.
        Checks for metadata.json for accurate YouTube titles.
        Returns list of dicts: {'path': ..., 'title': ..., 'filename': ...}
        """
        if not os.path.isdir(folder):
            return []

        # Check for metadata.json
        meta_dict = {}
        meta_file = os.path.join(folder, "metadata.json")
        if os.path.isfile(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta_list = json.load(f)
                    for item in meta_list:
                        if isinstance(item, dict):
                            fn = item.get("filename")
                            title = item.get("title")
                            if fn and title:
                                meta_dict[fn.lower()] = title
            except Exception as e:
                self.log(f"Warning reading metadata: {e}")

        # Scan video files
        supported_exts = (".mp4", ".mkv", ".mov", ".webm", ".avi")
        files = [
            f for f in os.listdir(folder)
            if os.path.isfile(os.path.join(folder, f)) and f.lower().endswith(supported_exts)
        ]
        files.sort()

        results = []
        for f in files:
            full_path = os.path.abspath(os.path.join(folder, f))
            title = meta_dict.get(f.lower())
            if not title:
                # Strip extension and clean up
                title = os.path.splitext(f)[0]

            results.append({
                "path": full_path,
                "filename": f,
                "title": title,
            })
        return results

    def start_batch(
        self,
        first_folder: str,
        second_folder: str,
        output_folder: str,
        speed1: float = 1.08,
        speed2: float = 0.92,
        feather_min: int = 45,
        feather_max: int = 85,
        seed: int = 0,
        template: Optional[str] = None,
        enable_ad: bool = True,
        ad_file: Optional[str] = None,
        ad_time: float = 15.0,
        ad_random: bool = False,
        audio_mode: str = "Keep original",
        pairing_mode: str = "cycle",  # "cycle" or "random"
        max_videos: Optional[int] = None,
        on_progress: Optional[Callable[[Dict], None]] = None,
    ):
        """Start batch generation in a background worker thread."""
        with self.lock:
            if self.is_running:
                self.log("Batch is already running!")
                return False

            self.stop_event.clear()
            self.pause_event.set()
            self.is_running = True
            self.current_index = 0
            self.total_count = 0
            self.completed_videos = []
            self.last_rendered_file = None
            self.current_status = "Initializing..."

        self.worker_thread = threading.Thread(
            target=self._run_worker,
            args=(
                first_folder,
                second_folder,
                output_folder,
                speed1,
                speed2,
                feather_min,
                feather_max,
                seed,
                template,
                enable_ad,
                ad_file,
                ad_time,
                ad_random,
                audio_mode,
                pairing_mode,
                max_videos,
                on_progress,
            ),
            daemon=True,
        )
        self.worker_thread.start()
        return True

    def _run_worker(
        self,
        first_folder: str,
        second_folder: str,
        output_folder: str,
        speed1: float,
        speed2: float,
        feather_min: int,
        feather_max: int,
        seed: int,
        template: Optional[str],
        enable_ad: bool,
        ad_file: Optional[str],
        ad_time: float,
        ad_random: bool,
        audio_mode: str,
        pairing_mode: str,
        max_videos: Optional[int],
        on_progress: Optional[Callable[[Dict], None]],
    ):
        try:
            self.log(f"Scanning input folders...")
            v1_list = self.scan_folder_videos(first_folder)
            v2_list = self.scan_folder_videos(second_folder)

            if not v1_list:
                self.log(f"Error: No videos found in first videos folder: {first_folder}")
                self.current_status = "Error: First folder empty"
                self.is_running = False
                return

            if not v2_list:
                self.log(f"Error: No videos found in second videos folder: {second_folder}")
                self.current_status = "Error: Second folder empty"
                self.is_running = False
                return

            if max_videos and max_videos > 0:
                v1_list = v1_list[:max_videos]

            os.makedirs(output_folder, exist_ok=True)
            self.total_count = len(v1_list)
            self.log(f"Batch loaded: {self.total_count} videos to generate using {len(v2_list)} background clips.")

            # Resolve template
            default_template = "banner.png"
            tpl = template or (default_template if os.path.isfile(default_template) else None)

            # Resolve ad
            default_ad = "ads.mp4"
            ad_path = None
            if enable_ad:
                if ad_file and os.path.isfile(ad_file):
                    ad_path = ad_file
                elif os.path.isfile(default_ad):
                    ad_path = default_ad

            # Resolve audio mode
            mute_flag = False
            remove_bgm_flag = False
            if "Totally mute" in str(audio_mode):
                mute_flag = True
            elif "Remove BGM" in str(audio_mode):
                remove_bgm_flag = True

            for idx, item1 in enumerate(v1_list, 1):
                # Check stop / pause
                if self.stop_event.is_set():
                    self.log("Batch processing stopped by user.")
                    self.current_status = "Stopped"
                    break

                self.pause_event.wait()  # Block if paused

                self.current_index = idx
                v1_path = item1["path"]
                title1 = item1["title"]
                self.current_title = title1
                self.current_status = f"Rendering {idx}/{self.total_count}: {title1[:40]}"

                # Pick video 2
                if pairing_mode == "random":
                    item2 = random.choice(v2_list)
                else:
                    item2 = v2_list[(idx - 1) % len(v2_list)]
                v2_path = item2["path"]

                # Generate clean filename based on video title
                safe_name = sanitize_filename(title1)
                out_filename = f"{safe_name}.mp4"
                out_target = os.path.join(output_folder, out_filename)

                # Avoid collision if two shorts have identical title
                dup_counter = 1
                while os.path.isfile(out_target) and os.path.getsize(out_target) > 5000:
                    out_filename = f"{safe_name}_{dup_counter}.mp4"
                    out_target = os.path.join(output_folder, out_filename)
                    dup_counter += 1

                self.log(f"[{idx}/{self.total_count}] Merging:\n  Video 1: {item1['filename']}\n  Video 2: {item2['filename']}\n  Output: {out_filename}")

                if on_progress:
                    on_progress(self.get_state())

                # Execute merge
                start_time = time.time()
                try:
                    out_path, feather_used = merge_videos(
                        video1=v1_path,
                        video2=v2_path,
                        speed1=speed1,
                        speed2=speed2,
                        feather_min=feather_min,
                        feather_max=feather_max,
                        seed=seed if seed else None,
                        out_name=out_target,
                        template=tpl,
                        ad_path=ad_path,
                        ad_insert_sec=ad_time,
                        ad_random=ad_random,
                        remove_bgm=remove_bgm_flag,
                        mute_video1=mute_flag,
                    )
                    elapsed = time.time() - start_time
                    self.last_rendered_file = out_path
                    self.completed_videos.append(out_path)
                    self.log(f"[{idx}/{self.total_count}] Done in {elapsed:.1f}s -> {os.path.basename(out_path)}")

                except Exception as ex:
                    self.log(f"[{idx}/{self.total_count}] Error merging video: {ex}")

                if on_progress:
                    on_progress(self.get_state())

            if not self.stop_event.is_set():
                self.current_status = f"Completed ({len(self.completed_videos)}/{self.total_count} videos generated)"
                self.log(f"All done! Successfully generated {len(self.completed_videos)} videos into '{output_folder}'.")

        except Exception as e:
            self.log(f"Unexpected worker failure: {e}")
            self.current_status = f"Error: {e}"
        finally:
            self.is_running = False
            if on_progress:
                on_progress(self.get_state())


# Global singleton instance
pipeline_instance = BatchPipeline()
