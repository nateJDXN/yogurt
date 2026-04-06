#!/usr/bin/env python3

import json
import os
import re
import sys
import gzip
import hashlib
import argparse
import logging
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CACHE_PATH = os.path.expanduser(
    "~/Library/Application Support/Granola/cache-v6.json"
)
DEFAULT_SUPABASE_PATH = os.path.expanduser(
    "~/Library/Application Support/Granola/supabase.json"
)
DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Documents/granola-notes")
MIN_CONTENT_LENGTH = 10  # skip notes shorter than this
API_BASE = "https://api.granola.ai/v1"
REQUEST_DELAY = 0.5  # seconds between API requests
RETRY_INTERVAL = 3600  # seconds between retries on API failure
DEFAULT_MAX_RETRIES = 23  # retry for up to ~24 hours

log = logging.getLogger("granola-export")


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False):
    """Configure logging to both stderr and a file next to the output dir."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler (goes to stderr, captured by launchd StandardErrorPath)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(fmt)
    log.addHandler(console)

    log.setLevel(level)


# ---------------------------------------------------------------------------
# Auth / API
# ---------------------------------------------------------------------------

class GranolaAuth:
    """Manages Granola API authentication with automatic token refresh."""

    def __init__(self, supabase_path: str):
        self._supabase_path = supabase_path
        self._access_token = None
        self._refresh_token = None
        self._expires_at = 0  # unix timestamp
        self._load_tokens()

    def _load_tokens(self):
        path = Path(self._supabase_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Supabase config not found: {self._supabase_path}\n"
                "Is Granola installed and have you signed in?"
            )
        with open(path) as f:
            data = json.load(f)

        tokens = data.get("workos_tokens", "{}")
        if isinstance(tokens, str):
            tokens = json.loads(tokens)

        self._access_token = tokens.get("access_token", "")
        self._refresh_token = tokens.get("refresh_token", "")
        obtained_at = tokens.get("obtained_at", 0) / 1000  # ms → seconds
        expires_in = tokens.get("expires_in", 0)
        self._expires_at = obtained_at + expires_in

    def _refresh(self):
        log.info("Refreshing access token...")
        payload = json.dumps({"refresh_token": self._refresh_token}).encode()
        req = urllib.request.Request(
            f"{API_BASE}/refresh-access-token",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp_data = _api_request(req)
        self._access_token = resp_data["access_token"]
        self._refresh_token = resp_data.get("refresh_token", self._refresh_token)
        obtained_at = resp_data.get("obtained_at", time.time() * 1000) / 1000
        expires_in = resp_data.get("expires_in", 21600)
        self._expires_at = obtained_at + expires_in

        # Persist updated tokens back to supabase.json so Granola stays in sync
        path = Path(self._supabase_path)
        with open(path) as f:
            sb = json.load(f)
        sb["workos_tokens"] = json.dumps(resp_data)
        with open(path, "w") as f:
            json.dump(sb, f, indent=2)
        log.info("Token refreshed, expires in %ds", expires_in)

    def get_token(self) -> str:
        # Refresh if token expires within 5 minutes
        if time.time() > (self._expires_at - 300):
            self._refresh()
        return self._access_token


def _api_request(req: urllib.request.Request) -> dict:
    """Execute an API request, handling gzip responses."""
    if "Accept-Encoding" not in req.headers:
        req.add_header("Accept-Encoding", "gzip, deflate")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        try:
            data = gzip.decompress(raw)
        except (gzip.BadGzipFile, OSError):
            data = raw
        return json.loads(data)


def fetch_panels(auth: GranolaAuth, document_id: str) -> list:
    """Fetch AI-generated panels for a document from the Granola API."""
    payload = json.dumps({"document_id": document_id}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/get-document-panels",
        data=payload,
        headers={
            "Authorization": f"Bearer {auth.get_token()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return _api_request(req)


# ---------------------------------------------------------------------------
# TipTap → Markdown converter
# ---------------------------------------------------------------------------

def tiptap_to_markdown(node: dict, depth: int = 0, ordered_index: int = 0) -> str:
    """Convert a TipTap/ProseMirror document node to Markdown."""
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type", "")
    content = node.get("content", [])
    attrs = node.get("attrs", {})

    if node_type == "text":
        text = node.get("text", "")
        marks = node.get("marks", [])
        for mark in marks:
            mt = mark.get("type", "")
            if mt == "bold":
                text = f"**{text}**"
            elif mt == "italic":
                text = f"*{text}*"
            elif mt == "code":
                text = f"`{text}`"
            elif mt == "link":
                href = mark.get("attrs", {}).get("href", "")
                text = f"[{text}]({href})"
        return text

    if node_type == "doc":
        return "".join(tiptap_to_markdown(c, depth) for c in content)

    if node_type == "heading":
        level = attrs.get("level", 1)
        inner = "".join(tiptap_to_markdown(c, depth) for c in content)
        return f"\n{'#' * level} {inner}\n\n"

    if node_type == "paragraph":
        inner = "".join(tiptap_to_markdown(c, depth) for c in content)
        if depth > 0:
            return inner
        return f"{inner}\n\n"

    if node_type == "bulletList":
        return "".join(tiptap_to_markdown(c, depth) for c in content) + (
            "\n" if depth == 0 else ""
        )

    if node_type == "orderedList":
        start = attrs.get("start", 1)
        parts = []
        for i, c in enumerate(content):
            parts.append(tiptap_to_markdown(c, depth, ordered_index=start + i))
        return "".join(parts) + ("\n" if depth == 0 else "")

    if node_type == "listItem":
        indent = "  " * depth
        parts = []
        for c in content:
            parts.append(tiptap_to_markdown(c, depth + 1))
        text = "".join(parts).strip()
        if ordered_index:
            bullet = f"{ordered_index}."
        else:
            bullet = "-"
        return f"{indent}{bullet} {text}\n"

    if node_type == "blockquote":
        inner = "".join(tiptap_to_markdown(c, depth) for c in content)
        lines = inner.strip().split("\n")
        return "\n".join(f"> {line}" for line in lines) + "\n\n"

    if node_type == "codeBlock":
        lang = attrs.get("language", "")
        inner = "".join(tiptap_to_markdown(c, depth) for c in content)
        return f"```{lang}\n{inner}\n```\n\n"

    if node_type == "horizontalRule":
        return "\n---\n\n"

    if node_type == "hardBreak":
        return "\n"

    # Fallback: recurse into children
    return "".join(tiptap_to_markdown(c, depth) for c in content)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text: str, max_len: int = 80) -> str:
    """Convert a title to a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)       # remove non-alphanumeric
    text = re.sub(r"[\s_]+", "-", text)         # spaces/underscores → hyphens
    text = re.sub(r"-{2,}", "-", text)          # collapse multiple hyphens
    text = text.strip("-")
    return text[:max_len]


def format_duration(start_str: str, end_str: str) -> str:
    """Return a human-readable duration like '1h 30m'."""
    try:
        start = datetime.fromisoformat(start_str)
        end = datetime.fromisoformat(end_str)
        delta = end - start
        total_minutes = int(delta.total_seconds() / 60)
        if total_minutes < 60:
            return f"{total_minutes}m"
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    except (ValueError, TypeError):
        return "unknown"


def content_hash(text: str) -> str:
    """Short hash of content for change detection."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def build_header(doc: dict) -> str:
    """Build the document header with title, time, date, and attendees."""
    title = doc.get("title", "Untitled")
    lines = [f"# {title}", ""]

    # Extract time and date from calendar event or created_at
    cal = doc.get("google_calendar_event") or {}
    start_dt_str = cal.get("start", {}).get("dateTime", "")
    created = doc.get("created_at", "")

    dt_str = start_dt_str or created
    if dt_str:
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            lines.append(f"**Time: {dt.strftime('%H:%M')}**")
            lines.append(f"**Date: {dt.strftime('%d-%m-%Y')}**")
        except (ValueError, TypeError):
            pass

    # Attendees
    people = doc.get("people") or {}
    attendees = people.get("attendees") or []
    if attendees:
        names = [att.get("name") or att.get("email", "unknown") for att in attendees]
        lines.append(f"**Attendees: {', '.join(names)}**")

    return "\n".join(lines)


def build_markdown(doc: dict, panel_md: str | None, is_fallback: bool) -> str:
    """Build the full markdown file content for a document."""
    header = build_header(doc)

    parts = [header, ""]

    if is_fallback:
        parts.append("> **Note:** No Granola summary found. Showing raw user notes.\n")

    body = panel_md if panel_md else ""
    if body.strip():
        parts.append(body.strip())
    else:
        parts.append("*No notes recorded.*")

    parts.append("")  # trailing newline
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# State tracking (for incremental exports)
# ---------------------------------------------------------------------------

def load_state(state_path: Path) -> dict:
    """Load previous export state (content hashes) to skip unchanged notes."""
    if state_path.exists():
        try:
            return json.loads(state_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state_path: Path, state: dict):
    """Persist export state."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Main export logic
# ---------------------------------------------------------------------------

def export_notes(
    cache_path: str,
    output_dir: str,
    supabase_path: str,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    """Read the Granola cache, fetch panels, and export notes to markdown."""

    cache_file = Path(cache_path)
    if not cache_file.exists():
        log.error("Cache file not found: %s", cache_path)
        log.error("Is Granola installed and has it been opened at least once?")
        return 1

    # Load cache
    with open(cache_file) as f:
        data = json.load(f)

    docs = data.get("cache", {}).get("state", {}).get("documents", {})
    if not docs:
        log.error("No documents found in cache.")
        return 1

    # Authenticate
    try:
        auth = GranolaAuth(supabase_path)
    except FileNotFoundError as e:
        log.error("%s", e)
        return 1

    output_path = Path(output_dir)
    state_path = output_path / ".export-state.json"
    state = {} if force else load_state(state_path)
    new_state = {}

    exported = 0
    skipped_empty = 0
    skipped_unchanged = 0
    fallback_count = 0
    errors = 0

    total = len(docs)
    log.info("Found %d documents in cache, fetching panels...", total)

    for i, (doc_id, doc) in enumerate(docs.items(), 1):
        title = doc.get("title", "Untitled")
        raw_notes = doc.get("notes_markdown", "") or ""
        created = doc.get("created_at", "")

        # Fetch AI panels from the API
        panel_md = None
        is_fallback = False

        try:
            panels = fetch_panels(auth, doc_id)
            time.sleep(REQUEST_DELAY)
            if panels:
                # Use the most recent panel
                most_recent = max(panels, key=lambda p: p.get("created_at", ""))
                content = most_recent.get("content", {})
                panel_md = tiptap_to_markdown(content).strip()
                log.debug("[%d/%d] %s — panel fetched (%d chars)", i, total, title, len(panel_md))
            else:
                log.debug("[%d/%d] %s — no panels, falling back to raw notes", i, total, title)
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            # API errors are fatal — trigger retry at the top level
            log.error("API error fetching panels for '%s': %s", title, e)
            raise

        # Determine content to use
        if panel_md and len(panel_md.strip()) >= MIN_CONTENT_LENGTH:
            pass  # use panel_md
        elif len(raw_notes.strip()) >= MIN_CONTENT_LENGTH:
            panel_md = raw_notes
            is_fallback = True
            fallback_count += 1
        else:
            skipped_empty += 1
            continue

        # Parse date for directory structure — prefer calendar event date
        cal = doc.get("google_calendar_event") or {}
        event_start = cal.get("start", {}).get("dateTime", "")
        date_source = event_start or created
        try:
            dt = datetime.fromisoformat(date_source.replace("Z", "+00:00"))
            year = dt.strftime("%Y")
            month = dt.strftime("%m")
            date_prefix = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            year = "unknown"
            month = "00"
            date_prefix = "undated"

        # Build file path
        slug = slugify(title)
        if not slug:
            slug = slugify(doc_id[:8])
        filename = f"{date_prefix}-{slug}.md"
        file_path = output_path / year / month / filename

        # Build markdown and hash (without exported_at so hash is stable)
        full_md = build_markdown(doc, panel_md, is_fallback)
        c_hash = content_hash(full_md)
        new_state[doc_id] = c_hash

        # Skip if unchanged since last export
        if state.get(doc_id) == c_hash and file_path.exists():
            skipped_unchanged += 1
            continue

        if dry_run:
            src = "fallback" if is_fallback else "panel"
            log.info("[DRY RUN] Would write (%s): %s", src, file_path)
            exported += 1
            continue

        # Write file
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(full_md, encoding="utf-8")
            exported += 1
        except OSError as e:
            log.error("Error writing %s: %s", file_path, e)
            errors += 1

    # Save state
    if not dry_run:
        merged_state = {**state, **new_state}
        save_state(state_path, merged_state)

    # Summary
    log.info("Export complete:")
    log.info("  Total documents in cache:  %d", total)
    log.info("  Exported (new/updated):    %d", exported)
    log.info("  Exported (fallback notes): %d", fallback_count)
    log.info("  Skipped (empty):           %d", skipped_empty)
    log.info("  Skipped (unchanged):       %d", skipped_unchanged)
    if errors:
        log.info("  Errors:                    %d", errors)
    log.info("  Output directory:          %s", output_dir)

    return 0 if errors == 0 else 1


def run_with_retries(args) -> int:
    """Run the export, retrying on API failure."""
    attempt = 0
    while True:
        try:
            return export_notes(
                args.cache, args.output, args.supabase, args.dry_run, args.force
            )
        except (urllib.error.HTTPError, urllib.error.URLError, ConnectionError) as e:
            attempt += 1
            if attempt > args.max_retries:
                log.error(
                    "Giving up after %d retries. Last error: %s", attempt, e
                )
                return 1
            log.error(
                "API unreachable (attempt %d/%d): %s — retrying in %ds",
                attempt,
                args.max_retries,
                e,
                RETRY_INTERVAL,
            )
            time.sleep(RETRY_INTERVAL)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Export Granola meeting notes to Markdown files."
    )
    parser.add_argument(
        "-c", "--cache",
        default=DEFAULT_CACHE_PATH,
        help=f"Path to Granola cache file (default: {DEFAULT_CACHE_PATH})",
    )
    parser.add_argument(
        "-o", "--output",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "-s", "--supabase",
        default=DEFAULT_SUPABASE_PATH,
        help=f"Path to Granola supabase.json (default: {DEFAULT_SUPABASE_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be exported without writing files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-export all notes, ignoring change detection",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Max hourly retries on API failure (default: {DEFAULT_MAX_RETRIES})",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)
    sys.exit(run_with_retries(args))


if __name__ == "__main__":
    main()