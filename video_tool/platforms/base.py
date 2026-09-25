"""Interface for a browser-visible video platform."""
from __future__ import annotations

from typing import Callable, Protocol

from ..models import Comment, CommentResult, Discovery, Video


class PlatformAdapter(Protocol):
    platform: str

    def discover(self, profile_url: str, limit: int | None, previous: Discovery | None = None,
                 on_progress: Callable[[Discovery], None] | None = None) -> Discovery: ...
    def read_video(self, url: str) -> Video: ...
    def read_comments(self, video: Video, limit: int | None,
                      on_progress: Callable[[CommentResult], None] | None = None,
                      existing: list[Comment] | None = None) -> CommentResult: ...
    def close(self) -> None: ...
