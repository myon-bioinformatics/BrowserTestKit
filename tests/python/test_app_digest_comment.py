"""Digest writes use local GitHub REST helpers; duplicate reruns and other authors are safe."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import app_digest_comment as comment

BODY = '<!-- platform-apps-digest -->\n1/1 passed; [Run](https://example.test/run/1)'


class Client:
    def __init__(self, comments):
        self.comments = comments

    def paginate(self, path):
        assert path == '/repos/owner/repo/issues/96/comments'
        return self.comments


def test_exact_bot_duplicate_is_not_posted(monkeypatch):
    monkeypatch.setattr(comment, 'issue_comment_create', lambda *a, **k: pytest.fail('no duplicate write'))
    client = Client([{'id': 12, 'user': {'login': 'github-actions[bot]'}, 'body': BODY + '\n'}])
    assert comment.publish('owner/repo', 96, BODY, client=client) == {
        'ok': True, 'posted': False, 'duplicate': True, 'comment_id': 12}


@pytest.mark.parametrize('old', [BODY, '<!-- platform-apps-digest -->\nolder run'])
def test_other_authors_are_never_patched_and_new_runs_use_canonical_create(monkeypatch, old):
    calls = []
    monkeypatch.setattr(comment, 'issue_comment_create', lambda *a, **k: calls.append((a, k)) or {'ok': True})
    client = Client([{'id': 12, 'user': {'login': 'someone-else'}, 'body': old}])
    assert comment.publish('owner/repo', 96, BODY, client=client)['ok']
    assert calls == [(('owner/repo', 96), {'body': BODY, 'write': True, 'client': client})]


@pytest.mark.parametrize('body', ['unrelated', '<!-- platform-apps-digest -->' + 'x' * 60_000])
def test_unbounded_or_wrong_summary_rejected_before_api(body):
    with pytest.raises(ValueError, match='bounded'):
        comment.publish('owner/repo', 96, body, client=Client([]))
