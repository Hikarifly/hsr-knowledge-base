"""Serve short-lived, read-only review snapshots from a trusted environment.

Run behind an HTTPS reverse proxy on the SAME origin as the preview site. Route
only /previews/<PR>/review.json to this service; serve all other paths as static
files. Bind loopback by default and apply proxy rate limits. This service never
executes PR code or reads preview content. It requires a read-only gh credential
that can inspect reviewer permissions; do not supply publishing credentials.
"""
import argparse
import json
import re
import subprocess
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from tools.preview_review import REPO_PATTERN, snapshot

PATH = re.compile(r'^/previews/([1-9][0-9]{0,7})/review\.json$')
CACHE_SECONDS = 10
MAX_CACHE = 1000


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, repo, reader=snapshot):
        if not isinstance(repo, str) or not REPO_PATTERN.fullmatch(repo):
            raise ValueError('Invalid repository')
        self.repo, self.reader = repo, reader
        self.cache = OrderedDict()
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, ReviewHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def get_review(self, number):
        # A single in-flight GitHub request bounds load; busy clients fail closed.
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('Review lookup busy')
        try:
            cached = self.cache.get(number)
            now = time.time()
            if cached and 0 <= now - cached['captured_at'] < CACHE_SECONDS:
                self.cache.move_to_end(number)
                return cached
            self.cache.pop(number, None)
            result = self.reader(self.repo, number)
            if (not isinstance(result, dict) or result.get('pr') != number
                    or not isinstance(result.get('head'), str)
                    or not re.fullmatch(r'[0-9a-f]{40}', result['head'])
                    or type(result.get('approved')) is not bool
                    or type(result.get('captured_at')) is not int
                    or not 0 <= time.time() - result['captured_at'] < CACHE_SECONDS
                    or not isinstance(result.get('reviews'), list) or len(result['reviews']) > 1000):
                raise ValueError('Invalid review snapshot')
            value = {key: result[key] for key in ('pr', 'head', 'approved', 'reviews', 'captured_at')}
            value['repo'] = self.repo
            self.cache[number] = value
            while len(self.cache) > MAX_CACHE:
                self.cache.popitem(last=False)
            return value
        finally:
            self.lock.release()


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = 'PreviewReview'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args):
        pass

    def reply(self, status, value):
        payload = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('ascii')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store, max-age=0')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parts = urlsplit(self.path)
        match = PATH.fullmatch(parts.path)
        if not match or parts.scheme or parts.netloc or parts.query or parts.fragment:
            self.reply(404, {'error': 'Not found'})
            return
        try:
            result = self.server.get_review(int(match.group(1)))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
            self.reply(503, {'error': 'Review status unavailable'})
            return
        self.reply(200, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Invalid port')
    server = ReviewServer(('127.0.0.1', args.port), args.repo)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
