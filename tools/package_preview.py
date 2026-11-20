"""Package the generated Wiki as untrusted preview input, without publishing it."""
import os
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

if __package__:
    from .preview_artifact import (MAX_ARCHIVE_BYTES, MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES,
                                   InvalidPreviewArtifact, archive_path)
else:
    from preview_artifact import (MAX_ARCHIVE_BYTES, MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES,
                                  InvalidPreviewArtifact, archive_path)


def package_site(wiki, destination):
    wiki, destination = Path(wiki), Path(destination)
    if wiki.is_symlink() or not wiki.is_dir() or destination.exists() or destination.is_symlink():
        raise InvalidPreviewArtifact("Invalid source or destination")
    roots = [wiki / "index.html", wiki / "assets", wiki / "data", wiki / "pages"]
    if any(path.is_symlink() or not path.exists() for path in roots):
        raise InvalidPreviewArtifact("Wiki output is incomplete or contains a link")
    files = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            for directory, names, names_files in os.walk(root, followlinks=False):
                for name in names:
                    if (Path(directory) / name).is_symlink():
                        raise InvalidPreviewArtifact("Links are not allowed in the Wiki output")
                files.extend(Path(directory) / name for name in names_files)
        else:
            raise InvalidPreviewArtifact("Unexpected Wiki output type")
    if len(files) > MAX_FILES:
        raise InvalidPreviewArtifact("Too many Wiki files")
    total = 0
    fd, temp_name = tempfile.mkstemp(prefix=".preview-package-", suffix=".zip", dir=destination.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temp_name, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(files):
                relative = path.relative_to(wiki).as_posix()
                archive_path(relative)
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                    raise InvalidPreviewArtifact("Invalid or oversized Wiki file")
                total += info.st_size
                if total > MAX_TOTAL_BYTES:
                    raise InvalidPreviewArtifact("Wiki output exceeds size limit")
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                with os.fdopen(os.open(path, flags), "rb") as source:
                    opened = os.fstat(source.fileno())
                    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
                        raise InvalidPreviewArtifact("Wiki file changed while packaging")
                    with archive.open(relative, "w") as target:
                        remaining = info.st_size
                        while remaining:
                            chunk = source.read(min(65536, remaining))
                            if not chunk:
                                raise InvalidPreviewArtifact("Wiki file changed while packaging")
                            target.write(chunk)
                            remaining -= len(chunk)
                        if source.read(1):
                            raise InvalidPreviewArtifact("Wiki file changed while packaging")
        if os.path.getsize(temp_name) > MAX_ARCHIVE_BYTES:
            raise InvalidPreviewArtifact("Compressed Wiki exceeds size limit")
        if destination.exists() or destination.is_symlink():
            raise InvalidPreviewArtifact("Destination already exists")
        os.rename(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: python tools/package_preview.py WIKI_DIR OUTPUT.zip")
    package_site(sys.argv[1], sys.argv[2])
