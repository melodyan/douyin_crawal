"""Generate Markdown exclusively from stored normalized records."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .naming import safe_filename, video_stem
from .storage import Store


def escape(text: str) -> str:
    text = str(text or "").replace("\r", " ").replace("\n", " ")
    text = text.replace("&", "&amp;").replace("<", "&lt;")
    text = text.replace("\\", "\\\\")
    text = re.sub(r"([`*_{}\[\]()#+\-.!|>])", r"\\\1", text)
    return text


def _quote(text: str) -> str:
    return "\n".join("> " + escape(line) for line in (text or "").splitlines()) or "> （空）"


def comment_char_count(text: str) -> int:
    return sum(not character.isspace() for character in text or "")


def generate(store: Store, config: dict) -> list[Path]:
    output = Path(config["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    videos = store.all_videos()
    for video in videos:
        path = output / f"{video_stem(video)}.md"
        discoveries = store.discoveries(video.platform, video.author_id) if video.author_id else []
        author_name = video.author_name or next(
            (row["author_name"] for row in discoveries if row["author_name"]), "未知博主")
        lines = [f"# {escape(video.title or '标题未读取')} · {escape(video.video_id)}", "",
                 f"博主：{escape(author_name)}；平台：{escape(video.platform)}", "",
                 f"生成时间：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
                 ""]
        if discoveries:
            lines.append("## 主页采集覆盖情况")
            lines.append("")
            for discovery in discoveries:
                status = "确认完整" if discovery["complete"] else "未确认完整"
                lines.append(f"- {escape(discovery['profile_url'])}：{status}，发现 {discovery['video_count']} 条；{escape(discovery['stop_reason'])}")
        else:
            lines.append("主页覆盖情况：未执行主页发现，或无法关联到已采集博主。")
        lines.append("")
        media_status = "images_saved" if video.content_type == "image" else "complete"
        if not config["write_partial_results"] and (not video.title or not video.url or video.transcript_status != media_status):
            continue
        lines.extend(["## 作品信息", "",
                          f"- 作品类型：{'图文' if video.content_type == 'image' else '视频'}",
                          f"- 作品链接：{video.url}",
                          f"- 标题状态：{'已读取' if video.title else '无法读取'}",
                          f"- 元数据状态：{escape(video.metadata_status)}{('；' + escape(video.metadata_error)) if video.metadata_error else ''}"])
        if video.tags_status == "present":
            lines.append("- 原有话题：" + " ".join("\\#" + escape(tag) for tag in video.hashtags))
        elif video.tags_status == "absent":
            lines.append("- 原有话题：页面未显示话题")
        else:
            lines.append("- 原有话题：无法读取")
        if video.content_type == "image":
            lines.extend([f"- 图片状态：{escape(video.transcript_status)}{('；' + escape(video.transcript_error)) if video.transcript_error else ''}",
                              f"- 图片数量：{len(video.image_urls)}", ""])
        else:
            lines.extend([f"- 转写状态：{escape(video.transcript_status)}{('；' + escape(video.transcript_error)) if video.transcript_error else ''}",
                              "", "### 转写全文", "", _quote(video.transcript) if video.transcript else
                              ("> 尚未运行转写" if video.transcript_status == "pending" else "> 转写不可得"), ""])
        comments = store.comments(video.platform, video.video_id)
        min_chars = config.get("min_comment_chars", 50)
        roots_all = [comment for comment in comments if not comment.parent_id]
        replies_all = [comment for comment in comments if comment.parent_id]
        roots = [comment for comment in roots_all if comment_char_count(comment.text) >= min_chars]
        kept_replies = [comment for comment in replies_all if comment_char_count(comment.text) >= min_chars]
        replies: dict[str, list] = {}
        for comment in kept_replies:
            replies.setdefault(comment.parent_id, []).append(comment)
        selected = roots[:config["top_comments"]] if config["top_comments"] is not None else roots
        eligible_root_ids = {comment.comment_id for comment in roots}
        orphan_replies = [comment for comment in kept_replies if comment.parent_id not in eligible_root_ids]
        shown = sum(len(replies.get(comment.comment_id, [])) for comment in selected) + len(orphan_replies)
        lines.extend(["### 评论与回复", "",
                      f"采集数量：{len(roots_all)} 条一级评论、{len(replies_all)} 条回复；"
                      f"字数门槛：至少 {min_chars} 字（不计空白）；"
                      f"过滤：{len(roots_all) - len(roots)} 条一级评论、"
                      f"{len(replies_all) - len(kept_replies)} 条回复；"
                      f"报告列出：{len(selected)} 条一级评论、{shown} 条回复；"
                      f"覆盖：{'确认完整' if video.comments_complete else '未确认完整'}；"
                      f"停止原因：{escape(video.comments_stop_reason)}", ""])
        if not selected and not orphan_replies:
            lines.extend(["（没有采集到可用评论）", ""])
        for index, comment in enumerate(selected, 1):
            by = f" · {escape(comment.author)}" if config["include_comment_author"] and comment.author else ""
            lines.extend([f"#### {index}. 一级评论 · 赞 {comment.likes}{by}", "", _quote(comment.text), ""])
            for reply in replies.pop(comment.comment_id, []):
                by = f" · {escape(reply.author)}" if config["include_comment_author"] and reply.author else ""
                lines.extend([f"- 回复 · 赞 {reply.likes}{by}", "", _quote(reply.text), ""])
        if orphan_replies:
            lines.extend(["#### 未能关联一级评论的回复", ""])
            for reply in orphan_replies:
                lines.extend([f"- 回复 {escape(reply.parent_id)} · 赞 {reply.likes}", "",
                              _quote(reply.text), ""])
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        paths.append(path)
        for outdated in output.glob(f"*_{safe_filename(video.video_id, 32)}.md"):
            if outdated == path:
                continue
            first_line = outdated.read_text(encoding="utf-8").splitlines()[:1]
            if first_line and first_line[0].endswith(f" · {video.video_id}"):
                outdated.unlink()
    represented = {(video.platform, video.author_id) for video in videos if video.author_id}
    for discovery in store.all_discoveries():
        key = (discovery["platform"], discovery["author_id"])
        if key in represented:
            continue
        path = output / f"{safe_filename(discovery['platform'])}_profile_{safe_filename(discovery['author_id'] or 'unknown')}_discovery.md"
        path.write_text(f"# {escape(discovery['platform'])} · {escape(discovery['author_name'] or '未知博主')}\n\n"
                        f"主页：{escape(discovery['profile_url'])}\n\n"
                        f"发现作品：{discovery['video_count']}；"
                        f"覆盖：{'确认完整' if discovery['complete'] else '未确认完整'}；"
                        f"原因：{escape(discovery['stop_reason'])}\n", encoding="utf-8")
        paths.append(path)
    by_id = {(video.platform, video.video_id): video for video in videos}
    generated_names = {path.name for path in paths}
    for collection in store.all_collections():
        path = output / f"douyin_collection_{safe_filename(collection.collection_id)}.md"
        lines = [f"# 合集 · {escape(collection.name or collection.collection_id)}", "",
                 f"合集链接：{collection.url}", "",
                 f"发现作品：{len(collection.video_urls)}；"
                 f"覆盖：{'确认完整' if collection.complete else '未确认完整'}；"
                 f"原因：{escape(collection.stop_reason)}", "", "## 作品目录", ""]
        for index, url in enumerate(collection.video_urls, 1):
            match = re.search(r"/(?:video|note)/(\d+)", url)
            identifier = match.group(1) if match else ""
            video = by_id.get(("douyin", identifier))
            label = escape(video.title if video and video.title else
                           collection.video_titles.get(identifier) or identifier or url)
            report_name = f"{video_stem(video)}.md" if video else ""
            title = f"[{label}]({quote(report_name, safe='')})" if report_name in generated_names else label
            state = (f"转写 {escape(video.transcript_status)}；"
                     f"评论{'完整' if video.comments_complete else '未确认完整'}"
                     if video else "待采集")
            lines.append(f"{index}. {title} · {state} · [原作品]({url})")
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        paths.append(path)
    return paths
