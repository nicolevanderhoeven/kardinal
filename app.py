#!/usr/bin/env python3
"""
Kanban Markdown Renderer
Reads an Obsidian Kanban-formatted Markdown file and renders it as a read-only Kanban board.
"""

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime
from html import escape
from urllib.parse import quote_plus

import bleach
import markdown
import requests
from flask import Flask, jsonify, render_template, request
from markupsafe import Markup

app = Flask(__name__)

# Path to the Markdown file (configurable via environment variable)
MARKDOWN_FILE = os.getenv('KANBAN_MARKDOWN_FILE', '/srv/kardinal/kardinal_public/Grafana Labs Kanban.md')


def _now():
    """Monotonic clock for cache expiry (a seam so tests can move time)."""
    return time.monotonic()


def _env_float(name, default):
    """Read a non-negative float from the environment, falling back to default."""
    try:
        value = float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)
    return value if value >= 0 else float(default)


# Obsidian Publish site that wikilink names are looked up on.
PUBLISH_SEARCH_URL = 'https://publish-01.obsidian.md/search'
PUBLISH_SITE_ID = '186a0d1b800fa85e50d49cb464898e4c'

# --- Outbound lookup limits --------------------------------------------------
# Every unique [[wikilink]] costs one POST to Obsidian Publish. Two limits keep
# an anonymous page view from fanning out without bound:
#   * at most MAX_CONCURRENT_LOOKUPS requests are in flight per process (the
#     pool below is the cap; lookups beyond it wait in the pool's queue), and
#   * one render spends at most LOOKUP_BUDGET_SECONDS on lookups in total, so a
#     slow Obsidian can never push a page past the 10 s monitoring timeout.
#     Names that miss the deadline render as italics (as "not found" does) and
#     are looked up again on the next refresh.
MAX_CONCURRENT_LOOKUPS = 8
LOOKUP_TIMEOUT_SECONDS = 3
LOOKUP_BUDGET_SECONDS = 4

# Thread pool for parallel note lookups
note_lookup_executor = ThreadPoolExecutor(
    max_workers=MAX_CONCURRENT_LOOKUPS, thread_name_prefix='note-lookup'
)

# --- Per-note lookup cache ---------------------------------------------------
# Definitive answers from the search API (found / not found) are cached for an
# hour. Failures (timeouts, errors, non-200) are cached for a much shorter time
# so a slow or down Obsidian costs one failed lookup per note per minute rather
# than one per request, yet still heals quickly. If a note had a definitive
# answer before and a refresh fails, the old answer keeps being served (stale)
# instead of degrading a link to italics.
NOTE_CACHE_MAX = 1000
NOTE_CACHE_TTL_SECONDS = 3600
NOTE_FAILURE_TTL_SECONDS = 60
_note_cache = {}  # note_name -> (expires_at, path or None, definitive)
_note_cache_lock = threading.Lock()


def clear_note_cache():
    """Drop all cached wikilink lookups."""
    with _note_cache_lock:
        _note_cache.clear()


def _store_note_result(note_name, ttl, path, definitive):
    with _note_cache_lock:
        if note_name not in _note_cache and len(_note_cache) >= NOTE_CACHE_MAX:
            _note_cache.clear()
        _note_cache[note_name] = (_now() + ttl, path, definitive)


def _note_lookup_failed(note_name):
    """Remember a failed lookup briefly. Returns the stale definitive answer
    if there is one, otherwise None."""
    with _note_cache_lock:
        previous = _note_cache.get(note_name)
    stale_path = previous[1] if previous and previous[2] else None
    _store_note_result(
        note_name, NOTE_FAILURE_TTL_SECONDS, stale_path, previous is not None and previous[2]
    )
    return stale_path


def find_note_path(note_name):
    """
    Find the path of a note on notes.nicolevanderhoeven.com using the search API.
    Returns the full path (without .md extension) if the note exists, None otherwise.
    Searches in all directories, not just system/cards/.

    Definitive results are cached for an hour. Failures also return None (or the
    last definitive answer, if there was one), are logged, and are cached for a
    short time (NOTE_FAILURE_TTL_SECONDS) so they are not retried on every request.
    """
    now = _now()
    with _note_cache_lock:
        cached = _note_cache.get(note_name)
        if cached and cached[0] > now:
            return cached[1]

    # The Obsidian publish search API typically responds in 0.7-2.5s, so a
    # 0.5s timeout made every lookup fail. Lookups run in parallel via the
    # thread pool, so a longer per-request timeout doesn't slow page loads in
    # the common case.
    try:
        response = requests.post(
            PUBLISH_SEARCH_URL,
            json={'id': PUBLISH_SITE_ID, 'query': [note_name]},
            timeout=LOOKUP_TIMEOUT_SECONDS
        )
        if response.status_code != 200:
            app.logger.warning(
                "Obsidian Publish lookup for %r returned HTTP %s",
                note_name, response.status_code
            )
            return _note_lookup_failed(note_name)
        results = response.json().get('results', [])
    except Exception as e:
        app.logger.warning("Obsidian Publish lookup for %r failed: %s", note_name, e)
        return _note_lookup_failed(note_name)

    # Check if any result matches the note name exactly (in any directory),
    # case-insensitively, and return its path without the .md extension.
    note_path = None
    for result in results:
        filename = os.path.splitext(os.path.basename(result))[0]
        if filename.lower() == note_name.lower():
            note_path = os.path.splitext(result)[0]
            break

    _store_note_result(note_name, NOTE_CACHE_TTL_SECONDS, note_path, True)
    return note_path


WIKILINK_RE = re.compile(r'\[\[([^\]]+)\]\]')


def resolve_wikilinks(note_names, budget=None):
    """
    Look up all unique note names in parallel. Returns {name: path or None}.

    The whole batch gets at most `budget` seconds (LOOKUP_BUDGET_SECONDS by
    default). Names still unresolved at the deadline map to None (rendered as
    italics) and are not cancelled if already running, so their answers still
    land in the note cache for the next render; lookups that never started are
    cancelled.
    """
    if budget is None:
        budget = LOOKUP_BUDGET_SECONDS
    future_to_note = {
        note_lookup_executor.submit(find_note_path, note): note
        for note in set(note_names)
    }
    note_paths = {}
    try:
        for future in as_completed(future_to_note, timeout=budget):
            note_name = future_to_note[future]
            try:
                note_paths[note_name] = future.result()
            except Exception as e:
                app.logger.warning("Wikilink lookup for %r failed: %s", note_name, e)
                note_paths[note_name] = None
    except FuturesTimeoutError:
        pending = [f for f in future_to_note if not f.done()]
        app.logger.warning(
            "Wikilink lookups exceeded the %.1fs budget; %d of %d unresolved",
            budget, len(pending), len(future_to_note)
        )
        for future in pending:
            future.cancel()
    for note_name in future_to_note.values():
        note_paths.setdefault(note_name, None)
    return note_paths


def render_card_markdown(text, note_cache=None):
    """
    Render markdown text to HTML and sanitize it for safe display.
    Allows common markdown features like links, bold, italic, etc.
    Falls back to plain text if rendering fails.
    
    Args:
        text: The markdown text to render
        note_cache: Optional dict of already-resolved {note name: path or None}
            (for batch processing). Names missing from it are looked up here.
    """
    try:
        if note_cache is None:
            note_cache = {}
        missing = [n for n in WIKILINK_RE.findall(text) if n not in note_cache]
        if missing:
            note_cache.update(resolve_wikilinks(missing))
        note_paths = note_cache

        # Parse Wikilinks [[text]] and replace with links or italic
        def process_wikilink(match):
            note_name = match.group(1)
            note_path = note_paths.get(note_name)
            if note_path:
                # URL encode each path segment (spaces become +, special chars like & become %26)
                # Split the path and encode each segment separately
                # This works for both root-level notes (e.g., "Spice Runner") and 
                # notes in subdirectories (e.g., "system/cards/Note Name")
                path_segments = note_path.split('/')
                encoded_segments = [quote_plus(segment) for segment in path_segments]
                encoded_path = '/'.join(encoded_segments)
                url = f'https://notes.nicolevanderhoeven.com/{encoded_path}'
                # Return Markdown link format
                return f'[{note_name}]({url})'
            else:
                # Note doesn't exist, convert to italic
                return f'*{note_name}*'
        
        # Replace [[text]] with either a Markdown link or italic text
        text = WIKILINK_RE.sub(process_wikilink, text)
        
        # Convert markdown to HTML
        html = markdown.markdown(
            text,
            extensions=['extra', 'nl2br']
        )
        
        # Sanitize HTML - allow safe tags and attributes
        allowed_tags = [
            'p', 'br', 'strong', 'em', 'u', 's', 'code', 'pre',
            'a', 'ul', 'ol', 'li', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'
        ]
        allowed_attributes = {
            'a': ['href', 'title', 'target', 'rel']
        }
        
        # Sanitize HTML
        cleaned = bleach.clean(
            html,
            tags=allowed_tags,
            attributes=allowed_attributes,
            protocols=['http', 'https', 'mailto']
        )
        
        # Add rel="noopener noreferrer" and target="_blank" to external links for security
        # Handle links that may or may not already have attributes
        def add_link_security(match):
            link_content = match.group(1)
            href_match = re.search(r'href=["\']([^"\']+)["\']', link_content)
            if href_match:
                url = href_match.group(1)
                # Only add security attributes to external links (http/https)
                if url.startswith(('http://', 'https://')):
                    # Remove existing rel and target if present
                    link_content = re.sub(r'\s+rel=["\'][^"\']*["\']', '', link_content)
                    link_content = re.sub(r'\s+target=["\'][^"\']*["\']', '', link_content)
                    return f'<a {link_content} rel="noopener noreferrer" target="_blank">'
            return match.group(0)
        
        cleaned = re.sub(r'<a\s+([^>]+)>', add_link_security, cleaned)
        
        return Markup(cleaned)
    except Exception as e:
        # Log error and fall back to plain text
        app.logger.error(f"Error rendering markdown: {e}")
        # Escape HTML to prevent XSS, but return as plain text
        return Markup(escape(text))


def parse_kanban_markdown(file_path):
    """
    Parse Obsidian Kanban markdown format:
    - Columns are headers starting with ##
    - Cards are task items (- [ ] or - [x]) under each column
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        return []
    except Exception as e:
        app.logger.error(f"Error reading markdown file: {e}")
        return []

    # First pass: collect all cards grouped by column (without processing wikilinks)
    columns_data = {}  # column_name -> list of cards
    lines = content.split('\n')
    current_column = None
    
    for line in lines:
        # Check for column header (## Header)
        column_match = re.match(r'^##\s+(.+)$', line.strip())
        if column_match:
            # Stop parsing if we encounter Archive column
            column_name = column_match.group(1)
            if column_name.strip().lower() == 'archive':
                break
            current_column = column_name
            if current_column not in columns_data:
                columns_data[current_column] = []
        # Check for task item (- [ ] or - [x])
        elif current_column is not None:
            task_match = re.match(r'^-\s+\[([ x])\]\s+(.+)$', line.strip())
            if task_match:
                is_completed = task_match.group(1) == 'x'
                card_text = task_match.group(2)
                # Remove leading list markers and spaces
                card_text = re.sub(r'^[-*+]\s+', '', card_text)
                card_text = re.sub(r'^\d+\.\s+', '', card_text)
                card_text = card_text.lstrip()
                columns_data[current_column].append({
                    'text': card_text,
                    'completed': is_completed
                })
    
    # Second pass: collect ALL wikilinks from ALL cards and resolve them in one batch
    all_wikilinks = set()
    for column_cards in columns_data.values():
        for card in column_cards:
            all_wikilinks.update(WIKILINK_RE.findall(card['text']))
    note_cache = resolve_wikilinks(all_wikilinks) if all_wikilinks else {}

    # Third pass: render all cards with pre-resolved wikilinks and build columns
    columns = []
    for column_name, cards in columns_data.items():
        rendered_cards = []
        for card in cards:
            rendered_html = render_card_markdown(card['text'], note_cache=note_cache)
            rendered_cards.append({
                'text': card['text'],
                'html': rendered_html,
                'completed': card['completed']
            })
        columns.append({
            'name': column_name,
            'cards': rendered_cards,
            'is_bottom': False
        })

    # Mark the last column as the bottom (collapsed) row, regardless of its name.
    # Only do this when there are multiple columns - a single column should render
    # normally in the top row instead of being collapsed at the bottom.
    if len(columns) > 1:
        columns[-1]['is_bottom'] = True

    return columns


# --- Rendered-board cache ----------------------------------------------------
# Rendering the board means one Obsidian Publish lookup per unique wikilink, so
# the parsed and sanitised board is cached per process:
#   * a copy younger than PAGE_CACHE_TTL_SECONDS (and for an unchanged file) is
#     served as is;
#   * an older copy is still served immediately while ONE background thread
#     refreshes it (stale-while-revalidate), so readers never wait on Obsidian;
#   * if a refresh fails, the old copy keeps being served (for up to
#     PAGE_MAX_STALE_SECONDS) and the next attempt is delayed by
#     PAGE_RETRY_SECONDS;
#   * only the very first render after a start (nothing to serve yet) blocks,
#     and concurrent requests share that one render instead of each doing
#     their own lookups.
# Each gunicorn worker process has its own cache. Set KARDINAL_PAGE_CACHE_TTL=0
# to disable the page cache (the per-note lookup cache stays on).
PAGE_CACHE_TTL_SECONDS = _env_float('KARDINAL_PAGE_CACHE_TTL', 60)
PAGE_RETRY_SECONDS = 15
PAGE_MAX_STALE_SECONDS = 3600


class _BoardEntry:
    __slots__ = ('columns', 'signature', 'rendered_at', 'next_refresh_at')

    def __init__(self, columns, signature, rendered_at, next_refresh_at):
        self.columns = columns
        self.signature = signature
        self.rendered_at = rendered_at
        self.next_refresh_at = next_refresh_at


_board_cache = {}  # file path -> _BoardEntry
_board_lock = threading.Lock()      # guards _board_cache and _refreshing
_refreshing = set()                 # paths with a background refresh running
_render_lock = threading.Lock()     # one render at a time per process


def clear_board_cache():
    """Drop all cached board renders."""
    with _board_lock:
        _board_cache.clear()


def _file_signature(path):
    """(mtime_ns, size) of the board file, or None if it can't be read."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _refresh_board(path):
    """Render the board and update the cache. Returns the columns to serve.

    On failure, keeps serving the previous copy if there is a usable one."""
    with _render_lock:
        signature = _file_signature(path)
        now = _now()
        with _board_lock:
            entry = _board_cache.get(path)
        # Another thread may have refreshed while we waited for the lock.
        if (entry and entry.signature == signature and now < entry.next_refresh_at):
            return entry.columns

        usable_stale = entry is not None and now - entry.rendered_at < PAGE_MAX_STALE_SECONDS
        try:
            columns = parse_kanban_markdown(path)
            failed = not columns and usable_stale  # unreadable file, not a real empty board
        except Exception:
            if not usable_stale:
                raise
            app.logger.exception("Board refresh failed; serving the previous copy")
            columns, failed = None, True

        now = _now()
        with _board_lock:
            if failed:
                entry.signature = signature
                entry.next_refresh_at = now + PAGE_RETRY_SECONDS
                return entry.columns
            if columns:
                _board_cache[path] = _BoardEntry(
                    columns, signature, now, now + PAGE_CACHE_TTL_SECONDS
                )
        return columns


def _refresh_board_in_background(path):
    with _board_lock:
        if path in _refreshing:
            return
        _refreshing.add(path)

    def run():
        try:
            _refresh_board(path)
        except Exception:
            app.logger.exception("Background board refresh failed")
        finally:
            with _board_lock:
                _refreshing.discard(path)

    threading.Thread(target=run, name='board-refresh', daemon=True).start()


def get_board(path):
    """Return the rendered board columns for `path`, from cache when possible."""
    if PAGE_CACHE_TTL_SECONDS <= 0:
        return parse_kanban_markdown(path)

    signature = _file_signature(path)
    now = _now()
    with _board_lock:
        entry = _board_cache.get(path)
    if entry is not None:
        if entry.signature == signature and now < entry.next_refresh_at:
            return entry.columns
        if now - entry.rendered_at < PAGE_MAX_STALE_SECONDS:
            # Expired, or the file changed: serve this copy, refresh behind it.
            _refresh_board_in_background(path)
            return entry.columns
    return _refresh_board(path)


@app.route('/')
def kanban_board():
    """Render the Kanban board from the Markdown file."""
    columns = get_board(MARKDOWN_FILE)
    return render_template('kanban.html', columns=columns)


# --- Security headers --------------------------------------------------------
# Sent by the app itself so they survive a replaced reverse-proxy config. The
# page has no inline script or style, no external resources and no forms, which
# is what makes this CSP possible. HSTS starts short; raise it with
# KARDINAL_HSTS_MAX_AGE (or in Traefik) once HTTPS is known to work everywhere.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
    "connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
HSTS_MAX_AGE = int(_env_float('KARDINAL_HSTS_MAX_AGE', 300))


@app.after_request
def add_security_headers(response):
    headers = response.headers
    headers.setdefault('Content-Security-Policy', CONTENT_SECURITY_POLICY)
    headers.setdefault('Strict-Transport-Security', f'max-age={HSTS_MAX_AGE}')
    headers.setdefault('X-Content-Type-Options', 'nosniff')
    headers.setdefault('X-Frame-Options', 'DENY')
    headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=(), usb=()')
    headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
    return response


@app.route('/health')
def health():
    """
    Health check endpoint.

    Anyone gets {"status": "ok"}. The deployment details (deploy time, whether
    the board file is readable) are only included for requests that come from
    inside the container itself (the Docker HEALTHCHECK, `docker exec`), not
    for requests relayed by the reverse proxy.
    """
    try:
        if request.remote_addr not in ('127.0.0.1', '::1'):
            return jsonify({'status': 'ok'})

        # Get the modification time of the app file as a proxy for deployment time
        app_file_path = os.path.abspath(__file__)
        file_mtime = os.path.getmtime(app_file_path)
        deployed_at = datetime.fromtimestamp(file_mtime).isoformat()

        # Check if the markdown file is readable (not just that it exists)
        try:
            with open(MARKDOWN_FILE, 'r', encoding='utf-8') as f:
                f.read(1)
            markdown_accessible = True
        except (FileNotFoundError, PermissionError, OSError):
            markdown_accessible = False

        return jsonify({
            'status': 'ok',
            'deployed_at': deployed_at,
            'markdown_accessible': markdown_accessible
        })
    except Exception:
        app.logger.exception("Health check failed")
        return jsonify({'status': 'error'}), 500


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)

