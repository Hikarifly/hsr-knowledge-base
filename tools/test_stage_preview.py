"""The staging gate treats GitHub metadata and downloaded ZIPs as untrusted."""
import copy
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools.package_preview import package_site
from tools.preview_artifact import InvalidPreviewArtifact
from tools.stage_preview import select_artifact, stage, unwrap_artifact

HEAD, BASE = 'a' * 40, 'b' * 40
PR = {'number': 7, 'state': 'open', 'head': {'sha': HEAD, 'repo': {'id': 1}},
      'base': {'sha': BASE, 'repo': {'id': 1, 'full_name': 'owner/repo'}}}
RUN = {'id': 10, 'repository': {'id': 1}, 'event': 'pull_request', 'status': 'completed',
       'conclusion': 'success', 'path': '.github/workflows/wiki-quality.yml', 'head_sha': HEAD,
       'pull_requests': [{'number': 7, 'head': {'sha': HEAD, 'repo': {'id': 1}},
                          'base': {'sha': BASE, 'repo': {'id': 1}}}]}
ARTIFACT = {'id': 20, 'name': f'wiki-preview-7-{HEAD}', 'expired': False, 'size_in_bytes': 123,
            'workflow_run': {'id': 10, 'repository_id': 1, 'head_repository_id': 1, 'head_sha': HEAD}}


class StagingTests(unittest.TestCase):
    def select(self, pr=PR, run=RUN, pages=None, approved=False):
        return select_artifact('owner/repo', 7, 10, pr, run,
                               [{'artifacts': [ARTIFACT]}] if pages is None else pages, approved)

    def test_matching_metadata(self):
        self.assertEqual(self.select(), (20, HEAD, BASE))

    def test_stale_failed_or_unmatched_inputs_are_rejected(self):
        cases = []
        for field, value in [('state', 'closed'), ('number', 8)]:
            pr = copy.deepcopy(PR); pr[field] = value; cases.append((pr, RUN, None))
        pr = copy.deepcopy(PR); pr['base']['sha'] = 'c' * 40; cases.append((pr, RUN, None))
        for field, value in [('head_sha', 'c' * 40), ('conclusion', 'failure'), ('status', 'in_progress'),
                             ('event', 'push'), ('path', '.github/workflows/other.yml')]:
            run = copy.deepcopy(RUN); run[field] = value; cases.append((PR, run, None))
        for field, value in [('expired', True), ('name', 'wrong'), ('size_in_bytes', 0)]:
            item = copy.deepcopy(ARTIFACT); item[field] = value; cases.append((PR, RUN, [{'artifacts': [item]}]))
        item = copy.deepcopy(ARTIFACT); item['workflow_run']['id'] = 11; cases.append((PR, RUN, [{'artifacts': [item]}]))
        cases.append((PR, RUN, [{'artifacts': [ARTIFACT, ARTIFACT]}]))
        for pr, run, pages in cases:
            with self.subTest(pr=pr, run=run, pages=pages), self.assertRaises(InvalidPreviewArtifact):
                self.select(pr, run, pages)

    def test_external_pr_requires_current_authorized_approval(self):
        pr, run, artifact = copy.deepcopy(PR), copy.deepcopy(RUN), copy.deepcopy(ARTIFACT)
        pr['head']['repo']['id'] = run['pull_requests'][0]['head']['repo']['id'] = 2
        artifact['workflow_run']['head_repository_id'] = 2
        pages = [{'artifacts': [artifact]}]
        with self.assertRaises(InvalidPreviewArtifact): self.select(pr, run, pages)
        self.assertEqual(self.select(pr, run, pages, approved=True), (20, HEAD, BASE))
        self.assertRaises(InvalidPreviewArtifact, self.select, pr, run, pages, 'yes')

    def test_wrapper_rejects_other_members_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in ['../wiki-preview.zip', 'other.zip']:
                wrapper = root / 'input.zip'
                with zipfile.ZipFile(wrapper, 'w') as z: z.writestr(filename, b'data')
                with self.assertRaises(InvalidPreviewArtifact): unwrap_artifact(wrapper, root / 'out')
                wrapper.unlink()
            with zipfile.ZipFile(root / 'input.zip', 'w') as z:
                info = zipfile.ZipInfo('wiki-preview.zip'); info.create_system = 3; info.external_attr = 0o120777 << 16
                z.writestr(info, b'link')
            with self.assertRaises(InvalidPreviewArtifact): unwrap_artifact(root / 'input.zip', root / 'out')
            self.assertFalse((root / 'out').exists())

    def test_staging_rechecks_metadata_and_leaves_no_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); wiki = root / 'wiki'; wiki.mkdir()
            (wiki / 'index.html').write_text('<h1>test</h1>', encoding='utf-8')
            for name in ('assets', 'data', 'pages'): (wiki / name).mkdir()
            package_site(wiki, root / 'inner.zip')
            with zipfile.ZipFile(root / 'wrapper.zip', 'w') as z: z.write(root / 'inner.zip', 'wiki-preview.zip')
            pages = [{'artifacts': [ARTIFACT]}]
            def fake_download(repo, artifact_id, destination): Path(destination).write_bytes((root / 'wrapper.zip').read_bytes())
            good = [PR, RUN, pages]
            changed = copy.deepcopy(PR); changed['head']['sha'] = 'c' * 40
            with patch('tools.stage_preview.gh_api', side_effect=good * 3), patch('tools.stage_preview.download_artifact', side_effect=fake_download):
                stage('owner/repo', 7, 10, root / 'bundle')
            self.assertTrue((root / 'bundle' / 'manifest.json').is_file())
            with patch('tools.stage_preview.gh_api', side_effect=good * 2 + [changed, RUN, pages]), patch('tools.stage_preview.download_artifact', side_effect=fake_download):
                with self.assertRaises(InvalidPreviewArtifact): stage('owner/repo', 7, 10, root / 'stale')
            self.assertFalse((root / 'stale').exists())

    def test_external_approval_revoked_during_conversion_leaves_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); wiki = root / 'wiki'; wiki.mkdir()
            (wiki / 'index.html').write_text('<h1>test</h1>', encoding='utf-8')
            for name in ('assets', 'data', 'pages'): (wiki / name).mkdir()
            package_site(wiki, root / 'inner.zip')
            with zipfile.ZipFile(root / 'wrapper.zip', 'w') as z: z.write(root / 'inner.zip', 'wiki-preview.zip')
            pr, run, artifact = copy.deepcopy(PR), copy.deepcopy(RUN), copy.deepcopy(ARTIFACT)
            pr['head']['repo']['id'] = run['pull_requests'][0]['head']['repo']['id'] = 2
            artifact['workflow_run']['head_repository_id'] = 2
            responses = [pr, run, [{'artifacts': [artifact]}]] * 3
            approvals = [{'head': HEAD, 'approved': True}] * 2 + [{'head': HEAD, 'approved': False}]
            def fake_download(repo, artifact_id, destination): Path(destination).write_bytes((root / 'wrapper.zip').read_bytes())
            with patch('tools.stage_preview.gh_api', side_effect=responses), patch('tools.stage_preview.snapshot', side_effect=approvals), patch('tools.stage_preview.download_artifact', side_effect=fake_download):
                with self.assertRaises(InvalidPreviewArtifact): stage('owner/repo', 7, 10, root / 'revoked')
            self.assertFalse((root / 'revoked').exists())


if __name__ == '__main__': unittest.main()
