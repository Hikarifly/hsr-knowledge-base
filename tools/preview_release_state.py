"""Resolve the active immutable preview release without following links."""
import re
from pathlib import Path

RELEASE = re.compile(r'site-[0-9a-f]{32}')


def active_site(root, *, optional=False):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Invalid release root')
    sites, pointer = root / 'sites', root / 'current'
    if sites.is_symlink() or not sites.is_dir() or pointer.is_symlink():
        raise ValueError('Invalid release state')
    if optional and not pointer.exists():
        return None
    if not pointer.is_file() or pointer.stat().st_size > 80:
        raise ValueError('Invalid active release')
    name = pointer.read_text(encoding='ascii').strip()
    if not RELEASE.fullmatch(name):
        raise ValueError('Invalid active release name')
    site = sites / name
    if site.is_symlink() or not site.is_dir():
        raise ValueError('Active release is unavailable')
    return site
