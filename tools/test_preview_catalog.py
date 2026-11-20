"""Build a fresh multi-PR site and omit closed or stale previews."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.preview_artifact import InvalidPreviewArtifact
from tools.preview_bundle import build_bundle
from tools.preview_catalog import assemble

HEAD, BASE = 'a' * 40, 'b' * 40


class PreviewCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bundles = self.root / 'bundles'; self.bundles.mkdir()
        self.shell = self.root / 'shell'; self.shell.mkdir()
        for name in ('index.html', 'shell.css', 'shell.js', 'frame.js'):
            (self.shell / name).write_text('trusted', encoding='ascii')
        self.site = self.root / 'site'; self.site.mkdir()
        (self.site / 'index.html').write_text('<script>untrusted()</script>', encoding='utf-8')
        self.states = {1: 'open', 2: 'open', 3: 'closed', 4: 'stale'}
        for number in self.states:
            build_bundle(self.site, self.bundles / str(number), repo='owner/repo', pr=number, head=HEAD, base=BASE, run=number + 10)

    def api(self, path):
        number = int(path.rsplit('/', 1)[-1])
        if '/pulls/' in path:
            state = self.states[number]
            return {'number': number, 'state': 'closed' if state == 'closed' else 'open',
                    'head': {'sha': 'c' * 40 if state == 'stale' else HEAD}, 'base': {'sha': BASE}}
        number -= 10
        return {'id': number + 10, 'event': 'pull_request', 'status': 'completed', 'conclusion': 'success',
                'path': '.github/workflows/wiki-quality.yml', 'head_sha': HEAD,
                'pull_requests': [{'number': number, 'head': {'sha': HEAD}, 'base': {'sha': BASE}}]}

    def test_multiple_prs_remain_and_closed_or_stale_previews_disappear(self):
        (self.bundles / '1' / 'unused.html').write_text('<script>bad</script>', encoding='ascii')
        with patch('tools.preview_catalog.gh_api', side_effect=self.api):
            included = assemble('owner/repo', self.bundles, self.shell, self.root / 'out')
        self.assertEqual(included, [1, 2])
        output = self.root / 'out'
        self.assertEqual(sorted(p.name for p in (output / 'previews').iterdir()), ['1', '2'])
        self.assertFalse((output / 'previews' / '1' / 'unused.html').exists())
        self.assertEqual((output / 'index.html').read_text(), 'trusted')
        self.assertTrue(all(p.suffix == '.json' for p in (output / 'previews').rglob('*') if p.is_file()))

    def test_corrupted_object_or_missing_api_does_not_create_output(self):
        manifest = json.loads((self.bundles / '1' / 'manifest.json').read_text())
        obj = self.bundles / '1' / 'objects' / (manifest['files']['index.html'] + '.json')
        obj.write_text('{"data":"YQ=="}', encoding='ascii')
        with patch('tools.preview_catalog.gh_api', side_effect=self.api), self.assertRaises(InvalidPreviewArtifact):
            assemble('owner/repo', self.bundles, self.shell, self.root / 'out')
        self.assertFalse((self.root / 'out').exists())
        with patch('tools.preview_catalog.gh_api', side_effect=subprocess.CalledProcessError(1, 'gh')), self.assertRaises(subprocess.CalledProcessError):
            assemble('owner/repo', self.bundles, self.shell, self.root / 'out')
        self.assertFalse((self.root / 'out').exists())

    def test_pr_changing_during_assembly_aborts_without_output(self):
        calls = 0
        def api(path):
            nonlocal calls
            result = self.api(path)
            if path.endswith('/pulls/1'):
                calls += 1
                if calls == 2: result['head']['sha'] = 'd' * 40
            return result
        with patch('tools.preview_catalog.gh_api', side_effect=api), self.assertRaises(InvalidPreviewArtifact):
            assemble('owner/repo', self.bundles, self.shell, self.root / 'out')
        self.assertFalse((self.root / 'out').exists())

    def test_never_overwrites_existing_output(self):
        output = self.root / 'out'; output.mkdir(); (output / 'keep').write_text('keep')
        with self.assertRaises(InvalidPreviewArtifact): assemble('owner/repo', self.bundles, self.shell, output)
        self.assertEqual((output / 'keep').read_text(), 'keep')


if __name__ == '__main__': unittest.main()
