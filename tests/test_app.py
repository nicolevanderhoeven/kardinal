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
from app import app as flask_app, clear_note_cache, find_note_path, parse_kanban_markdown  # noqa: E402


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

    def test_rendered_html_has_balanced_divs(self):
        fake_columns = [
            {'name': 'Backlog', 'cards': [{'text': 'a', 'html': 'a', 'completed': False}], 'is_bottom': False},
            {'name': 'Shipped', 'cards': [], 'is_bottom': True},
        ]
        with patch.object(app_module, 'parse_kanban_markdown', return_value=fake_columns):
            html = self.client.get('/').data.decode('utf-8')
        self.assertEqual(html.count('<div'), html.count('</div>'))


class FindNotePathTests(unittest.TestCase):
    """Unit tests for the Obsidian search API lookup."""

    def setUp(self):
        # Lookups are cached, so clear the cache before each test to ensure the
        # mocked `requests.post` is actually invoked.
        clear_note_cache()

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

    def test_failures_are_not_cached_and_are_logged(self):
        # A transient outage must not turn a link into italics until restart:
        # the failed lookup is logged, and the next call retries and succeeds.
        ok = self._mock_response(200, {'results': ['Spice Runner.md']})
        with patch(
            'app.requests.post',
            side_effect=[requests.exceptions.Timeout('mocked'), ok],
        ) as mock_post:
            with self.assertLogs(flask_app.logger, level='WARNING') as logs:
                self.assertIsNone(find_note_path('Spice Runner'))
            self.assertIn('Spice Runner', logs.output[0])
            self.assertEqual(find_note_path('Spice Runner'), 'Spice Runner')
        self.assertEqual(mock_post.call_count, 2)

    def test_non_200_is_not_cached(self):
        ok = self._mock_response(200, {'results': ['Spice Runner.md']})
        with patch(
            'app.requests.post',
            side_effect=[self._mock_response(503, {}), ok],
        ):
            with self.assertLogs(flask_app.logger, level='WARNING'):
                self.assertIsNone(find_note_path('Spice Runner'))
            self.assertEqual(find_note_path('Spice Runner'), 'Spice Runner')

    def test_definitive_results_are_cached(self):
        found = self._mock_response(200, {'results': ['Spice Runner.md']})
        missing = self._mock_response(200, {'results': []})
        with patch('app.requests.post', side_effect=[found, missing]) as mock_post:
            self.assertEqual(find_note_path('Spice Runner'), 'Spice Runner')
            self.assertEqual(find_note_path('Spice Runner'), 'Spice Runner')
            self.assertIsNone(find_note_path('Nope'))
            self.assertIsNone(find_note_path('Nope'))
        self.assertEqual(mock_post.call_count, 2)


class HealthTests(unittest.TestCase):
    def setUp(self):
        flask_app.config['TESTING'] = True
        self.client = flask_app.test_client()

    def test_health_does_not_leak_server_path(self):
        with tempfile.NamedTemporaryFile('w', suffix='.md') as f:
            f.write('## A\n')
            f.flush()
            with patch.object(app_module, 'MARKDOWN_FILE', f.name):
                response = self.client.get('/health')
            data = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(data['status'], 'ok')
            self.assertTrue(data['markdown_accessible'])
            self.assertNotIn('markdown_file', data)
            self.assertNotIn(f.name, response.get_data(as_text=True))

    def test_health_reports_unreadable_file(self):
        with patch.object(app_module, 'MARKDOWN_FILE', '/nonexistent/file.md'):
            data = self.client.get('/health').get_json()
        self.assertFalse(data['markdown_accessible'])


class SanitizationTests(unittest.TestCase):
    def test_script_and_js_links_are_stripped(self):
        html = str(app_module.render_card_markdown(
            'hi <script>alert(1)</script> [x](javascript:alert(1)) '
            '<img src=x onerror=alert(1)>', note_cache={}))
        self.assertNotIn('<script', html)
        self.assertNotIn('javascript:', html)
        self.assertNotIn('<img', html)

    def test_external_links_get_noopener(self):
        html = str(app_module.render_card_markdown('[x](https://example.com)', note_cache={}))
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertIn('target="_blank"', html)


if __name__ == '__main__':
    unittest.main()


class OpenTelemetryStartupTests(unittest.TestCase):
    """The app must start and serve requests under opentelemetry-instrument
    with no OTLP endpoint configured (the default for local and CI runs)."""

    def test_app_starts_under_opentelemetry_instrument_without_endpoint(self):
        import shutil
        import socket
        import subprocess
        import time
        import urllib.request

        root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        bindir = os.path.dirname(sys.executable)
        otel = shutil.which('opentelemetry-instrument', path=bindir)
        gunicorn = shutil.which('gunicorn', path=bindir)
        if not (otel and gunicorn):
            self.skipTest('opentelemetry-instrument / gunicorn not installed')

        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]

        env = {k: v for k, v in os.environ.items() if not k.startswith('OTEL_')}
        env.update({
            'OTEL_SERVICE_NAME': 'kardinal',
            'OTEL_TRACES_EXPORTER': 'none',
            'OTEL_METRICS_EXPORTER': 'none',
            'OTEL_LOGS_EXPORTER': 'none',
            'KANBAN_MARKDOWN_FILE': os.path.join(root, 'does-not-exist.md'),
        })
        proc = subprocess.Popen(
            [otel, gunicorn, '-w', '1', '-b', f'127.0.0.1:{port}', 'app:app'],
            cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            status = None
            deadline = time.time() + 30
            while time.time() < deadline and proc.poll() is None:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2) as r:
                        status = r.status
                        break
                except Exception:
                    time.sleep(0.3)
            self.assertIsNone(proc.poll(), 'server exited during startup')
            self.assertIsNotNone(status, 'server never answered')
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
