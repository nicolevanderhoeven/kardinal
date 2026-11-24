"""
Tests for the lookup and page caches: hit, expiry, failure caching, stale
serving, the lookup deadline, the concurrency cap and single-flight rendering.
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as app_module  # noqa: E402
from app import (  # noqa: E402
    app as flask_app,
    clear_board_cache,
    clear_note_cache,
    find_note_path,
    get_board,
    resolve_wikilinks,
)


class FakeClock:
    """Replaces app._now so tests can move time without sleeping."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _response(status, data):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = data
    return r


class NoteCacheTests(unittest.TestCase):
    def setUp(self):
        clear_note_cache()
        self.clock = FakeClock()
        patcher = patch.object(app_module, '_now', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(clear_note_cache)

    def test_hit_does_not_call_out_again(self):
        with patch('app.requests.post', return_value=_response(200, {'results': ['A.md']})) as post:
            self.assertEqual(find_note_path('A'), 'A')
            self.assertEqual(find_note_path('A'), 'A')
        self.assertEqual(post.call_count, 1)

    def test_definitive_entry_expires_after_an_hour(self):
        with patch('app.requests.post', return_value=_response(200, {'results': ['A.md']})) as post:
            find_note_path('A')
            self.clock.advance(app_module.NOTE_CACHE_TTL_SECONDS - 1)
            find_note_path('A')
            self.assertEqual(post.call_count, 1)
            self.clock.advance(2)
            find_note_path('A')
        self.assertEqual(post.call_count, 2)

    def test_failure_is_cached_briefly_then_retried(self):
        with patch('app.requests.post', side_effect=requests.exceptions.Timeout('slow')) as post:
            with self.assertLogs(flask_app.logger, level='WARNING'):
                self.assertIsNone(find_note_path('A'))
            # A burst of requests inside the failure TTL costs no more lookups.
            for _ in range(5):
                self.assertIsNone(find_note_path('A'))
            self.assertEqual(post.call_count, 1)
            self.clock.advance(app_module.NOTE_FAILURE_TTL_SECONDS + 1)
            with self.assertLogs(flask_app.logger, level='WARNING'):
                self.assertIsNone(find_note_path('A'))
        self.assertEqual(post.call_count, 2)

    def test_failure_ttl_is_much_shorter_than_success_ttl(self):
        self.assertLessEqual(app_module.NOTE_FAILURE_TTL_SECONDS, 120)
        self.assertLess(app_module.NOTE_FAILURE_TTL_SECONDS, app_module.NOTE_CACHE_TTL_SECONDS)

    def test_stale_definitive_answer_survives_a_failed_refresh(self):
        ok = _response(200, {'results': ['dir/A.md']})
        with patch('app.requests.post', side_effect=[ok, requests.exceptions.Timeout('slow')]):
            self.assertEqual(find_note_path('A'), 'dir/A')
            self.clock.advance(app_module.NOTE_CACHE_TTL_SECONDS + 1)
            with self.assertLogs(flask_app.logger, level='WARNING'):
                # The refresh fails, but the link must not turn into italics.
                self.assertEqual(find_note_path('A'), 'dir/A')
            self.assertEqual(find_note_path('A'), 'dir/A')

    def test_cache_is_bounded(self):
        with patch('app.requests.post', return_value=_response(200, {'results': []})):
            for i in range(app_module.NOTE_CACHE_MAX + 50):
                find_note_path(f'note {i}')
        self.assertLessEqual(len(app_module._note_cache), app_module.NOTE_CACHE_MAX)


class ResolveWikilinksTests(unittest.TestCase):
    def setUp(self):
        clear_note_cache()
        self.addCleanup(clear_note_cache)

    def test_concurrency_never_exceeds_the_cap(self):
        lock = threading.Lock()
        state = {'now': 0, 'max': 0}

        def fake_post(*args, **kwargs):
            with lock:
                state['now'] += 1
                state['max'] = max(state['max'], state['now'])
            time.sleep(0.02)
            with lock:
                state['now'] -= 1
            return _response(200, {'results': []})

        names = [f'note {i}' for i in range(40)]
        with patch('app.requests.post', side_effect=fake_post):
            result = resolve_wikilinks(names, budget=30)
        self.assertEqual(set(result), set(names))
        self.assertGreater(state['max'], 1)  # still parallel
        self.assertLessEqual(state['max'], app_module.MAX_CONCURRENT_LOOKUPS)

    def test_deadline_bounds_total_time_and_degrades_to_none(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def hanging_post(*args, **kwargs):
            release.wait(5)
            return _response(200, {'results': ['x.md']})

        names = [f'note {i}' for i in range(30)]
        started = time.monotonic()
        with patch('app.requests.post', side_effect=hanging_post):
            with self.assertLogs(flask_app.logger, level='WARNING') as logs:
                result = resolve_wikilinks(names, budget=0.3)
            elapsed = time.monotonic() - started
            release.set()
        self.assertLess(elapsed, 2)
        self.assertEqual(set(result), set(names))
        self.assertTrue(all(v is None for v in result.values()))
        self.assertTrue(any('budget' in line for line in logs.output))

    def test_late_answers_still_reach_the_note_cache(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def slow_post(*args, **kwargs):
            release.wait(5)
            return _response(200, {'results': ['A.md']})

        with patch('app.requests.post', side_effect=slow_post):
            with self.assertLogs(flask_app.logger, level='WARNING'):
                self.assertEqual(resolve_wikilinks(['A'], budget=0.1), {'A': None})
            release.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and 'A' not in app_module._note_cache:
                time.sleep(0.01)
        # The in-flight lookup finished after the deadline; the next render
        # finds it in the cache without calling out again.
        with patch('app.requests.post', side_effect=AssertionError('should be cached')):
            self.assertEqual(find_note_path('A'), 'A')


BOARD = "## Todo\n- [ ] see [[A]] and [[B]]\n\n## Done\n- [x] shipped\n"


class BoardCacheTests(unittest.TestCase):
    def setUp(self):
        clear_board_cache()
        clear_note_cache()
        self.clock = FakeClock()
        patcher = patch.object(app_module, '_now', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(clear_board_cache)
        self.addCleanup(clear_note_cache)
        fd, self.path = tempfile.mkstemp(suffix='.md')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(BOARD)
        self.addCleanup(os.unlink, self.path)
        self.parse = patch.object(
            app_module, 'parse_kanban_markdown', wraps=app_module.parse_kanban_markdown
        )
        self.parse_mock = self.parse.start()
        self.addCleanup(self.parse.stop)
        lookup = patch('app.requests.post', return_value=_response(200, {'results': []}))
        self.post = lookup.start()
        self.addCleanup(lookup.stop)

    def _wait_for_background_refresh(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with app_module._board_lock:
                if not app_module._refreshing:
                    return
            time.sleep(0.01)
        self.fail('background refresh did not finish')

    def _rewrite(self, text):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(text)
        st = os.stat(self.path)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    def test_hit_serves_the_same_render_without_lookups(self):
        first = get_board(self.path)
        lookups = self.post.call_count
        self.assertEqual(lookups, 2)  # [[A]] and [[B]], once
        for _ in range(10):
            self.assertIs(get_board(self.path), first)
        self.assertEqual(self.post.call_count, lookups)
        self.assertEqual(self.parse_mock.call_count, 1)

    def test_one_request_costs_at_most_one_lookup_per_unique_name(self):
        get_board(self.path)
        self.assertEqual(self.post.call_count, 2)

    def test_expiry_serves_stale_and_refreshes_in_the_background(self):
        first = get_board(self.path)
        self.clock.advance(app_module.PAGE_CACHE_TTL_SECONDS + 1)
        # The reader still gets the old copy immediately...
        self.assertIs(get_board(self.path), first)
        self._wait_for_background_refresh()
        # ...and the refresh has replaced it for the next reader.
        self.assertEqual(self.parse_mock.call_count, 2)
        second = get_board(self.path)
        self.assertIsNot(second, first)
        self.assertEqual(self.parse_mock.call_count, 2)

    def test_changed_file_is_picked_up_without_waiting_for_the_ttl(self):
        first = get_board(self.path)
        self._rewrite("## Todo\n- [ ] new card\n\n## Done\n- [x] x\n")
        self.assertIs(get_board(self.path), first)  # stale once, refresh kicked off
        self._wait_for_background_refresh()
        cards = get_board(self.path)[0]['cards']
        self.assertEqual(cards[0]['text'], 'new card')

    def test_only_one_background_refresh_runs_at_a_time(self):
        get_board(self.path)
        self.clock.advance(app_module.PAGE_CACHE_TTL_SECONDS + 1)
        release = threading.Event()
        self.addCleanup(release.set)
        calls = []

        def slow_parse(path):
            calls.append(path)
            release.wait(3)
            return [{'name': 'X', 'cards': [], 'is_bottom': False}]

        self.parse_mock.side_effect = slow_parse
        for _ in range(20):
            get_board(self.path)
        time.sleep(0.1)
        self.assertEqual(len(calls), 1)
        release.set()
        self._wait_for_background_refresh()

    def test_failed_refresh_keeps_serving_the_stale_copy(self):
        first = get_board(self.path)
        self.clock.advance(app_module.PAGE_CACHE_TTL_SECONDS + 1)
        self.parse_mock.side_effect = RuntimeError('boom')
        with self.assertLogs(flask_app.logger, level='ERROR'):
            self.assertIs(get_board(self.path), first)
            self._wait_for_background_refresh()
        calls = self.parse_mock.call_count
        # Not retried on every request: wait PAGE_RETRY_SECONDS first.
        for _ in range(5):
            self.assertIs(get_board(self.path), first)
        self._wait_for_background_refresh()
        self.assertEqual(self.parse_mock.call_count, calls)
        # Once Obsidian / the file recovers, the next retry replaces the copy.
        self.parse_mock.side_effect = None
        self.clock.advance(app_module.PAGE_RETRY_SECONDS + 1)
        get_board(self.path)
        self._wait_for_background_refresh()
        self.assertIsNot(get_board(self.path), first)

    def test_unreadable_file_keeps_the_stale_copy(self):
        first = get_board(self.path)
        os.unlink(self.path)
        self.addCleanup(lambda: open(self.path, 'w').close())
        self.clock.advance(app_module.PAGE_CACHE_TTL_SECONDS + 1)
        self.assertIs(get_board(self.path), first)
        self._wait_for_background_refresh()
        self.assertIs(get_board(self.path), first)

    def test_stale_copy_is_not_served_forever(self):
        get_board(self.path)
        self.clock.advance(app_module.PAGE_MAX_STALE_SECONDS + 1)
        self.parse_mock.side_effect = None
        fresh = get_board(self.path)  # too old to serve: renders synchronously
        self.assertEqual(self.parse_mock.call_count, 2)
        self.assertEqual(fresh[0]['name'], 'Todo')

    def test_failed_lookups_do_not_make_every_render_slow(self):
        # Obsidian is down: the first render pays for the lookups once, then
        # repeated renders (even after the page cache expires) reuse the
        # failure entries instead of calling out again.
        self.post.side_effect = requests.exceptions.Timeout('slow')
        with self.assertLogs(flask_app.logger, level='WARNING'):
            get_board(self.path)
        calls = self.post.call_count
        self.clock.advance(app_module.PAGE_CACHE_TTL_SECONDS - 1)
        get_board(self.path)
        self.assertEqual(self.post.call_count, calls)

    def test_concurrent_first_requests_share_one_render(self):
        calls = []
        gate = threading.Event()

        def slow_parse(path):
            calls.append(path)
            gate.wait(2)
            return [{'name': 'X', 'cards': [], 'is_bottom': False}]

        self.parse_mock.side_effect = slow_parse
        results = []
        threads = [threading.Thread(target=lambda: results.append(get_board(self.path))) for _ in range(6)]
        for t in threads:
            t.start()
        time.sleep(0.2)
        gate.set()
        for t in threads:
            t.join(5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 6)

    def test_empty_first_render_is_not_cached(self):
        self.parse_mock.side_effect = None
        with patch.object(app_module, 'parse_kanban_markdown', return_value=[]) as parse:
            get_board(self.path)
            get_board(self.path)
        self.assertEqual(parse.call_count, 2)

    def test_ttl_zero_disables_the_page_cache(self):
        with patch.object(app_module, 'PAGE_CACHE_TTL_SECONDS', 0):
            get_board(self.path)
            get_board(self.path)
        self.assertEqual(self.parse_mock.call_count, 2)

    def test_route_serves_from_the_cache(self):
        client = flask_app.test_client()
        with patch.object(app_module, 'MARKDOWN_FILE', self.path):
            for _ in range(5):
                self.assertEqual(client.get('/').status_code, 200)
        self.assertEqual(self.parse_mock.call_count, 1)


if __name__ == '__main__':
    unittest.main()
