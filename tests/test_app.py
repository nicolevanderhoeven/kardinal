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
from unittest.mock import MagicMock, patch

import requests

# Make the project root importable when running `python -m unittest` from anywhere
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as app_module  # noqa: E402
from app import app as flask_app, find_note_path, parse_kanban_markdown  # noqa: E402


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


class FindNotePathTests(unittest.TestCase):
    """Unit tests for the Obsidian search API lookup."""

    def setUp(self):
        # `find_note_path` is wrapped in `lru_cache`, so clear the cache before
        # each test to ensure the mocked `requests.post` is actually invoked.
        find_note_path.cache_clear()

    def _mock_response(self, status_code: int, json_data):
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = json_data
        return response

    def test_returns_path_when_note_exists(self):
        # Search API returns multiple results; we expect the one whose filename
        # matches exactly (case-insensitive) to win, with the .md stripped.
        api_results = {
            'results': [
                'Changelog.md',
                'system/cards/Some Other Note.md',
                'Spice Runner.md',
            ]
        }
        with patch(
            'app.requests.post',
            return_value=self._mock_response(200, api_results),
        ) as mock_post:
            result = find_note_path('Spice Runner')

        self.assertEqual(result, 'Spice Runner')
        # Sanity check: the call used a non-trivial timeout (>= 1s), so we don't
        # regress to the 0.5s value that caused every lookup to time out.
        _, kwargs = mock_post.call_args
        self.assertGreaterEqual(kwargs.get('timeout', 0), 1)

    def test_returns_path_for_note_in_subdirectory(self):
        api_results = {
            'results': [
                'system/cards/Deep Note.md',
                'Other.md',
            ]
        }
        with patch(
            'app.requests.post',
            return_value=self._mock_response(200, api_results),
        ):
            result = find_note_path('Deep Note')

        self.assertEqual(result, 'system/cards/Deep Note')

    def test_match_is_case_insensitive(self):
        api_results = {'results': ['Spice Runner.md']}
        with patch(
            'app.requests.post',
            return_value=self._mock_response(200, api_results),
        ):
            self.assertEqual(find_note_path('spice runner'), 'Spice Runner')

    def test_returns_none_when_no_match(self):
        api_results = {'results': ['Something Else.md']}
        with patch(
            'app.requests.post',
            return_value=self._mock_response(200, api_results),
        ):
            self.assertIsNone(find_note_path('Nonexistent Note'))

    def test_returns_none_on_timeout(self):
        # This is the regression we're guarding against: when the Obsidian
        # search API takes longer than the configured timeout, `requests.post`
        # raises `Timeout` and we should fall back to "note doesn't exist"
        # rather than crashing.
        with patch(
            'app.requests.post',
            side_effect=requests.exceptions.Timeout('mocked timeout'),
        ):
            self.assertIsNone(find_note_path('Spice Runner'))

    def test_returns_none_on_non_200(self):
        with patch(
            'app.requests.post',
            return_value=self._mock_response(500, {}),
        ):
            self.assertIsNone(find_note_path('Spice Runner'))

    def test_returns_none_on_connection_error(self):
        with patch(
            'app.requests.post',
            side_effect=requests.exceptions.ConnectionError('mocked'),
        ):
            self.assertIsNone(find_note_path('Spice Runner'))


if __name__ == '__main__':
    unittest.main()
