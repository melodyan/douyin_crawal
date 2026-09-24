"""Read only content exposed to the current visible Douyin browser session."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import sync_playwright

from ..models import Comment, CommentResult, Discovery, Video

LOG = logging.getLogger(__name__)
VIDEO_RE = re.compile(r"/(?:video|note)/(\d+)")
HASHTAG_RE = re.compile(r"(?<!\w)#([^\s#]+)")


def video_id(url: str) -> str:
    match = VIDEO_RE.search(urlparse(url).path)
    if match:
        return match.group(1)
    query = parse_qs(urlparse(url).query)
    return (query.get("modal_id") or query.get("aweme_id") or [""])[0]


def is_profile(url: str) -> bool:
    return "/user/" in urlparse(url).path


def likes_count(value) -> int:
    if isinstance(value, (int, float)):
        return max(0, int(value))
    value = str(value or "0").strip().replace(",", "").replace("赞", "")
    match = re.match(r"([\d.]+)\s*([万亿wW]?)", value)
    if not match:
        return 0
    return int(float(match.group(1)) * {"万": 10000, "w": 10000, "W": 10000, "亿": 100000000}.get(match.group(2), 1))


def _nested(value, *path):
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _walk_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_objects(child)


def _aweme_objects(payload):
    seen = set()
    for obj in _walk_objects(payload):
        if "aweme_id" in obj and ("video" in obj or "images" in obj or "desc" in obj):
            key = str(obj["aweme_id"])
            if key not in seen:
                seen.add(key)
                yield obj


def _comment_objects(payload):
    for obj in _walk_objects(payload):
        if "cid" in obj and "text" in obj:
            # A reply carries reply_id/reply_to_reply_id; count only top-level.
            if obj.get("reply_id") not in (None, "", "0", 0) or obj.get("reply_to_reply_id") not in (None, "", "0", 0):
                continue
            yield obj


def _image_sources(item: dict) -> list[list[str]]:
    """Keep each photo's alternative URLs together and in album order."""
    images = item.get("images") or []
    result = []
    for image in images:
        if not isinstance(image, dict):
            continue
        urls = image.get("url_list") or _nested(image, "url", "url_list") or []
        if isinstance(urls, str):
            urls = [urls]
        sources = list(dict.fromkeys(url for url in urls if isinstance(url, str) and url.startswith("https://")))
        if sources:
            result.append(sources)
    return result


def _content_url(item: dict) -> str:
    kind = "note" if str(item.get("aweme_type")) == "68" or item.get("images") else "video"
    return f"https://www.douyin.com/{kind}/{item['aweme_id']}"


class DouyinAdapter:
    platform = "douyin"

    def __init__(self, browser: dict, crawl: dict):
        self.browser_config = browser
        self.crawl = crawl
        self.playwright = sync_playwright().start()
        try:
            self.context = self.playwright.chromium.launch_persistent_context(
                str(browser["profile_dir"]), headless=browser["headless"], channel="chrome",
                accept_downloads=False)
        except Exception:
            self.context = self.playwright.chromium.launch_persistent_context(
                str(browser["profile_dir"]), headless=browser["headless"], accept_downloads=False)
        self.context.set_default_timeout(browser["navigation_timeout_seconds"] * 1000)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.responses: list[tuple[str, dict]] = []
        self.page.on("response", self._capture)

    def close(self):
        self.context.close()
        self.playwright.stop()

    def _capture(self, response):
        url = response.url.lower()
        if not any(token in url for token in ("aweme", "comment/list", "post/", "user/profile/other")):
            return
        if response.status != 200 or "json" not in response.headers.get("content-type", ""):
            return
        try:
            payload = response.json()
            if isinstance(payload, dict):
                self.responses.append((url, payload))
        except Exception as exc:
            LOG.debug("无法解析页面响应 %s: %s", url, exc)

    def _navigate(self, url: str):
        self.responses.clear()
        last_error = None
        for attempt in range(self.crawl["max_retries"] + 1):
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=self.browser_config["navigation_timeout_seconds"] * 1000)
                self.page.wait_for_timeout(1500)
                self._manual_gate()
                identifier = video_id(self.page.url)
                if identifier:
                    deadline = time.monotonic() + min(8, self.browser_config["navigation_timeout_seconds"])
                    while time.monotonic() < deadline:
                        if any(str(item.get("aweme_id")) == identifier
                               for payload in [data for _, data in self.responses] + self._hydration()
                               for item in _aweme_objects(payload)):
                            break
                        try:
                            ready = self.page.locator("video").evaluate_all(
                                "(els, id) => els.some(e => (e.currentSrc || '').includes(id))", identifier)
                            if ready:
                                break
                        except Exception:
                            pass
                        self.page.wait_for_timeout(400)
                host = (urlparse(self.page.url).hostname or "").lower()
                if not (host == "douyin.com" or host.endswith(".douyin.com") or host == "iesdouyin.com" or host.endswith(".iesdouyin.com")):
                    raise ValueError(f"短链接跳转到非抖音域名: {self.page.url}")
                return self.page.url
            except Exception as exc:
                last_error = exc
                if attempt < self.crawl["max_retries"]:
                    time.sleep(self.crawl["retry_backoff_seconds"] * (attempt + 1))
        raise RuntimeError(f"打开页面失败: {last_error}")

    def resolve_kind(self, url: str) -> tuple[str, bool]:
        final = self._navigate(url)
        return final, is_profile(final)

    def _manual_gate(self):
        try:
            text = self.page.locator("body").inner_text(timeout=3000)[:3000]
        except Exception:
            return
        if not any(word in text for word in ("扫码登录", "安全验证", "请完成验证", "验证码", "登录后查看")):
            return
        if is_profile(self.page.url) and "服务异常，重新刷新拉取数据" in text:
            return
        identifier = video_id(self.page.url)
        if identifier and not any(word in text for word in ("安全验证", "请完成验证")):
            try:
                playable = self.page.locator("video").evaluate_all(
                    "(els, id) => els.some(e => (e.currentSrc || '').includes(id))", identifier)
                if playable:
                    return
            except Exception:
                pass
        LOG.warning("浏览器需要手动登录或验证: %s", self.page.url)
        deadline = None if self.browser_config["manual_wait_seconds"] == 0 else time.monotonic() + self.browser_config["manual_wait_seconds"]
        while deadline is None or time.monotonic() < deadline:
            self.page.wait_for_timeout(2000)
            try:
                current = self.page.locator("body").inner_text(timeout=3000)[:3000]
            except Exception:
                continue
            if not any(word in current for word in ("扫码登录", "安全验证", "请完成验证", "验证码", "登录后查看")):
                return
        raise RuntimeError("等待手动登录或验证超时")

    def _hydration(self):
        data = []
        for selector in ("script#RENDER_DATA", "script#__NEXT_DATA__"):
            try:
                for raw in self.page.locator(selector).all_text_contents():
                    data.append(json.loads(unquote(raw)))
            except (ValueError, TypeError):
                continue
        return data

    def _visible_image_sources(self) -> list[list[str]]:
        """Read the rendered album when the page does not expose aweme images."""
        slides = self.page.locator(".dySwiperSlide").evaluate_all(
            "els => els.map(slide => [...slide.querySelectorAll('img')].flatMap(img => [img.currentSrc, img.src]))")
        if not slides or any(not sources for sources in slides):
            return []
        images = []
        for sources in slides:
            urls = list(dict.fromkeys(url for url in sources if isinstance(url, str) and url.startswith("https://")))
            if not urls:
                return []
            images.append(urls)
        return images

    def _visible_video_urls(self, profile: bool = False):
        urls = []
        selector = ('[data-e2e="user-post-list"] a[href*="/video/"], '
                    '[data-e2e="user-post-list"] a[href*="/note/"], '
                    '[data-e2e="user-post-item"] a[href*="/video/"], '
                    '[data-e2e="user-post-item"] a[href*="/note/"], '
                    '[data-e2e="user-video-list"] a[href*="/video/"], '
                    '[data-e2e="user-video-list"] a[href*="/note/"]') if profile else 'a[href*="/video/"], a[href*="/note/"]'
        for href in self.page.locator(selector).evaluate_all("els => els.map(e => e.href)"):
            identifier = video_id(href)
            if identifier:
                kind = "note" if "/note/" in urlparse(href).path else "video"
                urls.append(f"https://www.douyin.com/{kind}/{identifier}")
        for response_url, payload in self.responses:
            if profile and not ("/aweme/post" in response_url or "/post/" in response_url):
                continue
            for item in _aweme_objects(payload):
                urls.append(_content_url(item))
        if not profile:
            for payload in self._hydration():
                for item in _aweme_objects(payload):
                    urls.append(_content_url(item))
        unique = {}
        for url in urls:
            identifier = video_id(url)
            if identifier and (identifier not in unique or "/note/" in urlparse(url).path):
                unique[identifier] = url
        return list(unique.values())

    def discover(self, profile_url: str, limit: int | None, previous: Discovery | None = None,
                 on_progress: Callable[[Discovery], None] | None = None) -> Discovery:
        final = self._navigate(profile_url)
        if not is_profile(final):
            raise ValueError(f"不是抖音博主主页: {profile_url}")
        author_id = final.rstrip("/").split("/")[-1]
        try:
            author_name = self.page.locator("h1").first.inner_text(timeout=2500).strip()
        except Exception:
            author_name = ""
        if not author_name:
            for response_url, payload in self.responses:
                if "user/profile/other" in response_url:
                    author_name = str(_nested(payload, "user", "nickname") or "")
                    if author_name:
                        break
        if not author_name and previous:
            author_name = previous.author_name
        body = self.page.locator("body").inner_text(timeout=3000)
        prior_urls = previous.video_urls if previous else []
        if "服务异常，重新刷新拉取数据" in body:
            return Discovery(prior_urls, False, "主页作品列表显示服务异常；当前会话无法读取", author_id,
                             author_name or (previous.author_name if previous else ""))
        observed = self._visible_video_urls(profile=True)
        urls = list(dict.fromkeys(prior_urls + observed))
        if on_progress:
            on_progress(Discovery(urls, False, "采集中", author_id, author_name))
        no_new = 0
        stop = "连续滚动无新视频，未确认到底"
        complete = False
        while True:
            if limit is not None and len(urls) >= limit:
                urls = urls[:limit]
                stop = f"达到主页上限 {limit}"
                break
            # Only a page response with has_more=false can confirm the full list.
            page_end = any(("/aweme/post" in url or "/post/" in url) and data.get("has_more") in (0, False)
                           and "aweme_list" in data for url, data in self.responses)
            if page_end:
                complete, stop = True, "页面响应确认列表到底"
                break
            self.page.mouse.wheel(0, 2400)
            self.page.wait_for_timeout(max(700, int(self.crawl["delay_seconds"] * 1000)))
            current = self._visible_video_urls(profile=True)
            latest = list(dict.fromkeys(urls + current))
            no_new = no_new + 1 if len(current) == len(observed) else 0
            observed = current
            urls = latest
            if on_progress:
                on_progress(Discovery(urls, False, "采集中", author_id, author_name))
            if no_new >= self.crawl["no_new_content_scrolls"]:
                break
        return Discovery(urls, complete, stop, author_id, author_name)

    def read_video(self, url: str) -> Video:
        final = self._navigate(url)
        identifier = video_id(final) or video_id(url)
        candidates = []
        for _, payload in self.responses:
            candidates.extend(_aweme_objects(payload))
        for payload in self._hydration():
            candidates.extend(_aweme_objects(payload))
        matches = [item for item in candidates if str(item.get("aweme_id")) == identifier]
        match = max(matches, key=lambda item: (len(_image_sources(item)),
                                                bool(_nested(item, "video", "play_addr", "url_list")),
                                                bool(item.get("desc"))), default=None)
        if not identifier:
            raise ValueError(f"无法从页面解析视频 ID: {url}")
        kind = "note" if "/note/" in urlparse(final).path else "video"
        if match and (str(match.get("aweme_type")) == "68" or match.get("images")):
            kind = "note"
        video = Video("douyin", identifier, f"https://www.douyin.com/{kind}/{identifier}")
        video.content_type = "image" if kind == "note" else "video"
        if match:
            video.title = str(match.get("desc") or "").strip()
            author = match.get("author") or {}
            video.author_id = str(author.get("sec_uid") or author.get("uid") or "")
            video.author_name = str(author.get("nickname") or "")
            video.hashtags = list(dict.fromkeys(HASHTAG_RE.findall(video.title)))
            video.tags_status = "present" if video.hashtags else "absent"
            if video.content_type == "image":
                video.image_urls = _image_sources(match)
            else:
                media = match.get("video") or {}
                for field in ("play_addr", "play_addr_h264", "play_addr_265"):
                    video.media_urls.extend(_nested(media, field, "url_list") or [])
        if not video.title:
            for selector in ("h1", "[data-e2e='video-desc']", "meta[property='og:description']"):
                try:
                    locator = self.page.locator(selector).first
                    text = locator.get_attribute("content") if selector.startswith("meta") else locator.inner_text(timeout=1500)
                    if text and text.strip():
                        video.title = text.strip()
                        break
                except Exception:
                    pass
        if not video.title:
            try:
                title = self.page.title().strip()
                if title.endswith(" - 抖音"):
                    video.title = title[:-5].strip()
            except Exception:
                pass
        if not match:
            if video.title:
                video.hashtags = list(dict.fromkeys(HASHTAG_RE.findall(video.title)))
                video.tags_status = "present" if video.hashtags else "absent"
            try:
                tags = self.page.locator('a[href*="/search/%23"], a[href*="hashtag"]')
                visible = [text.lstrip("#").strip() for text in tags.all_inner_texts() if text.strip()]
                video.hashtags = list(dict.fromkeys(video.hashtags + visible))
                if visible:
                    video.tags_status = "present"
            except Exception:
                pass
        if not video.author_id:
            try:
                for href, name in self.page.locator('a[href*="/user/"]').evaluate_all(
                        "els => els.map(e => [e.href, e.textContent?.trim() || ''])"):
                    if "/user/self" not in href and name:
                        video.author_id = urlparse(href).path.rsplit("/", 1)[-1]
                        video.author_name = name
                        break
            except Exception:
                pass
        if video.content_type == "video" and not video.media_urls:
            try:
                sources = self.page.locator("video").evaluate_all(
                    "els => els.flatMap(e => [e.currentSrc, e.src]).filter(Boolean)")
                video.media_urls.extend(source for source in sources if identifier in source)
            except Exception:
                pass
        if video.content_type == "image" and not video.image_urls:
            try:
                video.image_urls = self._visible_image_sources()
            except Exception:
                pass
        video.media_urls = list(dict.fromkeys(url for url in video.media_urls if isinstance(url, str) and url.startswith("https://")))
        has_media = video.image_urls if video.content_type == "image" else video.media_urls
        video.metadata_status = "complete" if video.title and has_media else "partial"
        video.metadata_error = "" if video.metadata_status == "complete" else "标题或媒体来源未能从可访问页面读取"
        return video

    def read_comments(self, video: Video, limit: int | None) -> CommentResult:
        if video_id(self.page.url) != video.video_id:
            self._navigate(video.url)
        # Open the visible comment panel, if necessary. Selectors are intentionally broad;
        # no private endpoint is called directly.
        for selector in ('[data-e2e="comment-icon"]', 'button[aria-label*="评论"]', 'text=评论'):
            try:
                item = self.page.locator(selector).first
                if item.is_visible(timeout=800):
                    item.click(timeout=1500)
                    break
            except Exception:
                continue
        comments: dict[str, Comment] = {}
        no_new = 0
        previous_count = -1
        parse_error = False
        complete = False
        stop = "连续滚动无新评论，未确认到底"
        while True:
            for response_url, payload in self.responses:
                if "comment/list" not in response_url:
                    continue
                response_video_id = (parse_qs(urlparse(response_url).query).get("aweme_id") or [""])[0]
                if response_video_id != video.video_id:
                    continue
                parsed = list(_comment_objects(payload))
                if payload.get("comments") and not parsed:
                    parse_error = True
                for item in parsed:
                    if str(item.get("aweme_id") or video.video_id) != video.video_id:
                        continue
                    cid = str(item.get("cid") or "")
                    if cid:
                        comments[cid] = Comment(cid, str(item.get("text") or ""),
                                                likes_count(item.get("digg_count")),
                                                str(_nested(item, "user", "nickname") or ""))
                if payload.get("status_code") == 0 and payload.get("has_more") in (0, False) and "comments" in payload and not parse_error:
                    complete = True
            if limit is not None and len(comments) >= limit:
                stop = f"达到评论上限 {limit}"
                break
            if complete:
                stop = "评论响应确认到底"
                break
            no_new = no_new + 1 if len(comments) == previous_count else 0
            previous_count = len(comments)
            if no_new >= self.crawl["no_new_content_scrolls"]:
                break
            self.page.mouse.wheel(0, 1400)
            self.page.wait_for_timeout(max(700, int(self.crawl["delay_seconds"] * 1000)))
        if parse_error:
            complete, stop = False, "页面返回评论，但部分评论未能解析"
        result = sorted(comments.values(), key=lambda item: (-item.likes, item.comment_id))
        return CommentResult(result[:limit] if limit else result, complete, stop)
