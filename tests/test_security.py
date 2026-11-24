"""Security headers, the CSP-compatible page, and the health endpoint."""

import os
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as app_module  # noqa: E402
from app import app as flask_app, clear_board_cache  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

FAKE_COLUMNS = [
    {'name': 'Backlog', 'cards': [{'text': 'a', 'html': 'a', 'completed': False}], 'is_bottom': False},
    {'name': 'Shipped', 'cards': [], 'is_bottom': True},
]


class SecurityHeaderTests(unittest.TestCase):
    EXPECTED = (
        'Content-Security-Policy',
        'Strict-Transport-Security',
        'X-Content-Type-Options',
        'X-Frame-Options',
        'Referrer-Policy',
        'Permissions-Policy',
        'Cross-Origin-Opener-Policy',
    )

    def setUp(self):
        flask_app.config['TESTING'] = True
        self.client = flask_app.test_client()
        clear_board_cache()
        self.addCleanup(clear_board_cache)

    def _board(self):
        with patch.object(app_module, 'parse_kanban_markdown', return_value=FAKE_COLUMNS):
            return self.client.get('/')

    def test_page_has_all_security_headers(self):
        response = self._board()
        for name in self.EXPECTED:
            self.assertIn(name, response.headers, name)
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        self.assertRegex(response.headers['Strict-Transport-Security'], r'^max-age=\d+$')

    def test_headers_also_on_errors_static_files_and_health(self):
        for path in ('/does-not-exist', '/static/style.css', '/static/app.js', '/health'):
            with self.client.get(path) as response:
                for name in self.EXPECTED:
                    self.assertIn(name, response.headers, f'{name} missing on {path}')

    def test_csp_is_strict(self):
        csp = self._board().headers['Content-Security-Policy']
        self.assertIn("default-src 'none'", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn('unsafe-inline', csp)
        self.assertNotIn('unsafe-eval', csp)
        self.assertNotIn('unsafe-hashes', csp)

    def test_page_works_under_the_csp(self):
        # Nothing inline: no <script> bodies, no event-handler attributes, no
        # style attributes; the one script is a same-origin file.
        html = self._board().get_data(as_text=True)
        scripts = re.findall(r'<script\b([^>]*)>(.*?)</script>', html, flags=re.S | re.I)
        self.assertEqual(len(scripts), 1)
        attrs, body = scripts[0]
        self.assertIn('src="/static/app.js"', attrs)
        self.assertEqual(body.strip(), '')
        self.assertIsNone(re.search(r'\son[a-z]+\s*=', html, flags=re.I))
        self.assertIsNone(re.search(r'\sstyle\s*=', html, flags=re.I))
        self.assertNotIn('<style', html.lower())

    def test_static_script_is_served(self):
        with self.client.get('/static/app.js') as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn('javascript', response.content_type)

    def test_stylesheet_loads_nothing_external(self):
        with open(os.path.join(ROOT, 'static', 'style.css'), encoding='utf-8') as f:
            css = f.read()
        self.assertNotRegex(css, r'url\(\s*[\'"]?(https?:)?//')
        self.assertNotIn('@import', css)

    def test_done_row_toggle_script_targets_existing_markup(self):
        with open(os.path.join(ROOT, 'static', 'app.js'), encoding='utf-8') as f:
            js = f.read()
        html = self._board().get_data(as_text=True)
        for selector in ('.done-header', '.done-column', '.toggle-icon'):
            self.assertIn(selector, js)
            self.assertIn(selector[1:], html)


class HealthEndpointTests(unittest.TestCase):
    def setUp(self):
        flask_app.config['TESTING'] = True
        self.client = flask_app.test_client()

    def test_public_health_discloses_only_status(self):
        response = self.client.get('/health', environ_base={'REMOTE_ADDR': '203.0.113.7'})
        self.assertEqual(response.get_json(), {'status': 'ok'})

    def test_forwarded_for_header_cannot_unlock_details(self):
        response = self.client.get(
            '/health',
            environ_base={'REMOTE_ADDR': '172.19.0.2'},
            headers={'X-Forwarded-For': '127.0.0.1'},
        )
        self.assertEqual(response.get_json(), {'status': 'ok'})

    def test_loopback_gets_details_for_the_container_healthcheck(self):
        data = self.client.get('/health', environ_base={'REMOTE_ADDR': '127.0.0.1'}).get_json()
        self.assertEqual(data['status'], 'ok')
        self.assertIn('deployed_at', data)
        self.assertIn('markdown_accessible', data)


if __name__ == '__main__':
    unittest.main()
