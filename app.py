#!/usr/bin/env python3
"""
Kanban Markdown Renderer
Reads an Obsidian Kanban-formatted Markdown file and renders it as a read-only Kanban board.
"""

import os
import re
from flask import Flask, render_template

app = Flask(__name__)

# Path to the Markdown file (configurable via environment variable)
MARKDOWN_FILE = os.getenv('KANBAN_MARKDOWN_FILE', '/srv/kardinal/kardinal_public/Grafana Labs Kanban.md')


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
                current_cards.append({
                    'text': card_text,
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

