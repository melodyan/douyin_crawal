"""SQLite state; each public method commits its completed step."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Collection, Comment, CommentResult, Discovery, SegmentResult, Video


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS videos (
                platform TEXT NOT NULL, video_id TEXT NOT NULL, url TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '', author_id TEXT NOT NULL DEFAULT '',
                author_name TEXT NOT NULL DEFAULT '', hashtags TEXT NOT NULL DEFAULT '[]',
                tags_status TEXT NOT NULL DEFAULT 'unread', metadata_status TEXT NOT NULL DEFAULT 'pending',
                metadata_error TEXT NOT NULL DEFAULT '', comments_complete INTEGER NOT NULL DEFAULT 0,
                comments_stop_reason TEXT NOT NULL DEFAULT '未采集', comments_version INTEGER NOT NULL DEFAULT 2,
                transcript_status TEXT NOT NULL DEFAULT 'pending',
                transcript TEXT NOT NULL DEFAULT '', transcript_error TEXT NOT NULL DEFAULT '',
                content_type TEXT NOT NULL DEFAULT 'video', image_urls TEXT NOT NULL DEFAULT '[]',
                description TEXT NOT NULL DEFAULT '', published_at TEXT NOT NULL DEFAULT '',
                likes INTEGER NOT NULL DEFAULT 0, favorites INTEGER NOT NULL DEFAULT 0,
                shares INTEGER NOT NULL DEFAULT 0, cover_url TEXT NOT NULL DEFAULT '',
                duration_seconds REAL NOT NULL DEFAULT 0, local_media_path TEXT NOT NULL DEFAULT '',
                collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (platform, video_id)
            );
            CREATE TABLE IF NOT EXISTS comments (
                platform TEXT NOT NULL, video_id TEXT NOT NULL, comment_id TEXT NOT NULL,
                text TEXT NOT NULL, likes INTEGER NOT NULL DEFAULT 0, author TEXT NOT NULL DEFAULT '',
                parent_id TEXT NOT NULL DEFAULT '', reply_to_id TEXT NOT NULL DEFAULT '',
                author_id TEXT NOT NULL DEFAULT '', published_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (platform, video_id, comment_id)
            );
            CREATE TABLE IF NOT EXISTS segments (
                platform TEXT NOT NULL, video_id TEXT NOT NULL, segment_index INTEGER NOT NULL,
                start_seconds REAL NOT NULL, end_seconds REAL NOT NULL, status TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (platform, video_id, segment_index)
            );
            CREATE TABLE IF NOT EXISTS comment_marks (
                platform TEXT NOT NULL, video_id TEXT NOT NULL, comment_id TEXT NOT NULL,
                PRIMARY KEY (platform, video_id, comment_id)
            );
            CREATE TABLE IF NOT EXISTS discoveries (
                platform TEXT NOT NULL, profile_url TEXT NOT NULL, author_id TEXT NOT NULL DEFAULT '',
                author_name TEXT NOT NULL DEFAULT '', complete INTEGER NOT NULL,
                stop_reason TEXT NOT NULL, video_count INTEGER NOT NULL, video_urls TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (platform, profile_url)
            );
            CREATE TABLE IF NOT EXISTS collections (
                platform TEXT NOT NULL, collection_id TEXT NOT NULL, url TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '', complete INTEGER NOT NULL DEFAULT 0,
                stop_reason TEXT NOT NULL DEFAULT '未采集', video_urls TEXT NOT NULL DEFAULT '[]',
                video_titles TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (platform, collection_id)
            );
            CREATE TABLE IF NOT EXISTS api_tasks (
                task_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(discoveries)")}
        if "video_urls" not in columns:
            self.db.execute("ALTER TABLE discoveries ADD COLUMN video_urls TEXT NOT NULL DEFAULT '[]'")
        video_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(videos)")}
        if "content_type" not in video_columns:
            self.db.execute("ALTER TABLE videos ADD COLUMN content_type TEXT NOT NULL DEFAULT 'video'")
        if "image_urls" not in video_columns:
            self.db.execute("ALTER TABLE videos ADD COLUMN image_urls TEXT NOT NULL DEFAULT '[]'")
        if "comments_version" not in video_columns:
            self.db.execute("ALTER TABLE videos ADD COLUMN comments_version INTEGER NOT NULL DEFAULT 1")
        video_additions = {
            "description": "TEXT NOT NULL DEFAULT ''",
            "published_at": "TEXT NOT NULL DEFAULT ''",
            "likes": "INTEGER NOT NULL DEFAULT 0",
            "favorites": "INTEGER NOT NULL DEFAULT 0",
            "shares": "INTEGER NOT NULL DEFAULT 0",
            "cover_url": "TEXT NOT NULL DEFAULT ''",
            "duration_seconds": "REAL NOT NULL DEFAULT 0",
            "local_media_path": "TEXT NOT NULL DEFAULT ''",
            "collected_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in video_additions.items():
            if name not in video_columns:
                self.db.execute(f"ALTER TABLE videos ADD COLUMN {name} {definition}")
        comment_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(comments)")}
        if "parent_id" not in comment_columns:
            self.db.execute("ALTER TABLE comments ADD COLUMN parent_id TEXT NOT NULL DEFAULT ''")
        if "reply_to_id" not in comment_columns:
            self.db.execute("ALTER TABLE comments ADD COLUMN reply_to_id TEXT NOT NULL DEFAULT ''")
        if "author_id" not in comment_columns:
            self.db.execute("ALTER TABLE comments ADD COLUMN author_id TEXT NOT NULL DEFAULT ''")
        if "published_at" not in comment_columns:
            self.db.execute("ALTER TABLE comments ADD COLUMN published_at TEXT NOT NULL DEFAULT ''")
        collection_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(collections)")}
        if "video_titles" not in collection_columns:
            self.db.execute("ALTER TABLE collections ADD COLUMN video_titles TEXT NOT NULL DEFAULT '{}'")
        self.db.commit()

    def close(self):
        self.db.close()

    def marked_comments(self, platform: str, video_id: str) -> set[str]:
        return {row[0] for row in self.db.execute(
            "SELECT comment_id FROM comment_marks WHERE platform=? AND video_id=?", (platform, video_id))}

    def mark_comment(self, platform: str, video_id: str, comment_id: str, marked: bool):
        if not self.db.execute("SELECT 1 FROM comments WHERE platform=? AND video_id=? AND comment_id=?",
                               (platform, video_id, comment_id)).fetchone():
            raise ValueError("评论不存在")
        with self.db:
            if marked:
                self.db.execute("INSERT OR IGNORE INTO comment_marks VALUES (?,?,?)", (platform, video_id, comment_id))
            else:
                self.db.execute("DELETE FROM comment_marks WHERE platform=? AND video_id=? AND comment_id=?",
                                (platform, video_id, comment_id))

    def get_video(self, platform: str, video_id: str) -> Video | None:
        row = self.db.execute("SELECT * FROM videos WHERE platform=? AND video_id=?", (platform, video_id)).fetchone()
        return self._video(row) if row else None

    @staticmethod
    def _video(row) -> Video:
        data = dict(row)
        data["hashtags"] = json.loads(data["hashtags"])
        data["image_urls"] = json.loads(data["image_urls"])
        data["comments_complete"] = bool(data["comments_complete"])
        return Video(**data)

    def save_video(self, video: Video):
        self.db.execute("""
            INSERT INTO videos (platform,video_id,url,title,author_id,author_name,hashtags,tags_status,
              metadata_status,metadata_error,comments_complete,comments_stop_reason,comments_version,transcript_status,transcript,transcript_error,
              content_type,image_urls,description,published_at,likes,favorites,shares,cover_url,duration_seconds,local_media_path)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(platform,video_id) DO UPDATE SET
              url=excluded.url,title=excluded.title,author_id=excluded.author_id,author_name=excluded.author_name,
              hashtags=excluded.hashtags,tags_status=excluded.tags_status,metadata_status=excluded.metadata_status,
              metadata_error=excluded.metadata_error,comments_complete=excluded.comments_complete,
              comments_stop_reason=excluded.comments_stop_reason,comments_version=excluded.comments_version,
              transcript_status=excluded.transcript_status,
              transcript=excluded.transcript,transcript_error=excluded.transcript_error,
              content_type=excluded.content_type,image_urls=excluded.image_urls,
              description=excluded.description,published_at=excluded.published_at,
              likes=excluded.likes,favorites=excluded.favorites,shares=excluded.shares,
              cover_url=excluded.cover_url,duration_seconds=excluded.duration_seconds,
              local_media_path=excluded.local_media_path,updated_at=CURRENT_TIMESTAMP
        """, (video.platform, video.video_id, video.url, video.title, video.author_id, video.author_name,
              json.dumps(video.hashtags, ensure_ascii=False), video.tags_status, video.metadata_status,
              video.metadata_error, int(video.comments_complete), video.comments_stop_reason, video.comments_version,
              video.transcript_status, video.transcript, video.transcript_error,
              video.content_type, json.dumps(video.image_urls, ensure_ascii=False),
              video.description, video.published_at, video.likes, video.favorites, video.shares,
              video.cover_url, video.duration_seconds, video.local_media_path))
        self.db.commit()

    def save_comments(self, platform: str, video_id: str, result: CommentResult, replace: bool = False):
        with self.db:
            if replace:
                self.db.execute("DELETE FROM comments WHERE platform=? AND video_id=?", (platform, video_id))
            for comment in result.comments:
                self.db.execute("""INSERT INTO comments
                    (platform,video_id,comment_id,text,likes,author,parent_id,reply_to_id,author_id,published_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(platform,video_id,comment_id) DO UPDATE SET
                    text=excluded.text,likes=excluded.likes,author=excluded.author,
                    parent_id=excluded.parent_id,reply_to_id=excluded.reply_to_id,
                    author_id=excluded.author_id,published_at=excluded.published_at""",
                    (platform, video_id, comment.comment_id, comment.text, comment.likes,
                     comment.author, comment.parent_id, comment.reply_to_id,
                     comment.author_id, comment.published_at))
            self.db.execute("UPDATE videos SET comments_complete=?,comments_stop_reason=?,comments_version=2 WHERE platform=? AND video_id=?",
                            (int(result.complete), result.stop_reason, platform, video_id))

    def save_segments(self, platform: str, video_id: str, segments: list[SegmentResult]):
        with self.db:
            for part in segments:
                self.db.execute("""INSERT INTO segments VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(platform,video_id,segment_index) DO UPDATE SET
                    status=excluded.status,text=excluded.text,error=excluded.error""",
                    (platform, video_id, part.index, part.start_seconds, part.end_seconds,
                     part.status, part.text, part.error))

    def segments(self, platform: str, video_id: str) -> list[SegmentResult]:
        rows = self.db.execute("SELECT * FROM segments WHERE platform=? AND video_id=? ORDER BY segment_index",
                               (platform, video_id)).fetchall()
        return [SegmentResult(row["segment_index"], row["start_seconds"], row["end_seconds"],
                              row["status"], row["text"], row["error"]) for row in rows]

    def save_discovery(self, platform: str, url: str, result: Discovery):
        self.db.execute("""INSERT INTO discoveries (platform,profile_url,author_id,author_name,complete,stop_reason,video_count,video_urls)
          VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(platform,profile_url) DO UPDATE SET
          author_id=excluded.author_id,author_name=excluded.author_name,complete=excluded.complete,
          stop_reason=excluded.stop_reason,video_count=excluded.video_count,video_urls=excluded.video_urls,
          updated_at=CURRENT_TIMESTAMP""",
          (platform, url, result.author_id, result.author_name, int(result.complete), result.stop_reason,
           len(result.video_urls), json.dumps(result.video_urls, ensure_ascii=False)))
        self.db.commit()

    def get_discovery(self, platform: str, url: str) -> Discovery | None:
        row = self.db.execute("SELECT * FROM discoveries WHERE platform=? AND profile_url=?", (platform, url)).fetchone()
        return Discovery(json.loads(row["video_urls"]), bool(row["complete"]), row["stop_reason"],
                         row["author_id"], row["author_name"]) if row else None

    def all_videos(self) -> list[Video]:
        return [self._video(row) for row in self.db.execute("SELECT * FROM videos ORDER BY author_name,video_id")]

    def delete_video(self, platform: str, video_id: str) -> bool:
        with self.db:
            # Delete dependent rows first when present, then delete the video row.
            # SQLite DELETE is safe when a related row is already absent.
            self.db.execute("DELETE FROM comment_marks WHERE platform=? AND video_id=?", (platform, video_id))
            self.db.execute("DELETE FROM comments WHERE platform=? AND video_id=?", (platform, video_id))
            self.db.execute("DELETE FROM segments WHERE platform=? AND video_id=?", (platform, video_id))
            self.db.execute("DELETE FROM videos WHERE platform=? AND video_id=?", (platform, video_id))
        # Make deletion idempotent: a stale list entry is treated as already deleted.
        return True

    def comments(self, platform: str, video_id: str) -> list[Comment]:
        rows = self.db.execute("SELECT * FROM comments WHERE platform=? AND video_id=? ORDER BY likes DESC, comment_id",
                               (platform, video_id)).fetchall()
        return [Comment(row["comment_id"], row["text"], row["likes"], row["author"],
                        row["parent_id"], row["reply_to_id"], row["author_id"], row["published_at"])
                for row in rows]

    def discoveries(self, platform: str, author_id: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM discoveries WHERE platform=? AND author_id=? ORDER BY profile_url",
                               (platform, author_id)).fetchall()

    def all_discoveries(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM discoveries ORDER BY platform,author_id,profile_url").fetchall()

    def save_collection(self, platform: str, result: Collection):
        self.db.execute("""INSERT INTO collections
            (platform,collection_id,url,name,complete,stop_reason,video_urls,video_titles)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(platform,collection_id) DO UPDATE SET
            url=excluded.url,name=excluded.name,complete=excluded.complete,
            stop_reason=excluded.stop_reason,video_urls=excluded.video_urls,video_titles=excluded.video_titles,
            updated_at=CURRENT_TIMESTAMP""",
            (platform, result.collection_id, result.url, result.name, int(result.complete),
             result.stop_reason, json.dumps(result.video_urls, ensure_ascii=False),
             json.dumps(result.video_titles, ensure_ascii=False)))
        self.db.commit()

    def get_collection(self, platform: str, collection_id: str) -> Collection | None:
        row = self.db.execute("SELECT * FROM collections WHERE platform=? AND collection_id=?",
                              (platform, collection_id)).fetchone()
        return (Collection(row["collection_id"], row["url"], row["name"],
                           json.loads(row["video_urls"]), bool(row["complete"]), row["stop_reason"],
                           json.loads(row["video_titles"]))
                if row else None)

    def all_collections(self) -> list[Collection]:
        return [Collection(row["collection_id"], row["url"], row["name"],
                           json.loads(row["video_urls"]), bool(row["complete"]), row["stop_reason"],
                           json.loads(row["video_titles"]))
                for row in self.db.execute("SELECT * FROM collections ORDER BY platform,collection_id")]

    def save_task(self, task: dict):
        payload = json.dumps(task, ensure_ascii=False)
        self.db.execute("""INSERT INTO api_tasks (task_id,payload,created_at)
            VALUES (?,?,?) ON CONFLICT(task_id) DO UPDATE SET
            payload=excluded.payload,updated_at=CURRENT_TIMESTAMP""",
            (task["id"], payload, task["created_at"]))
        self.db.commit()

    def get_task(self, task_id: str) -> dict | None:
        row = self.db.execute("SELECT payload FROM api_tasks WHERE task_id=?", (task_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def all_tasks(self) -> list[dict]:
        rows = self.db.execute("SELECT payload FROM api_tasks ORDER BY created_at DESC").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def delete_task(self, task_id: str) -> bool:
        cursor = self.db.execute("DELETE FROM api_tasks WHERE task_id=?", (task_id,))
        self.db.commit()
        return cursor.rowcount > 0
