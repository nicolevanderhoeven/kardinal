"""
Tests for the Kanban renderer.

Focus: ensure the "bottom column" behaviour is position-based (the last column
in the file becomes the collapsed bottom row) so column titles can be renamed
without code changes.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Make the project root importable when running `python -m unittest` from anywhere
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as app_module  # noqa: E402
from app import app as flask_app, parse_kanban_markdown  # noqa: E402


class ParseKanbanMarkdownTests(unittest.TestCase):
    """Unit tests for the markdown parser."""

    def setUp(self):
        # Avoid network calls to the Obsidian search API during tests.
        self._patcher = patch('app.find_note_path', return_value=None)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _write_md(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix='.md')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        self.addCleanup(os.unlink, path)
        return path

    def test_last_column_is_marked_bottom(self):
        path = self._write_md(
            "## Backlog\n- [ ] A\n\n"
            "## In Progress\n- [ ] B\n\n"
            "## \u2705 Shipped\n- [x] C\n"
        )
        columns = parse_kanban_markdown(path)
        self.assertEqual([c['name'] for c in columns], ['Backlog', 'In Progress', '\u2705 Shipped'])
        self.assertEqual([c['is_bottom'] for c in columns], [False, False, True])

    def test_renaming_columns_does_not_break_layout(self):
        # Arbitrary names: layout should still place the last one at the bottom.
        path = self._write_md(
            "## Foo\n- [ ] A\n\n"
            "## Bar\n- [ ] B\n\n"
            "## Baz\n- [x] C\n"
        )
        columns = parse_kanban_markdown(path)
        self.assertEqual([c['name'] for c in columns], ['Foo', 'Bar', 'Baz'])
        self.assertEqual([c['is_bottom'] for c in columns], [False, False, True])

    def test_single_column_is_not_bottom(self):
        path = self._write_md("## Backlog\n- [ ] A\n")
        columns = parse_kanban_markdown(path)
        self.assertEqual(len(columns), 1)
        self.assertFalse(columns[0]['is_bottom'])

    def test_empty_file_returns_no_columns(self):
        path = self._write_md("Just some text, no headers.\n")
        self.assertEqual(parse_kanban_markdown(path), [])

    def test_archive_section_is_excluded(self):
        # Archive (and anything below it) is dropped; the column right before
        # Archive becomes the new "last" column and should be the bottom one.
        path = self._write_md(
            "## Backlog\n- [ ] A\n\n"
            "## \u2705 Shipped\n- [x] B\n\n"
            "## Archive\n- [x] Old stuff\n"
        )
        columns = parse_kanban_markdown(path)
        self.assertEqual([c['name'] for c in columns], ['Backlog', '\u2705 Shipped'])
        self.assertEqual([c['is_bottom'] for c in columns], [False, True])

    def test_missing_file_returns_empty(self):
        self.assertEqual(parse_kanban_markdown('/nonexistent/path/to/file.md'), [])


class KanbanRouteRenderingTests(unittest.TestCase):
    """Template-level tests via the Flask test client."""

    def setUp(self):
        flask_app.config['TESTING'] = True
        self.client = flask_app.test_client()

    def test_bottom_column_rendered_after_top_row(self):
        fake_columns = [
            {'name': 'Backlog', 'cards': [], 'is_bottom': False},
            {'name': 'In Progress', 'cards': [], 'is_bottom': False},
            {'name': '\u2705 Shipped', 'cards': [], 'is_bottom': True},
        ]
        with patch.object(app_module, 'parse_kanban_markdown', return_value=fake_columns):
            response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        html = response.data.decode('utf-8')

        # Bottom column is rendered as the collapsible done-column block.
        self.assertIn('done-column collapsed', html)
        self.assertIn('\u2705 Shipped', html)

        # Sanity check: the bottom column appears after the top columns in the markup.
        backlog_idx = html.index('Backlog')
        in_progress_idx = html.index('In Progress')
        shipped_idx = html.index('\u2705 Shipped')
        self.assertLess(backlog_idx, shipped_idx)
        self.assertLess(in_progress_idx, shipped_idx)

    def test_top_columns_do_not_get_done_class(self):
        fake_columns = [
            {'name': 'Backlog', 'cards': [], 'is_bottom': False},
            {'name': 'Released', 'cards': [], 'is_bottom': True},
        ]
        with patch.object(app_module, 'parse_kanban_markdown', return_value=fake_columns):
            response = self.client.get('/')

        html = response.data.decode('utf-8')
        # Backlog must not be wrapped in the collapsible done-column container.
        backlog_idx = html.index('Backlog')
        released_idx = html.index('Released')
        self.assertLess(backlog_idx, released_idx)
        # The done-column container must be rendered exactly once
        # (the JS function name "toggleDoneColumn" also contains the substring
        # "done-column" indirectly, so match the actual class attribute instead).
        self.assertEqual(html.count('kanban-column done-column'), 1)


if __name__ == '__main__':
    unittest.main()
