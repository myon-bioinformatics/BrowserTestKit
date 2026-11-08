"""Publish a bounded run digest through the canonical gh_ops write operation.

Exact summaries already posted by the Actions bot are not reposted. Existing
comments, including another author's matching marker, are never modified.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# gh_ops is vendored under tools/ (see vendor.lock.json).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import gh_ops  # noqa: E402

MAX_BYTES = 60_000


def publish(repo, number, body, *, client=None):
    if not body.startswith('<!-- platform-apps-digest -->') or len(body.encode('utf-8')) > MAX_BYTES:
        raise ValueError('expected a bounded platform-apps digest')
    client = client or gh_ops.Client()
    repo = gh_ops._repo(repo)
    comments = client.paginate(f'/repos/{repo}/issues/{int(number)}/comments')
    for comment in comments:
        if ((comment.get('user') or {}).get('login') == 'github-actions[bot]'
                and (comment.get('body') or '').rstrip('\n') == body.rstrip('\n')):
            return {'ok': True, 'posted': False, 'duplicate': True, 'comment_id': comment['id']}
    return gh_ops.issue_comment_create(repo, number, body=body, write=True, client=client)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('repo')
    parser.add_argument('number', type=int)
    parser.add_argument('--file', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        with args.file.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('digest exceeds 60000 bytes')
        result = publish(args.repo, args.number, raw.decode('utf-8').rstrip('\n'))
    except (OSError, ValueError, gh_ops.GhOpsError) as error:
        parser.exit(2, f'error: {error}\n')
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
