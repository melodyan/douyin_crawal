"""Download a media source already discovered by a platform adapter."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx


@dataclass
class DownloadResult:
    path: Path | None
    media_type: str = ""
    error: str = ""


def download(media_urls: list[str], destination: Path, timeout_seconds: int) -> DownloadResult:
    destination.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for media_url in media_urls:
        parsed = urlparse(media_url)
        if parsed.scheme != "https" or not parsed.hostname:
            errors.append("无效媒体来源")
            continue
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout_seconds) as client:
                with client.stream("GET", media_url, headers={"User-Agent": "Mozilla/5.0"}) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";")[0].lower()
                    if content_type.startswith("text/") or "json" in content_type:
                        raise ValueError(f"返回了 {content_type}，不是媒体文件")
                    size = 0
                    with destination.open("wb") as output:
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            output.write(chunk)
                    if size == 0:
                        raise ValueError("媒体文件为空")
                    return DownloadResult(destination, content_type)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            destination.unlink(missing_ok=True)
            errors.append(str(exc))
    return DownloadResult(None, error="; ".join(errors) if errors else "页面未提供可用媒体来源")
