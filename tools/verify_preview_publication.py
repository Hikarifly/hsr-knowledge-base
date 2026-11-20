"""Recheck an assembled catalog immediately before uploading it to Pages.

This reduces, but cannot eliminate, races between verification and deployment.
It never executes the preview content and fails closed on API or metadata errors.
"""
import argparse
import subprocess
from pathlib import Path

from tools.preview_artifact import InvalidPreviewArtifact
from tools.preview_catalog import PR_NUMBER, metadata, validated_manifest
from tools.preview_review import REPO_PATTERN, gh_api, snapshot
from tools.reconcile_previews import latest_run, pages_items


def verify(repo, site):
    if not isinstance(repo, str) or not REPO_PATTERN.fullmatch(repo):
        raise InvalidPreviewArtifact('Invalid repository')
    site = Path(site)
    previews = site / 'previews'
    if site.is_symlink() or not site.is_dir() or previews.is_symlink() or not previews.is_dir():
        raise InvalidPreviewArtifact('Invalid catalog')
    entries = sorted(previews.iterdir(), key=lambda item: item.name)
    if len(entries) > 1000:
        raise InvalidPreviewArtifact('Too many previews')
    for entry in entries:
        if not PR_NUMBER.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
            raise InvalidPreviewArtifact('Invalid preview entry')
        number = int(entry.name)
        manifest = validated_manifest(entry, repo, number)
        prefix = f'repos/{repo}'
        pr = gh_api(f'{prefix}/pulls/{number}')
        if not metadata(repo, number, manifest):
            raise InvalidPreviewArtifact('Preview changed before publication')
        for workflow, event, key, attempt in (
            ('wiki-quality.yml', 'pull_request', 'run', 'run_attempt'),
            ('wiki-trusted-data.yml', 'pull_request_target', 'trusted_run', 'trusted_attempt'),
        ):
            path = f'.github/workflows/{workflow}'
            runs = pages_items(gh_api(f"{prefix}/actions/workflows/{workflow}/runs?event={event}&head_sha={manifest['head']}&per_page=100", paginate=True), 'workflow_runs')
            latest = latest_run(runs, number, pr, event, path)
            if (not latest or latest.get('id') != manifest[key] or latest.get('run_attempt') != manifest[attempt]
                    or latest.get('status') != 'completed' or latest.get('conclusion') != 'success'):
                raise InvalidPreviewArtifact('A newer or unsuccessful check exists')
        head_id = pr.get('head', {}).get('repo', {}).get('id')
        base_id = pr.get('base', {}).get('repo', {}).get('id')
        if type(head_id) is not int or type(base_id) is not int or min(head_id, base_id) < 1:
            raise InvalidPreviewArtifact('Invalid repository metadata')
        if head_id != base_id:
            review = snapshot(repo, number)
            if review.get('head') != manifest['head'] or review.get('approved') is not True:
                raise InvalidPreviewArtifact('External preview approval is no longer valid')
        if not metadata(repo, number, manifest):
            raise InvalidPreviewArtifact('Preview changed during final verification')
    return len(entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo')
    parser.add_argument('site')
    args = parser.parse_args()
    try:
        count = verify(args.repo, args.site)
    except (InvalidPreviewArtifact, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Cannot verify preview catalog: {error}\n')
    print(f'Verified {count} preview(s) for publication')


if __name__ == '__main__':
    main()
