#!/usr/bin/env python3
"""
Kanban Markdown Renderer
Reads an Obsidian Kanban-formatted Markdown file and renders it as a read-only Kanban board.
"""

import os
import re
from flask import Flask, render_template
from markupsafe import Markup
import markdown
import bleach
import requests
from urllib.parse import quote_plus

app = Flask(__name__)

# Path to the Markdown file (configurable via environment variable)
MARKDOWN_FILE = os.getenv('KANBAN_MARKDOWN_FILE', '/srv/kardinal/kardinal_public/Grafana Labs Kanban.md')


def find_note_path(note_name):
    """
    Find the path of a note on notes.nicolevanderhoeven.com using the search API.
    Returns the full path (without .md extension) if the note exists, None otherwise.
    Searches in all directories, not just system/cards/.
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
            timeout=2
        )
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        results = data.get('results', [])
        
        # Check if any result matches the note name exactly (in any directory)
        for result in results:
            # Extract filename without extension
            filename = os.path.splitext(os.path.basename(result))[0]
            if filename == note_name:
                # Return the full path without .md extension
                # This will be used to construct the URL
                return os.path.splitext(result)[0]
        
        return None
    except Exception:
        # If request fails for any reason, assume note doesn't exist
        return None


def render_card_markdown(text):
    """
    Render markdown text to HTML and sanitize it for safe display.
    Allows common markdown features like links, bold, italic, etc.
    Falls back to plain text if rendering fails.
    """
    try:
        # Parse Wikilinks [[text]] and check if they exist on notes site
        def process_wikilink(match):
            note_name = match.group(1)
            note_path = find_note_path(note_name)
            if note_path:
                # URL encode each path segment (spaces become +, special chars like & become %26)
                # Split the path and encode each segment separately
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

    columns = []
    lines = content.split('\n')
    current_column = None
    current_cards = []
    stopped_at_archive = False

    for line in lines:
        # Check for column header (## Header)
        column_match = re.match(r'^##\s+(.+)$', line.strip())
        if column_match:
            # Stop parsing if we encounter Archive column
            column_name = column_match.group(1)
            if column_name.strip().lower() == 'archive':
                # Save previous column if exists before stopping
                if current_column is not None:
                    columns.append({
                        'name': current_column,
                        'cards': current_cards
                    })
                # Stop parsing - ignore Archive and everything after it
                stopped_at_archive = True
                break
            
            # Save previous column if exists
            if current_column is not None:
                columns.append({
                    'name': current_column,
                    'cards': current_cards
                })
            # Start new column
            current_column = column_name
            current_cards = []
        # Check for task item (- [ ] or - [x])
        elif current_column is not None:
            task_match = re.match(r'^-\s+\[([ x])\]\s+(.+)$', line.strip())
            if task_match:
                is_completed = task_match.group(1) == 'x'
                card_text = task_match.group(2)
                # Remove leading list markers and spaces (e.g., "- ", "* ", "+ ", numbered lists)
                card_text = re.sub(r'^[-*+]\s+', '', card_text)  # Remove unordered list markers
                card_text = re.sub(r'^\d+\.\s+', '', card_text)  # Remove ordered list markers
                card_text = card_text.lstrip()  # Remove any remaining leading whitespace
                # Render markdown for the card text
                rendered_html = render_card_markdown(card_text)
                current_cards.append({
                    'text': card_text,
                    'html': rendered_html,
                    'completed': is_completed
                })

    # Don't forget the last column (only if we didn't break early at Archive)
    if not stopped_at_archive and current_column is not None and current_column.strip().lower() != 'archive':
        columns.append({
            'name': current_column,
            'cards': current_cards
        })

    return columns


@app.route('/')
def kanban_board():
    """Render the Kanban board from the Markdown file."""
    columns = parse_kanban_markdown(MARKDOWN_FILE)
    return render_template('kanban.html', columns=columns)


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)

