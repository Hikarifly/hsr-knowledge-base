"""Verify the checked Wiki can be packaged for later isolated preview publication."""
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools.package_preview import package_site
from tools.preview_artifact import InvalidPreviewArtifact, extract_preview_zip


class PackagePreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.wiki = self.root / "wiki"
        self.wiki.mkdir()
        (self.wiki / "index.html").write_text("home", encoding="utf-8")
        for name in ("assets", "data", "pages"):
            (self.wiki / name).mkdir()
        (self.wiki / "pages" / "页面.html").write_text("entry", encoding="utf-8")
        (self.wiki / "build_wiki.py").write_text("source", encoding="utf-8")
        self.zip = self.root / "preview.zip"

    def test_package_contains_site_files_and_is_extractable(self):
        package_site(self.wiki, self.zip)
        with zipfile.ZipFile(self.zip) as archive:
            self.assertEqual(set(archive.namelist()), {"index.html", "pages/页面.html"})
        output = self.root / "extracted"
        extract_preview_zip(self.zip, output)
        self.assertEqual((output / "pages" / "页面.html").read_text(encoding="utf-8"), "entry")

    def test_rejects_incomplete_oversized_or_existing_output(self):
        with patch("tools.package_preview.MAX_FILE_BYTES", 1):
            with self.assertRaises(InvalidPreviewArtifact):
                package_site(self.wiki, self.zip)
        self.assertFalse(self.zip.exists())
        self.assertFalse(list(self.root.glob('.preview-package-*')))
        (self.wiki / "index.html").unlink()
        with self.assertRaises(InvalidPreviewArtifact):
            package_site(self.wiki, self.zip)
        self.zip.write_bytes(b"keep")
        with self.assertRaises(InvalidPreviewArtifact):
            package_site(self.wiki, self.zip)
        self.assertEqual(self.zip.read_bytes(), b"keep")

    def test_rejects_links_in_site_tree(self):
        link = self.wiki / "assets" / "link"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(InvalidPreviewArtifact):
            package_site(self.wiki, self.zip)
        self.assertFalse(self.zip.exists())


if __name__ == "__main__":
    unittest.main()
