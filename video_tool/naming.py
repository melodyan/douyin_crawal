"""Stable, human-readable names for generated media and reports."""
from __future__ import annotations

import re

from .models import Video


def safe_filename(value: str, max_length: int = 100) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(value or ""))
    name = re.sub(r"\s+", " ", name).strip(" .")[:max_length].strip(" .")
    if not name:
        return "unknown"
    if re.fullmatch(r"(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", name):
        return "_" + name
    return name


def video_stem(video: Video) -> str:
    """Lead with the title; append the stable ID to prevent same-title collisions."""
    title = safe_filename(video.title or "标题未读取", 80)
    return f"{title}_{safe_filename(video.video_id, 32)}"
