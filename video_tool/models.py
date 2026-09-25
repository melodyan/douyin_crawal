"""Normalized records shared between collection, storage, and reports."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Comment:
    comment_id: str
    text: str
    likes: int = 0
    author: str = ""
    parent_id: str = ""
    reply_to_id: str = ""


@dataclass
class Video:
    platform: str
    video_id: str
    url: str
    title: str = ""
    author_id: str = ""
    author_name: str = ""
    hashtags: list[str] = field(default_factory=list)
    tags_status: str = "unread"  # present, absent, unread
    media_urls: list[str] = field(default_factory=list)
    metadata_status: str = "pending"
    metadata_error: str = ""
    comments_complete: bool = False
    comments_stop_reason: str = "未采集"
    comments_version: int = 2
    transcript_status: str = "pending"
    transcript: str = ""
    transcript_error: str = ""
    content_type: str = "video"  # video or image
    image_urls: list[list[str]] = field(default_factory=list)


@dataclass
class Discovery:
    video_urls: list[str]
    complete: bool
    stop_reason: str
    author_id: str = ""
    author_name: str = ""


@dataclass
class CommentResult:
    comments: list[Comment]
    complete: bool
    stop_reason: str


@dataclass
class SegmentResult:
    index: int
    start_seconds: float
    end_seconds: float
    status: str
    text: str = ""
    error: str = ""
