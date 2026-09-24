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


def _image_extension(data: bytes) -> str | None:
    """Use file signatures so a CDN error page cannot be saved as a photo."""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"):
        return ".avif"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"hevx", b"mif1"):
        return ".heic"
    return None


def download_images(image_urls: list[list[str]], destination: Path, timeout_seconds: int) -> Path:
    """Download every album image in order into a directory named for the post."""
    if not image_urls:
        raise RuntimeError("页面未提供可用图片来源")
    destination.mkdir(parents=True, exist_ok=True)
    for index, sources in enumerate(image_urls, 1):
        temporary = destination / f"{index:02d}.part"
        errors = []
        for source in sources:
            result = download([source], temporary, timeout_seconds)
            if not result.path:
                errors.append(result.error)
                continue
            try:
                with temporary.open("rb") as image:
                    extension = _image_extension(image.read(32))
                if not extension:
                    errors.append("返回的内容不是受支持的图片")
                    continue
                temporary.replace(destination / f"{index:02d}{extension}")
                break
            finally:
                temporary.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"第 {index} 张图片下载失败：{'；'.join(errors) if errors else '无可用来源'}")
    return destination
