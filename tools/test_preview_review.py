"""Review status must fail closed for stale, missing, or revoked approvals."""
import copy
import unittest
from unittest.mock import patch

from tools.preview_review import approved_for_head, review_evidence, snapshot

HEAD = "a" * 40
PR = {"state": "open", "head": {"sha": HEAD}, "user": {"login": "Author"}, "updated_at": "2026-01-01T00:00:00Z"}


def review(login="Reviewer", state="APPROVED", sha=HEAD, number=1, timestamp="2026-01-01T00:00:00Z", user_type="User"):
    return {"id": number, "user": {"login": login, "type": user_type}, "state": state,
            "commit_id": sha, "submitted_at": timestamp}


class PreviewReviewTests(unittest.TestCase):
    def test_current_authorized_review(self):
        self.assertTrue(approved_for_head(PR, [review()], {"reviewer": "write"}))
        self.assertTrue(approved_for_head(PR, [review(), review(state="COMMENTED", number=2)], {"reviewer": "admin"}))

    def test_stale_self_bot_or_unauthorized_reviews_do_not_approve(self):
        for item, permissions in [
            (review(sha="b" * 40), {"reviewer": "write"}),
            (review(login="Author"), {"author": "admin"}),
            (review(user_type="Bot"), {"reviewer": "admin"}),
            (review(), {"reviewer": "read"}),
            (review(), {}),
        ]:
            with self.subTest(item=item, permissions=permissions):
                self.assertFalse(approved_for_head(PR, [item], permissions))

    def test_revoked_or_new_change_request_blocks(self):
        for second in [review(state="DISMISSED", number=2), review(state="CHANGES_REQUESTED", number=2),
                       review(login="Other", state="CHANGES_REQUESTED", number=2)]:
            self.assertFalse(approved_for_head(PR, [review(), second], {"reviewer": "write", "other": "write"}))

    def test_closed_or_malformed_snapshot_fails_closed(self):
        closed = {**PR, "state": "closed"}
        self.assertFalse(approved_for_head(closed, [review()], {"reviewer": "write"}))
        self.assertFalse(approved_for_head(PR, [review(timestamp="bad")], {"reviewer": "write"}))
        self.assertFalse(approved_for_head(PR, [review(state="UNKNOWN")], {"reviewer": "write"}))

    def test_snapshot_detects_changed_pr_or_review_list(self):
        changed = copy.deepcopy(PR)
        changed["head"]["sha"] = "b" * 40
        for current, final_pages in [(changed, [[review()]]), (PR, [[]])]:
            responses = [PR, [[review()]], {"permission": "write"}, current, final_pages]
            with patch("tools.preview_review.gh_api", side_effect=responses):
                self.assertFalse(snapshot("owner/repo", 1)["approved"])

    def test_permission_failure_fails_closed(self):
        import subprocess
        responses = [PR, [[review()]], subprocess.CalledProcessError(1, "gh"), PR, [[review()]]]
        with patch("tools.preview_review.gh_api", side_effect=responses):
            self.assertFalse(snapshot("owner/repo", 1)["approved"])

    def test_review_evidence_is_ordered_and_rejects_duplicate_ids(self):
        self.assertEqual([item[0] for item in review_evidence([review(number=2), review(number=1)])], [1, 2])
        with self.assertRaises(ValueError):
            review_evidence([review(number=1), review(number=1)])


if __name__ == "__main__":
    unittest.main()
