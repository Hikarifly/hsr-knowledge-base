"""A public preview bundle must never contain directly executable PR files."""
import base64
import json
import tempfile
import unittest
from pathlib import Path

from tools.preview_artifact import InvalidPreviewArtifact, extract_preview_zip
from tools.preview_bundle import build_bundle, safe_json
from tools.package_preview import package_site


class PreviewBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.wiki = self.root / "wiki"
        self.wiki.mkdir()
        (self.wiki / "index.html").write_text('<script>parent.document.body.innerHTML="owned"</script>', encoding="utf-8")
        for name in ("assets", "data", "pages"):
            (self.wiki / name).mkdir()
        (self.wiki / "assets" / "app.js").write_text('alert("untrusted")', encoding="utf-8")
        self.meta = {"repo": "owner/repo", "pr": 17, "head": "a" * 40, "base": "b" * 40, "run": 123}

    def test_full_pipeline_produces_only_inert_json(self):
        archive = self.root / "preview.zip"
        source = self.root / "source"
        bundle = self.root / "bundle"
        package_site(self.wiki, archive)
        extract_preview_zip(archive, source)
        build_bundle(source, bundle, **self.meta)
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="ascii"))
        self.assertEqual({key: manifest[key] for key in self.meta}, self.meta)
        for path in bundle.rglob('*'):
            if path.is_file():
                self.assertEqual(path.suffix, '.json')
                contents = path.read_text(encoding="ascii")
                self.assertNotIn('<', contents)
                self.assertNotIn('>', contents)
        digest = manifest['files']['index.html']
        payload = json.loads((bundle / 'objects' / (digest + '.json')).read_text(encoding='ascii'))
        self.assertEqual(base64.b64decode(payload['data']), (self.wiki / 'index.html').read_bytes())
        self.assertFalse((bundle / 'index.html').exists())

    def test_rejects_invalid_metadata_and_existing_destination(self):
        invalid = {**self.meta, "head": "not-a-sha"}
        with self.assertRaises(InvalidPreviewArtifact):
            build_bundle(self.wiki, self.root / 'bundle', **invalid)
        target = self.root / 'bundle'
        target.mkdir()
        (target / 'keep').write_text('keep', encoding='ascii')
        with self.assertRaises(InvalidPreviewArtifact):
            build_bundle(self.wiki, target, **self.meta)
        self.assertEqual((target / 'keep').read_text(encoding='ascii'), 'keep')

    def test_json_escapes_markup_but_round_trips(self):
        value = {'<script>': 'x&y</script>'}
        data = safe_json(value)
        self.assertNotIn('<', data)
        self.assertNotIn('>', data)
        self.assertNotIn('&', data)
        self.assertEqual(json.loads(data), value)


if __name__ == '__main__':
    unittest.main()
