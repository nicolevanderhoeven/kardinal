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

app = Flask(__name__)

# Path to the Markdown file (configurable via environment variable)
MARKDOWN_FILE = os.getenv('KANBAN_MARKDOWN_FILE', '/srv/kardinal/kardinal_public/Grafana Labs Kanban.md')


def render_card_markdown(text):
    """
    Render markdown text to HTML and sanitize it for safe display.
    Allows common markdown features like links, bold, italic, etc.
    Falls back to plain text if rendering fails.
    """
    try:
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

    for line in lines:
        # Check for column header (## Header)
        column_match = re.match(r'^##\s+(.+)$', line.strip())
        if column_match:
            # Save previous column if exists
            if current_column is not None:
                columns.append({
                    'name': current_column,
                    'cards': current_cards
                })
            # Start new column
            current_column = column_match.group(1)
            current_cards = []
        # Check for task item (- [ ] or - [x])
        elif current_column is not None:
            task_match = re.match(r'^-\s+\[([ x])\]\s+(.+)$', line.strip())
            if task_match:
                is_completed = task_match.group(1) == 'x'
                card_text = task_match.group(2)
                # Render markdown for the card text
                rendered_html = render_card_markdown(card_text)
                current_cards.append({
                    'text': card_text,
                    'html': rendered_html,
                    'completed': is_completed
                })

    # Don't forget the last column
    if current_column is not None:
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

