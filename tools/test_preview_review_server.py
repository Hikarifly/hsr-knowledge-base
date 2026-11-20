"""Exercise the review service over HTTP without GitHub credentials."""
import json
import subprocess
import threading
import time
import unittest
import urllib.error
import urllib.request

from tools.preview_review_server import ReviewServer

HEAD = 'a' * 40


class ReviewServerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.state = 'approved'
        def reader(repo, number):
            self.calls.append((repo, number))
            if self.state == 'failure':
                raise subprocess.CalledProcessError(1, 'gh')
            return {'pr': number, 'head': HEAD, 'approved': self.state == 'approved',
                    'reviews': [], 'captured_at': int(time.time())}
        self.server = ReviewServer(('127.0.0.1', 0), 'owner/repo', reader=reader)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                return response.status, json.loads(response.read()), response.headers
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.loads(response.read()), response.headers

    def test_valid_request_is_read_only_bounded_and_not_browser_cached(self):
        status, body, headers = self.get('/previews/37/review.json')
        self.assertEqual(status, 200)
        self.assertTrue(body['approved'])
        self.assertEqual((body['repo'], body['pr'], body['head']), ('owner/repo', 37, HEAD))
        self.assertEqual(headers['Cache-Control'], 'no-store, max-age=0')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.get('/previews/37/review.json')
        self.assertEqual(len(self.calls), 1)
        self.server.cache[37]['captured_at'] -= 11
        self.state = 'revoked'
        self.assertFalse(self.get('/previews/37/review.json')[1]['approved'])
        self.assertEqual(len(self.calls), 2)

    def test_failure_and_invalid_paths_never_show_approval(self):
        self.state = 'failure'
        status, body, _ = self.get('/previews/37/review.json')
        self.assertEqual((status, body), (503, {'error': 'Review status unavailable'}))
        for path in ['/previews/0/review.json', '/previews/1/review.json?repo=elsewhere', '/previews/../review.json']:
            self.assertEqual(self.get(path)[0], 404)
        self.assertEqual(len(self.calls), 1)
        self.state = 'revoked'
        self.assertFalse(self.get('/previews/37/review.json')[1]['approved'])

    def test_invalid_snapshot_and_busy_service_fail_closed(self):
        self.server.reader = lambda repo, number: {'pr': number, 'approved': True, 'head': 'wrong'}
        self.assertEqual(self.get('/previews/37/review.json')[0], 503)
        self.server.lock.acquire()
        try:
            self.assertEqual(self.get('/previews/37/review.json')[0], 503)
        finally:
            self.server.lock.release()


if __name__ == '__main__': unittest.main()
