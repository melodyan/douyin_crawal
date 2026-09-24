"""SQLite state; each public method commits its completed step."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Comment, CommentResult, Discovery, SegmentResult, Video


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
                comments_stop_reason TEXT NOT NULL DEFAULT '未采集', transcript_status TEXT NOT NULL DEFAULT 'pending',
                transcript TEXT NOT NULL DEFAULT '', transcript_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (platform, video_id)
            );
            CREATE TABLE IF NOT EXISTS comments (
                platform TEXT NOT NULL, video_id TEXT NOT NULL, comment_id TEXT NOT NULL,
                text TEXT NOT NULL, likes INTEGER NOT NULL DEFAULT 0, author TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (platform, video_id, comment_id)
            );
            CREATE TABLE IF NOT EXISTS segments (
                platform TEXT NOT NULL, video_id TEXT NOT NULL, segment_index INTEGER NOT NULL,
                start_seconds REAL NOT NULL, end_seconds REAL NOT NULL, status TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (platform, video_id, segment_index)
            );
            CREATE TABLE IF NOT EXISTS discoveries (
                platform TEXT NOT NULL, profile_url TEXT NOT NULL, author_id TEXT NOT NULL DEFAULT '',
                author_name TEXT NOT NULL DEFAULT '', complete INTEGER NOT NULL,
                stop_reason TEXT NOT NULL, video_count INTEGER NOT NULL, video_urls TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (platform, profile_url)
            );
        """)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(discoveries)")}
        if "video_urls" not in columns:
            self.db.execute("ALTER TABLE discoveries ADD COLUMN video_urls TEXT NOT NULL DEFAULT '[]'")
        self.db.commit()

    def close(self):
        self.db.close()

    def get_video(self, platform: str, video_id: str) -> Video | None:
        row = self.db.execute("SELECT * FROM videos WHERE platform=? AND video_id=?", (platform, video_id)).fetchone()
        return self._video(row) if row else None

    @staticmethod
    def _video(row) -> Video:
        data = dict(row)
        data.pop("updated_at", None)
        data["hashtags"] = json.loads(data["hashtags"])
        data["comments_complete"] = bool(data["comments_complete"])
        return Video(**data)

    def save_video(self, video: Video):
        self.db.execute("""
            INSERT INTO videos (platform,video_id,url,title,author_id,author_name,hashtags,tags_status,
              metadata_status,metadata_error,comments_complete,comments_stop_reason,transcript_status,transcript,transcript_error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(platform,video_id) DO UPDATE SET
              url=excluded.url,title=excluded.title,author_id=excluded.author_id,author_name=excluded.author_name,
              hashtags=excluded.hashtags,tags_status=excluded.tags_status,metadata_status=excluded.metadata_status,
              metadata_error=excluded.metadata_error,comments_complete=excluded.comments_complete,
              comments_stop_reason=excluded.comments_stop_reason,transcript_status=excluded.transcript_status,
              transcript=excluded.transcript,transcript_error=excluded.transcript_error,updated_at=CURRENT_TIMESTAMP
        """, (video.platform, video.video_id, video.url, video.title, video.author_id, video.author_name,
              json.dumps(video.hashtags, ensure_ascii=False), video.tags_status, video.metadata_status,
              video.metadata_error, int(video.comments_complete), video.comments_stop_reason,
              video.transcript_status, video.transcript, video.transcript_error))
        self.db.commit()

    def save_comments(self, platform: str, video_id: str, result: CommentResult, replace: bool = False):
        with self.db:
            if replace:
                self.db.execute("DELETE FROM comments WHERE platform=? AND video_id=?", (platform, video_id))
            for comment in result.comments:
                self.db.execute("""INSERT INTO comments VALUES (?,?,?,?,?,?)
                    ON CONFLICT(platform,video_id,comment_id) DO UPDATE SET
                    text=excluded.text,likes=excluded.likes,author=excluded.author""",
                    (platform, video_id, comment.comment_id, comment.text, comment.likes, comment.author))
            self.db.execute("UPDATE videos SET comments_complete=?,comments_stop_reason=? WHERE platform=? AND video_id=?",
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

    def comments(self, platform: str, video_id: str) -> list[Comment]:
        rows = self.db.execute("SELECT * FROM comments WHERE platform=? AND video_id=? ORDER BY likes DESC, comment_id",
                               (platform, video_id)).fetchall()
        return [Comment(row["comment_id"], row["text"], row["likes"], row["author"]) for row in rows]

    def discoveries(self, platform: str, author_id: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM discoveries WHERE platform=? AND author_id=? ORDER BY profile_url",
                               (platform, author_id)).fetchall()

    def all_discoveries(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM discoveries ORDER BY platform,author_id,profile_url").fetchall()
