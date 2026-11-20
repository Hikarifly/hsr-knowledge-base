"""Encode an extracted preview as inert JSON for a separately hosted viewer.

This is not a publisher: the caller must obtain trusted PR and workflow metadata,
use the safe archive extractor, and enforce freshness and review policy. Never
serve the extracted source directory, which may contain attacker-controlled HTML.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

from tools.preview_artifact import MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, InvalidPreviewArtifact, archive_path


def safe_json(value):
    """Ensure even a wrongly served JSON file contains no literal HTML markup."""
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def build_bundle(site, destination, *, repo, pr, head, base, run, run_attempt=None, trusted_run=None, trusted_attempt=None):
    """Encode files into hashed JSON objects and a manifest in a fresh directory."""
    site, destination = Path(site), Path(destination)
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
            or not isinstance(pr, int) or pr < 1 or not isinstance(run, int) or run < 1
            or (run_attempt is not None and (type(run_attempt) is not int or run_attempt < 1))
            or ((trusted_run is not None or trusted_attempt is not None) and
                (type(trusted_run) is not int or trusted_run < 1 or type(trusted_attempt) is not int or trusted_attempt < 1))
            or not re.fullmatch(r"[0-9a-f]{40}", head) or not re.fullmatch(r"[0-9a-f]{40}", base)):
        raise InvalidPreviewArtifact("Invalid preview metadata")
    if site.is_symlink() or not site.is_dir() or destination.exists() or destination.is_symlink():
        raise InvalidPreviewArtifact("Invalid source or destination")
    stage = Path(tempfile.mkdtemp(prefix=".preview-bundle-", dir=destination.parent))
    try:
        objects = stage / "objects"
        objects.mkdir()
        files = {}
        total = 0
        for directory, names, filenames in os.walk(site, followlinks=False):
            for name in names:
                if (Path(directory) / name).is_symlink():
                    raise InvalidPreviewArtifact("Links are not allowed in a preview bundle")
            for name in sorted(filenames):
                path = Path(directory) / name
                relative = path.relative_to(site).as_posix()
                archive_path(relative)
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                    raise InvalidPreviewArtifact("Invalid or oversized preview file")
                total += info.st_size
                if len(files) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                    raise InvalidPreviewArtifact("Preview bundle exceeds size limits")
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                with os.fdopen(os.open(path, flags), "rb") as src:
                    opened = os.fstat(src.fileno())
                    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
                        raise InvalidPreviewArtifact("Preview file changed while bundling")
                    data = src.read(MAX_FILE_BYTES + 1)
                    if len(data) != info.st_size:
                        raise InvalidPreviewArtifact("Preview file changed while bundling")
                digest = hashlib.sha256(data).hexdigest()
                obj = objects / (digest + ".json")
                if not obj.exists():
                    obj.write_text(safe_json({"data": base64.b64encode(data).decode("ascii")}), encoding="ascii")
                files[relative] = digest
        if "index.html" not in files:
            raise InvalidPreviewArtifact("Preview index.html is missing")
        manifest = {"version": 1, "repo": repo, "pr": pr, "head": head, "base": base, "run": run, "files": files}
        if run_attempt is not None:
            manifest['run_attempt'] = run_attempt
        if trusted_run is not None:
            manifest.update(trusted_run=trusted_run, trusted_attempt=trusted_attempt)
        (stage / "manifest.json").write_text(safe_json(manifest), encoding="ascii")
        if destination.exists() or destination.is_symlink():
            raise InvalidPreviewArtifact("Destination already exists")
        os.rename(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
