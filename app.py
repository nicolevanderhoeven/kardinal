#!/usr/bin/env python3
"""
Kanban Markdown Renderer
Reads an Obsidian Kanban-formatted Markdown file and renders it as a read-only Kanban board.
"""

import os
import re
from flask import Flask, render_template, jsonify
from markupsafe import Markup
import markdown
import bleach
import requests
from urllib.parse import quote_plus
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from datetime import datetime

app = Flask(__name__)

# Path to the Markdown file (configurable via environment variable)
MARKDOWN_FILE = os.getenv('KANBAN_MARKDOWN_FILE', '/srv/kardinal/kardinal_public/Grafana Labs Kanban.md')

# Thread pool for parallel note lookups
note_lookup_executor = ThreadPoolExecutor(max_workers=10)


@lru_cache(maxsize=1000)
def find_note_path(note_name):
    """
    Find the path of a note on notes.nicolevanderhoeven.com using the search API.
    Returns the full path (without .md extension) if the note exists, None otherwise.
    Searches in all directories, not just system/cards/.
    
    Results are cached to avoid duplicate API calls for the same note.
    """
    try:
        import os
        
        # Use the Obsidian search API to find the note
        # The search API returns file paths, and we check if any result
        # matches the note name exactly (in any directory)
        payload = {
            'id': '186a0d1b800fa85e50d49cb464898e4c',
            'query': [note_name]
        }
        
        response = requests.post(
            'https://publish-01.obsidian.md/search',
            json=payload,
            timeout=0.5  # Reduced timeout to 0.5 seconds for faster failure
        )
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        results = data.get('results', [])
        
        # Check if any result matches the note name exactly (in any directory)
        # Use case-insensitive comparison to handle variations in casing
        for result in results:
            # Extract filename without extension
            filename = os.path.splitext(os.path.basename(result))[0]
            if filename.lower() == note_name.lower():
                # Return the full path without .md extension
                # This will be used to construct the URL
                return os.path.splitext(result)[0]
        
        return None
    except Exception:
        # If request fails for any reason, assume note doesn't exist
        return None


def render_card_markdown(text, note_cache=None):
    """
    Render markdown text to HTML and sanitize it for safe display.
    Allows common markdown features like links, bold, italic, etc.
    Falls back to plain text if rendering fails.
    
    Args:
        text: The markdown text to render
        note_cache: Optional dict to cache note lookups (for batch processing)
    """
    try:
        # Find all wikilinks first
        wikilinks = re.findall(r'\[\[([^\]]+)\]\]', text)
        
        # If we have a cache, use it; otherwise create a local one
        if note_cache is None:
            note_cache = {}
        
        # Batch lookup all unique wikilinks in parallel
        unique_notes = list(set(wikilinks))
        note_paths = {}
        
        # Submit all lookups to thread pool
        future_to_note = {
            note_lookup_executor.submit(find_note_path, note): note 
            for note in unique_notes if note not in note_cache
        }
        
        # Wait for all lookups to complete
        for future in as_completed(future_to_note):
            note_name = future_to_note[future]
            try:
                note_path = future.result()
                note_paths[note_name] = note_path
                if note_cache is not None:
                    note_cache[note_name] = note_path
            except Exception:
                note_paths[note_name] = None
        
        # Merge with cache
        note_paths.update({note: note_cache[note] for note in unique_notes if note in note_cache})
        
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
        text = re.sub(r'\[\[([^\]]+)\]\]', process_wikilink, text)
        
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
        from html import escape
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
    stopped_at_archive = False
    
    for line in lines:
        # Check for column header (## Header)
        column_match = re.match(r'^##\s+(.+)$', line.strip())
        if column_match:
            # Stop parsing if we encounter Archive column
            column_name = column_match.group(1)
            if column_name.strip().lower() == 'archive':
                stopped_at_archive = True
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
            wikilinks = re.findall(r'\[\[([^\]]+)\]\]', card['text'])
            all_wikilinks.update(wikilinks)
    
    # Resolve all unique wikilinks in parallel (this is the key optimization)
    note_cache = {}
    if all_wikilinks:
        unique_notes = list(all_wikilinks)
        future_to_note = {
            note_lookup_executor.submit(find_note_path, note): note 
            for note in unique_notes
        }
        
        # Wait for all lookups to complete
        for future in as_completed(future_to_note):
            note_name = future_to_note[future]
            try:
                note_path = future.result()
                note_cache[note_name] = note_path
            except Exception:
                note_cache[note_name] = None
    
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
            'cards': rendered_cards
        })

    return columns


@app.route('/')
def kanban_board():
    """Render the Kanban board from the Markdown file."""
    columns = parse_kanban_markdown(MARKDOWN_FILE)
    return render_template('kanban.html', columns=columns)


@app.route('/health')
def health():
    """
    Health check endpoint that returns deployment status and version info.
    Useful for verifying that new deployments have been applied.
    """
    try:
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
            'markdown_file': MARKDOWN_FILE,
            'markdown_accessible': markdown_accessible,
            'version': '1.0.0'  # Update this when you make significant changes
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)

