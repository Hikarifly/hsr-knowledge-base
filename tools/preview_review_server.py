"""Serve short-lived, read-only review snapshots from a trusted environment.

Run behind an HTTPS reverse proxy and apply proxy rate limits. With --site, serve
the trusted shell, inert JSON bundles and live review status from one origin.
Bind loopback by default. The service never executes PR code. It requires a
read-only gh credential to inspect reviewer permissions, not publishing rights.
"""
import argparse
import json
import re
import subprocess
import threading
import time
from pathlib import Path
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from tools.preview_review import REPO_PATTERN, snapshot
from tools.preview_catalog import validated_manifest
from tools.verify_preview_publication import verify_preview
from tools.preview_release_state import active_site

PATH = re.compile(r'^/previews/([1-9][0-9]{0,7})/review\.json$')
OBJECT = re.compile(r'^/previews/([1-9][0-9]{0,7})/objects/([0-9a-f]{64})\.json$')
MANIFEST = re.compile(r'^/previews/([1-9][0-9]{0,7})/manifest\.json$')
CACHE_SECONDS = 10
MAX_CACHE = 1000


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, repo, reader=snapshot, site=None, validator=verify_preview, site_root=None):
        if not isinstance(repo, str) or not REPO_PATTERN.fullmatch(repo):
            raise ValueError('Invalid repository')
        self.repo, self.reader = repo, reader
        self.validator = validator
        if site is not None and site_root is not None:
            raise ValueError('Choose one preview site mode')
        self.site = Path(site) if site is not None else None
        self.site_root = Path(site_root) if site_root is not None else None
        if self.site_root is not None:
            if self.site_root.is_symlink() or not self.site_root.is_dir():
                raise ValueError('Invalid release root')
            self.site_root = self.site_root.resolve(strict=True)
        if self.site is not None:
            if self.site.is_symlink() or not self.site.is_dir():
                raise ValueError('Invalid preview site')
            self.site = self.site.resolve(strict=True)
        self.cache = OrderedDict()
        self.cache_site = None
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(16)
        self.static_slots = threading.BoundedSemaphore(4)
        super().__init__(address, ReviewHandler)

    def active_site(self):
        return active_site(self.site_root) if self.site_root is not None else self.site

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
            site = self.active_site()
            if site != self.cache_site:
                self.cache.clear()
                self.cache_site = site
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
                    or not 0 <= time.time() - result['captured_at'] < 25
                    or not isinstance(result.get('reviews'), list) or len(result['reviews']) > 1000):
                raise ValueError('Invalid review snapshot')
            value = {key: result[key] for key in ('pr', 'head', 'approved', 'reviews', 'captured_at')}
            value['repo'] = self.repo
            if site is not None:
                bundle = site / 'previews' / str(number)
                if (bundle.is_symlink() or not bundle.is_dir()
                        or (site / 'previews').is_symlink()):
                    raise ValueError('Preview is not available')
                manifest = validated_manifest(bundle, self.repo, number)
                valid = bool(self.validator(self.repo, number, manifest))
                if time.time() - result['captured_at'] >= 25:
                    raise ValueError('Preview status expired during lookup')
                value['source'] = 'service-v1'
                value['preview'] = {'head': manifest['head'], 'base': manifest['base'],
                                    'run': manifest['run'], 'run_attempt': manifest['run_attempt'], 'trusted_run': manifest['trusted_run'],
                                    'trusted_attempt': manifest['trusted_attempt'], 'valid': valid}
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
        if parts.scheme or parts.netloc or parts.fragment:
            self.reply(404, {'error': 'Not found'})
            return
        if not match:
            self.serve_static(parts.path, parts.query)
            return
        if parts.query:
            self.reply(404, {'error': 'Not found'})
            return
        try:
            result = self.server.get_review(int(match.group(1)))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
            self.reply(503, {'error': 'Review status unavailable'})
            return
        self.reply(200, result)

    def serve_static(self, path, query):
        try:
            site = self.server.active_site()
        except (OSError, ValueError, UnicodeError):
            self.reply(503, {'error': 'Preview site unavailable'})
            return
        names = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/index.html': ('index.html', 'text/html; charset=utf-8'),
                 '/shell.js': ('shell.js', 'text/javascript; charset=utf-8'),
                 '/frame.js': ('frame.js', 'text/javascript; charset=utf-8'),
                 '/shell.css': ('shell.css', 'text/css; charset=utf-8')}
        entry = names.get(path)
        object_match, manifest_match = OBJECT.fullmatch(path), MANIFEST.fullmatch(path)
        if (site is None or (query and path not in ('/', '/index.html'))
                or not (entry or object_match or manifest_match)):
            self.reply(404, {'error': 'Not found'})
            return
        if entry:
            relative, mime = entry
            maximum = 1024 * 1024
        elif object_match:
            relative = f'previews/{object_match.group(1)}/objects/{object_match.group(2)}.json'
            mime, maximum = 'application/json; charset=utf-8', 24 * 1024 * 1024
        else:
            relative = f'previews/{manifest_match.group(1)}/manifest.json'
            mime, maximum = 'application/json; charset=utf-8', 16 * 1024 * 1024
        if not self.server.static_slots.acquire(blocking=False):
            self.reply(503, {'error': 'Preview busy'})
            return
        try:
            candidate = site
            for part in relative.split('/'):
                candidate = candidate / part
                if candidate.is_symlink():
                    raise OSError('Symbolic links are not allowed')
            if not candidate.is_file() or candidate.stat().st_size > maximum:
                raise OSError('Missing or oversized preview file')
            content = candidate.read_bytes()
            if len(content) > maximum:
                raise OSError('Preview file changed during read')
            if mime.startswith('text/html'):
                if content.count(b'</head>') != 1:
                    raise OSError('Invalid trusted preview shell')
                content = content.replace(b'</head>', b'<meta name="preview-service" content="enabled"></head>', 1)
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store, max-age=0')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if mime.startswith('text/html'):
                self.send_header('Content-Security-Policy', "frame-src 'none'")
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except OSError:
            # If writing already began, the client simply receives a truncated response.
            if 'content' not in locals():
                self.reply(404, {'error': 'Not found'})
        finally:
            self.server.static_slots.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo')
    parser.add_argument('--port', type=int, default=8765)
    sites = parser.add_mutually_exclusive_group()
    sites.add_argument('--site', help='immutable assembled preview site directory')
    sites.add_argument('--site-root', help='managed preview releases directory')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Invalid port')
    server = ReviewServer(('127.0.0.1', args.port), args.repo, site=args.site, site_root=args.site_root)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
