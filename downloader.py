"""
YouTube Batch Downloader module for Shorts and Videos.
- Supports channel URLs (@handle, /shorts, /videos, channel ID)
- Extracts metadata and sorts by popularity (view_count) or latest
- Downloads exactly MP4 - 720x1280 HD (vertical 9:16) conformed
- Preserves original video titles and saves metadata.json
"""

import json
import os
import re
import sys
import threading
from typing import Callable, Dict, List, Optional
import yt_dlp

from video_utils import sanitize_filename, conform_to_720x1280, WORKDIR


def normalize_channel_url(url: str, is_shorts: bool = True) -> str:
    """Normalize user input to a proper YouTube channel URL."""
    url = url.strip()
    if not url:
        return url

    # Handle bare handle like @MrBeast
    if url.startswith("@"):
        url = f"https://www.youtube.com/{url}"
    elif not url.startswith("http://") and not url.startswith("https://"):
        if url.startswith("youtube.com") or url.startswith("www.youtube.com"):
            url = f"https://{url}"
        else:
            url = f"https://www.youtube.com/@{url.lstrip('/')}"

    # If already points to /shorts or /videos or /featured, respect it
    if is_shorts:
        if not re.search(r'/(shorts|videos|featured|streams|playlists)(\?|$)', url):
            url = url.rstrip("/") + "/shorts"
    else:
        if not re.search(r'/(shorts|videos|featured|streams|playlists)(\?|$)', url):
            url = url.rstrip("/") + "/videos"

    return url


def get_channel_videos_metadata(
    channel_url: str,
    sort: str = "popular",
    limit: int = 50,
    is_shorts: bool = True,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> List[Dict]:
    """
    Extract video metadata from a YouTube channel using flat extraction.
    Sorts by popularity (view_count descending) or keeps latest order.
    Returns list of dicts with: id, title, view_count, url, duration.
    """
    normalized_url = normalize_channel_url(channel_url, is_shorts=is_shorts)
    if progress_callback:
        progress_callback(f"Scanning channel: {normalized_url}...")

    ydl_opts = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "js_runtimes": {"node": {}},
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(normalized_url, download=False)
        except Exception as e:
            # Fallback try base channel URL if /shorts or /videos had an issue
            if progress_callback:
                progress_callback(f"Initial scan failed: {e}. Trying alternate channel path...")
            base_url = re.sub(r'/(shorts|videos)/?$', '', normalized_url)
            info = ydl.extract_info(base_url, download=False)

    entries = [e for e in info.get("entries", []) if e]
    if progress_callback:
        progress_callback(f"Found {len(entries)} total entries. Sorting by {sort}...")

    # Filter/clean entries
    cleaned_entries = []
    for e in entries:
        vid_id = e.get("id")
        if not vid_id:
            continue
        title = e.get("title") or f"video_{vid_id}"
        view_count = e.get("view_count") or 0
        duration = e.get("duration")
        url = e.get("url") or f"https://www.youtube.com/watch?v={vid_id}"
        if is_shorts and not e.get("url"):
            url = f"https://www.youtube.com/shorts/{vid_id}"

        cleaned_entries.append({
            "id": vid_id,
            "title": title,
            "view_count": view_count,
            "duration": duration,
            "url": url,
        })

    if sort == "popular":
        cleaned_entries.sort(key=lambda x: x.get("view_count") or 0, reverse=True)

    selected = cleaned_entries[:limit]
    if progress_callback:
        progress_callback(f"Selected top {len(selected)} videos ready for download.")

    return selected


def download_video_720x1280(
    video_info: Dict,
    output_dir: str,
    index: int = 1,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """
    Download a single video and ensure it is formatted as MP4 - 720x1280 HD.
    Saves file with sanitized video title in output_dir.
    Returns path to downloaded file.
    """
    os.makedirs(output_dir, exist_ok=True)
    title = video_info.get("title", f"video_{index}")
    vid_id = video_info.get("id", str(index))
    safe_title = sanitize_filename(title)
    if not safe_title:
        safe_title = f"video_{index}"

    final_filename = f"{safe_title}.mp4"
    final_path = os.path.join(output_dir, final_filename)

    # Avoid duplicate name collision
    counter = 1
    while os.path.isfile(final_path) and os.path.getsize(final_path) > 1000:
        base, ext = os.path.splitext(final_filename)
        if counter == 1:
            # File exists and valid - return existing path
            if progress_callback:
                progress_callback(f"[{index}] Already downloaded: {final_filename}")
            return final_path
        final_filename = f"{safe_title}_{counter}.mp4"
        final_path = os.path.join(output_dir, final_filename)
        counter += 1

    temp_template = os.path.join(WORKDIR, f"dl_temp_{vid_id}.%(ext)s")

    ydl_opts = {
        "format": "bv*[height<=1280][ext=mp4]+ba[ext=m4a]/bv*[height<=1280]+ba/best[height<=1280]/best",
        "outtmpl": temp_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {}},
    }

    url = video_info.get("url") or f"https://www.youtube.com/watch?v={vid_id}"

    if progress_callback:
        views = video_info.get('view_count', 0)
        views_str = f" ({views:,} views)" if views else ""
        progress_callback(f"[{index}] Downloading: {title[:60]}{views_str}...")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # Find the downloaded file
    downloaded_cand = None
    for ext in ("mp4", "mkv", "webm", "m4v"):
        cand = os.path.join(WORKDIR, f"dl_temp_{vid_id}.{ext}")
        if os.path.isfile(cand):
            downloaded_cand = cand
            break

    if not downloaded_cand:
        raise RuntimeError(f"Download completed but file not found for: {title}")

    # Conform to exact 720x1280 HD MP4
    if progress_callback:
        progress_callback(f"[{index}] Conforming to MP4 (720x1280 HD)...")

    conform_to_720x1280(downloaded_cand, final_path)

    # Clean up temp file
    try:
        if os.path.isfile(downloaded_cand):
            os.remove(downloaded_cand)
    except OSError:
        pass

    return final_path


def download_channel_batch(
    channel_url: str,
    output_dir: str,
    count: int = 50,
    sort: str = "popular",
    is_shorts: bool = True,
    progress_callback: Optional[Callable[[int, int, str, str], None]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> List[Dict]:
    """
    Download up to `count` videos from channel_url into output_dir.
    Saves metadata.json in output_dir.
    Calls progress_callback(current, total, current_title, status_text).
    """
    os.makedirs(output_dir, exist_ok=True)

    def log(msg: str):
        if log_callback:
            log_callback(msg)
        print(f"[Downloader] {msg}")

    log(f"Starting batch download from {channel_url} (target: {count} videos, sort: {sort})...")
    video_list = get_channel_videos_metadata(
        channel_url=channel_url,
        sort=sort,
        limit=count,
        is_shorts=is_shorts,
        progress_callback=log,
    )

    if not video_list:
        log("No videos found on channel!")
        return []

    total = len(video_list)
    log(f"Beginning download of {total} videos to '{output_dir}'...")

    metadata_path = os.path.join(output_dir, "metadata.json")
    saved_metadata = []
    if os.path.isfile(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                saved_metadata = json.load(f)
        except Exception:
            saved_metadata = []

    saved_dict = {item.get("id"): item for item in saved_metadata if isinstance(item, dict)}

    downloaded_items = []
    for i, item in enumerate(video_list, 1):
        if stop_event and stop_event.is_set():
            log("Download cancelled by user.")
            break

        title = item.get("title", "")
        if progress_callback:
            progress_callback(i, total, title, f"Downloading ({i}/{total}): {title}")

        try:
            filepath = download_video_720x1280(
                item, output_dir=output_dir, index=i, progress_callback=log
            )
            item_record = {
                "index": i,
                "id": item.get("id"),
                "title": title,
                "view_count": item.get("view_count"),
                "duration": item.get("duration"),
                "url": item.get("url"),
                "filename": os.path.basename(filepath),
                "filepath": os.path.abspath(filepath),
            }
            saved_dict[item.get("id")] = item_record
            downloaded_items.append(item_record)

            # Periodically write metadata.json
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(list(saved_dict.values()), f, indent=2, ensure_ascii=False)

        except Exception as e:
            log(f"Error downloading '{title}': {e}")

    log(f"Batch download finished! {len(downloaded_items)}/{total} downloaded.")
    return downloaded_items
