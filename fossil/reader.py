"""Read-only interface to Fossil's SQLite database.

Each .fossil file is a SQLite database containing all repo data:
code, timeline, tickets, wiki, forum. This module reads them directly
without requiring the fossil binary.
"""

import contextlib
import sqlite3
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class TimelineEntry:
    rid: int
    uuid: str
    event_type: str  # ci=checkin, w=wiki, t=ticket, g=tag, e=technote, f=forum
    timestamp: datetime
    user: str
    comment: str
    branch: str = ""
    parent_rid: int = 0  # primary parent rid for DAG drawing
    is_merge: bool = False  # has multiple parents
    rail: int = 0  # column position for DAG graph


@dataclass
class FileEntry:
    name: str
    uuid: str
    size: int
    is_dir: bool = False
    last_commit_message: str = ""
    last_commit_user: str = ""
    last_commit_time: datetime | None = None


@dataclass
class CheckinDetail:
    uuid: str
    timestamp: datetime
    user: str
    comment: str
    branch: str = ""
    parent_uuid: str = ""
    is_merge: bool = False
    files_changed: list = None  # list of dicts: {name, change_type, uuid, prev_uuid}

    def __post_init__(self):
        if self.files_changed is None:
            self.files_changed = []


@dataclass
class TicketEntry:
    uuid: str
    title: str
    status: str
    type: str
    created: datetime
    owner: str
    subsystem: str = ""
    priority: str = ""
    severity: str = ""
    resolution: str = ""
    body: str = ""  # main comment/description


@dataclass
class WikiPage:
    name: str
    content: str
    last_modified: datetime
    user: str


@dataclass
class ForumPost:
    uuid: str
    title: str
    body: str
    timestamp: datetime
    user: str
    in_reply_to: str = ""


@dataclass
class RepoMetadata:
    project_name: str = ""
    project_code: str = ""
    checkin_count: int = 0
    file_count: int = 0
    wiki_page_count: int = 0
    ticket_count: int = 0
    branches: list[str] = field(default_factory=list)


def _julian_to_datetime(julian: float) -> datetime:
    """Convert Julian day number to Python datetime (UTC)."""

    # Julian day epoch is Jan 1, 4713 BC (proleptic Julian calendar)
    # Unix epoch in Julian days = 2440587.5
    unix_ts = (julian - 2440587.5) * 86400.0
    return datetime.fromtimestamp(unix_ts, tz=UTC)


def _decompress_blob(data: bytes) -> bytes:
    """Decompress a Fossil blob.

    Fossil stores blobs with a 4-byte big-endian size prefix followed by
    zlib-compressed content. The size prefix is the uncompressed size.
    """
    if not data:
        return b""
    # Fossil prepends uncompressed size as 4-byte big-endian int
    if len(data) > 4:
        payload = data[4:]
        try:
            return zlib.decompress(payload)
        except zlib.error:
            pass
    # Fallback: try without size prefix
    try:
        return zlib.decompress(data)
    except zlib.error:
        pass
    try:
        return zlib.decompress(data, -zlib.MAX_WBITS)
    except zlib.error:
        return data  # Already uncompressed or unknown format


def _extract_wiki_content(artifact_text: str) -> str:
    """Extract wiki body from a Fossil wiki artifact.

    Format: header cards (D/L/P/U lines), then W <size>\\n<content>\\nZ <hash>
    The W card specifies the byte count of the content that follows.
    """
    import re

    match = re.search(r"^W (\d+)\n", artifact_text, re.MULTILINE)
    if not match:
        return ""
    start = match.end()
    size = int(match.group(1))
    return artifact_text[start : start + size]


class FossilReader:
    """Read-only interface to a .fossil SQLite database."""

    def __init__(self, path: Path):
        self.path = path
        self._conn: sqlite3.Connection | None = None

    def __enter__(self):
        self._conn = self._connect()
        return self

    def __exit__(self, *args):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # --- Metadata ---

    def get_metadata(self) -> RepoMetadata:
        meta = RepoMetadata()
        meta.project_name = self.get_project_name()
        meta.project_code = self.get_project_code()
        meta.checkin_count = self.get_checkin_count()
        with contextlib.suppress(sqlite3.OperationalError):
            meta.ticket_count = self.conn.execute("SELECT count(*) FROM ticket").fetchone()[0]
        with contextlib.suppress(sqlite3.OperationalError):
            meta.wiki_page_count = self.conn.execute(
                "SELECT count(DISTINCT substr(tagname,6)) FROM tag WHERE tagname LIKE 'wiki-%'"
            ).fetchone()[0]
        return meta

    def get_project_name(self) -> str:
        try:
            row = self.conn.execute("SELECT value FROM config WHERE name='project-name'").fetchone()
            return row[0] if row else ""
        except sqlite3.OperationalError:
            return ""

    def get_project_code(self) -> str:
        try:
            row = self.conn.execute("SELECT value FROM config WHERE name='project-code'").fetchone()
            return row[0] if row else ""
        except sqlite3.OperationalError:
            return ""

    def get_checkin_count(self) -> int:
        try:
            row = self.conn.execute("SELECT count(*) FROM event WHERE type='ci'").fetchone()
            return row[0] if row else 0
        except sqlite3.OperationalError:
            return 0

    # --- Timeline ---

    def get_timeline(self, limit: int = 50, offset: int = 0, event_type: str | None = None) -> list[TimelineEntry]:
        sql = """
            SELECT blob.rid, blob.uuid, event.type, event.mtime, event.user, event.comment
            FROM event
            JOIN blob ON event.objid = blob.rid
        """
        params: list = []
        if event_type:
            sql += " WHERE event.type = ?"
            params.append(event_type)
        sql += " ORDER BY event.mtime DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        entries = []
        try:
            for row in self.conn.execute(sql, params):
                branch = ""
                parent_rid = 0
                is_merge = False

                try:
                    br = self.conn.execute(
                        "SELECT tag.tagname FROM tagxref JOIN tag ON tagxref.tagid=tag.tagid "
                        "WHERE tagxref.rid=? AND tag.tagname LIKE 'sym-%'",
                        (row["rid"],),
                    ).fetchone()
                    if br:
                        branch = br[0].replace("sym-", "", 1)
                except sqlite3.OperationalError:
                    pass

                # Get parent info from plink for DAG
                if row["type"] == "ci":
                    try:
                        parents = self.conn.execute("SELECT pid, isprim FROM plink WHERE cid=?", (row["rid"],)).fetchall()
                        for p in parents:
                            if p["isprim"]:
                                parent_rid = p["pid"]
                        is_merge = len(parents) > 1
                    except sqlite3.OperationalError:
                        pass

                entries.append(
                    TimelineEntry(
                        rid=row["rid"],
                        uuid=row["uuid"],
                        event_type=row["type"],
                        timestamp=_julian_to_datetime(row["mtime"]),
                        user=row["user"] or "",
                        comment=row["comment"] or "",
                        branch=branch,
                        parent_rid=parent_rid,
                        is_merge=is_merge,
                    )
                )
        except sqlite3.OperationalError:
            pass

        # Assign rail positions based on branches
        branch_rails: dict[str, int] = {}
        next_rail = 0
        for entry in entries:
            if entry.event_type != "ci":
                entry.rail = -1  # non-checkin events don't get a rail
                continue
            b = entry.branch or "trunk"
            if b not in branch_rails:
                branch_rails[b] = next_rail
                next_rail += 1
            entry.rail = branch_rails[b]

        return entries

    # --- Checkin Detail ---

    def get_checkin_detail(self, uuid: str) -> CheckinDetail | None:
        """Get full details for a specific checkin, including changed files."""
        try:
            row = self.conn.execute(
                "SELECT blob.rid, blob.uuid, event.mtime, event.user, event.comment "
                "FROM event JOIN blob ON event.objid=blob.rid "
                "WHERE blob.uuid LIKE ? AND event.type='ci'",
                (uuid + "%",),
            ).fetchone()
            if not row:
                return None

            rid = row["rid"]
            full_uuid = row["uuid"]

            # Get branch
            branch = ""
            try:
                br = self.conn.execute(
                    "SELECT tag.tagname FROM tagxref JOIN tag ON tagxref.tagid=tag.tagid WHERE tagxref.rid=? AND tag.tagname LIKE 'sym-%'",
                    (rid,),
                ).fetchone()
                if br:
                    branch = br[0].replace("sym-", "", 1)
            except sqlite3.OperationalError:
                pass

            # Get parent
            parent_uuid = ""
            is_merge = False
            try:
                parents = self.conn.execute("SELECT pid, isprim FROM plink WHERE cid=?", (rid,)).fetchall()
                for p in parents:
                    if p["isprim"]:
                        parent_row = self.conn.execute("SELECT uuid FROM blob WHERE rid=?", (p["pid"],)).fetchone()
                        if parent_row:
                            parent_uuid = parent_row["uuid"]
                is_merge = len(parents) > 1
            except sqlite3.OperationalError:
                pass

            # Get changed files from mlink
            files_changed = []
            try:
                mlinks = self.conn.execute(
                    """
                    SELECT fn.name, ml.fid, ml.pid,
                           b_new.uuid as new_uuid,
                           b_old.uuid as old_uuid
                    FROM mlink ml
                    JOIN filename fn ON ml.fnid = fn.fnid
                    LEFT JOIN blob b_new ON ml.fid = b_new.rid
                    LEFT JOIN blob b_old ON ml.pid = b_old.rid
                    WHERE ml.mid = ?
                    ORDER BY fn.name
                    """,
                    (rid,),
                ).fetchall()
                for ml in mlinks:
                    if ml["fid"] == 0:
                        change_type = "deleted"
                    elif ml["pid"] == 0:
                        change_type = "added"
                    else:
                        change_type = "modified"
                    files_changed.append(
                        {
                            "name": ml["name"],
                            "change_type": change_type,
                            "uuid": ml["new_uuid"] or "",
                            "prev_uuid": ml["old_uuid"] or "",
                        }
                    )
            except sqlite3.OperationalError:
                pass

            return CheckinDetail(
                uuid=full_uuid,
                timestamp=_julian_to_datetime(row["mtime"]),
                user=row["user"] or "",
                comment=row["comment"] or "",
                branch=branch,
                parent_uuid=parent_uuid,
                is_merge=is_merge,
                files_changed=files_changed,
            )
        except sqlite3.OperationalError:
            return None

    # --- Code / Files ---

    def get_latest_checkin_uuid(self) -> str | None:
        try:
            row = self.conn.execute(
                "SELECT blob.uuid FROM event JOIN blob ON event.objid=blob.rid WHERE event.type='ci' ORDER BY event.mtime DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else None
        except sqlite3.OperationalError:
            return None

    def get_files_at_checkin(self, checkin_uuid: str | None = None) -> list[FileEntry]:
        """Get the cumulative file list at a given checkin, with last commit info per file."""
        if checkin_uuid is None:
            checkin_uuid = self.get_latest_checkin_uuid()
        if not checkin_uuid:
            return []

        try:
            # Build cumulative file state: for each filename, find the latest mlink entry
            # where fid > 0 (fid=0 means file was deleted)
            rows = self.conn.execute(
                """
                SELECT fn.name, b.uuid, b.size,
                       e.comment, e.user, e.mtime
                FROM (
                    SELECT ml.fnid, ml.fid,
                           MAX(e2.mtime) as max_mtime
                    FROM mlink ml
                    JOIN event e2 ON ml.mid = e2.objid
                    WHERE e2.type = 'ci'
                    GROUP BY ml.fnid
                ) latest
                JOIN mlink ml2 ON ml2.fnid = latest.fnid
                JOIN event e ON ml2.mid = e.objid AND e.mtime = latest.max_mtime AND e.type = 'ci'
                JOIN filename fn ON latest.fnid = fn.fnid
                LEFT JOIN blob b ON ml2.fid = b.rid
                WHERE ml2.fid > 0
                ORDER BY fn.name
                """,
            ).fetchall()

            return [
                FileEntry(
                    name=r["name"],
                    uuid=r["uuid"] or "",
                    size=r["size"] or 0,
                    last_commit_message=r["comment"] or "",
                    last_commit_user=r["user"] or "",
                    last_commit_time=_julian_to_datetime(r["mtime"]) if r["mtime"] else None,
                )
                for r in rows
            ]
        except sqlite3.OperationalError:
            return []

    def get_file_content(self, blob_uuid: str) -> bytes:
        try:
            row = self.conn.execute("SELECT content FROM blob WHERE uuid=?", (blob_uuid,)).fetchone()
            if not row or not row[0]:
                return b""
            return _decompress_blob(row[0])
        except sqlite3.OperationalError:
            return b""

    # --- Tickets ---

    def get_tickets(self, status: str | None = None, limit: int = 50) -> list[TicketEntry]:
        sql = "SELECT tkt_uuid, title, status, type, tkt_ctime, subsystem, priority FROM ticket"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY tkt_ctime DESC LIMIT ?"
        params.append(limit)

        entries = []
        try:
            for row in self.conn.execute(sql, params):
                entries.append(
                    TicketEntry(
                        uuid=row["tkt_uuid"] or "",
                        title=row["title"] or "",
                        status=row["status"] or "",
                        type=row["type"] or "",
                        created=_julian_to_datetime(row["tkt_ctime"]) if row["tkt_ctime"] else datetime.now(UTC),
                        owner="",
                        subsystem=row["subsystem"] or "",
                        priority=row["priority"] or "",
                    )
                )
        except sqlite3.OperationalError:
            pass
        return entries

    def get_ticket_detail(self, uuid: str) -> TicketEntry | None:
        try:
            row = self.conn.execute(
                "SELECT tkt_uuid, title, status, type, tkt_ctime, subsystem, priority, severity, resolution, comment "
                "FROM ticket WHERE tkt_uuid LIKE ?",
                (uuid + "%",),
            ).fetchone()
            if not row:
                return None
            return TicketEntry(
                uuid=row["tkt_uuid"],
                title=row["title"] or "",
                status=row["status"] or "",
                type=row["type"] or "",
                created=_julian_to_datetime(row["tkt_ctime"]) if row["tkt_ctime"] else datetime.now(UTC),
                owner="",
                subsystem=row["subsystem"] or "",
                priority=row["priority"] or "",
                severity=row["severity"] or "",
                resolution=row["resolution"] or "",
                body=row["comment"] or "",
            )
        except sqlite3.OperationalError:
            return None

    # --- Wiki ---

    def get_wiki_pages(self) -> list[WikiPage]:
        pages = []
        try:
            rows = self.conn.execute(
                """
                SELECT substr(tag.tagname, 6) as name, event.mtime, event.user
                FROM tag
                JOIN tagxref ON tag.tagid = tagxref.tagid
                JOIN event ON tagxref.rid = event.objid
                WHERE tag.tagname LIKE 'wiki-%' AND event.type = 'w'
                GROUP BY tag.tagname
                HAVING event.mtime = MAX(event.mtime)
                ORDER BY name
                """
            ).fetchall()
            for row in rows:
                pages.append(
                    WikiPage(
                        name=row["name"],
                        content="",
                        last_modified=_julian_to_datetime(row["mtime"]),
                        user=row["user"] or "",
                    )
                )
        except sqlite3.OperationalError:
            pass
        return pages

    def get_wiki_page(self, name: str) -> WikiPage | None:
        try:
            row = self.conn.execute(
                """
                SELECT tagxref.rid, event.mtime, event.user
                FROM tag
                JOIN tagxref ON tag.tagid = tagxref.tagid
                JOIN event ON tagxref.rid = event.objid
                WHERE tag.tagname = ? AND event.type = 'w'
                ORDER BY event.mtime DESC
                LIMIT 1
                """,
                (f"wiki-{name}",),
            ).fetchone()
            if not row:
                return None

            # Read the wiki content from the blob
            blob_row = self.conn.execute("SELECT content FROM blob WHERE rid=?", (row["rid"],)).fetchone()
            content = ""
            if blob_row and blob_row[0]:
                raw = _decompress_blob(blob_row[0])
                text = raw.decode("utf-8", errors="replace")
                # Fossil wiki artifact format: header cards (D/L/P/U) then W <size>\n<content>\nZ <hash>
                content = _extract_wiki_content(text)

            return WikiPage(
                name=name,
                content=content,
                last_modified=_julian_to_datetime(row["mtime"]),
                user=row["user"] or "",
            )
        except sqlite3.OperationalError:
            return None

    # --- Forum ---

    def get_forum_posts(self, limit: int = 50) -> list[ForumPost]:
        """Get root forum posts (thread starters) with body content."""
        posts = []
        try:
            rows = self.conn.execute(
                """
                SELECT b.uuid, fp.fmtime, e.user, e.comment, b.rid
                FROM forumpost fp
                JOIN blob b ON fp.fpid = b.rid
                JOIN event e ON fp.fpid = e.objid
                WHERE fp.firt IS NULL AND fp.fprev IS NULL
                ORDER BY fp.fmtime DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            for row in rows:
                body = self._read_forum_body(row["rid"])
                posts.append(
                    ForumPost(
                        uuid=row["uuid"],
                        title=row["comment"] or "",
                        body=body,
                        timestamp=_julian_to_datetime(row["fmtime"]),
                        user=row["user"] or "",
                    )
                )
        except sqlite3.OperationalError:
            pass
        return posts

    def get_forum_thread(self, root_uuid: str) -> list[ForumPost]:
        """Get all posts in a forum thread by root post UUID."""
        posts = []
        try:
            # Find root post rid
            root_row = self.conn.execute("SELECT rid FROM blob WHERE uuid=?", (root_uuid,)).fetchone()
            if not root_row:
                return []
            root_rid = root_row["rid"]

            rows = self.conn.execute(
                """
                SELECT b.uuid, fp.fmtime, e.user, e.comment, b.rid, fp.firt
                FROM forumpost fp
                JOIN blob b ON fp.fpid = b.rid
                JOIN event e ON fp.fpid = e.objid
                WHERE fp.froot = ?
                ORDER BY fp.fmtime ASC
                """,
                (root_rid,),
            ).fetchall()
            for row in rows:
                body = self._read_forum_body(row["rid"])
                posts.append(
                    ForumPost(
                        uuid=row["uuid"],
                        title=row["comment"] or "",
                        body=body,
                        timestamp=_julian_to_datetime(row["fmtime"]),
                        user=row["user"] or "",
                        in_reply_to=str(row["firt"]) if row["firt"] else "",
                    )
                )
        except sqlite3.OperationalError:
            pass
        return posts

    def _read_forum_body(self, rid: int) -> str:
        """Read and extract body text from a forum post artifact."""
        try:
            row = self.conn.execute("SELECT content FROM blob WHERE rid=?", (rid,)).fetchone()
            if not row or not row[0]:
                return ""
            data = _decompress_blob(row[0])
            text = data.decode("utf-8", errors="replace")
            return _extract_wiki_content(text)
        except Exception:
            return ""
