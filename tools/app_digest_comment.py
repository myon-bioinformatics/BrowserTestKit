"""Publish a bounded run digest as a PR comment via GitHub REST (stdlib only).

Exact summaries already posted by the Actions bot are not reposted. Existing
comments, including another author's matching marker, are never modified.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

MAX_BYTES = 60_000
API_ROOT = "https://api.github.com"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_NEXT_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class DigestError(Exception):
    """Input or GitHub communication error."""


Transport = Callable[[str, str, dict | None, dict], tuple[int, Any, dict]]


def _repo(repo: str) -> str:
    if not _REPO_RE.match(repo or ""):
        raise DigestError(f"repository must look like OWNER/REPO, got {repo!r}")
    return repo


def _urllib_transport(method: str, url: str, body: dict | None, headers: dict) -> tuple[int, Any, dict]:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30.0) as reply:
            status, raw, reply_headers = reply.status, reply.read(), dict(reply.headers)
    except urllib.error.HTTPError as error:
        status, raw, reply_headers = error.code, error.read(), dict(error.headers or {})
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise DigestError(f"{method} {url} failed: {type(error).__name__}: {reason}") from None
    try:
        payload: Any = json.loads(raw) if raw else None
    except ValueError:
        payload = raw.decode("utf-8", "replace")
    return status, payload, {key.lower(): value for key, value in reply_headers.items()}


class Client:
    """Minimal GitHub REST client with an injectable transport (for tests)."""

    def __init__(self, token: str | None = None, transport: Transport | None = None, api_root: str = API_ROOT):
        if token is None:
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        self._token = token
        self._transport = transport or _urllib_transport
        self.api_root = api_root.rstrip("/")

    def request(self, method: str, path: str, body: dict | None = None, *, expect: tuple[int, ...] = (200,)) -> Any:
        url = path if path.startswith("http") else self.api_root + path
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "browser-test-kit-app-digest",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        status, payload, _headers = self._transport(method, url, body, headers)
        if status not in expect:
            message = payload.get("message", "") if isinstance(payload, dict) else ""
            text = f"{method} {urllib.parse.urlsplit(url).path} -> HTTP {status}"
            if message:
                text += f": {message}"
            raise DigestError(text)
        return payload

    def paginate(self, path: str, *, max_pages: int = 50) -> list:
        items: list = []
        seen = set()
        url, query = path, {"per_page": 100}
        for _ in range(max_pages):
            request_url = url if url.startswith("http") else self.api_root + url
            if query:
                request_url += ("&" if "?" in request_url else "?") + urllib.parse.urlencode(query)
            if request_url in seen:
                raise DigestError("pagination cycle; incomplete response")
            seen.add(request_url)
            full_headers = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "browser-test-kit-app-digest",
            }
            if self._token:
                full_headers["Authorization"] = f"Bearer {self._token}"
            status, payload, headers = self._transport("GET", request_url, None, full_headers)
            if status != 200:
                message = payload.get("message", "") if isinstance(payload, dict) else ""
                raise DigestError(f"GET {urllib.parse.urlsplit(request_url).path} -> HTTP {status}"
                                  + (f": {message}" if message else ""))
            if not isinstance(payload, list):
                raise DigestError("pagination expected a list response")
            items.extend(payload)
            match = _NEXT_LINK_RE.search(headers.get("link", ""))
            if not match:
                return items
            url, query = match.group(1), None
        raise DigestError("pagination page limit exceeded; incomplete response")


def issue_comment_create(repo: str, number: int, *, body: str, write: bool = False, client: Client | None = None) -> dict:
    """Create one top-level PR conversation comment. Verifies the number is a PR first."""
    if not body.strip():
        raise DigestError("comment body must not be empty")
    client = client or Client()
    repo, number = _repo(repo), int(number)
    client.request("GET", f"/repos/{repo}/pulls/{number}")
    path = f"/repos/{repo}/issues/{number}/comments"
    if not write:
        return {"ok": True, "dry_run": True, "posted": False, "number": number, "body_chars": len(body)}
    data = client.request("POST", path, {"body": body}, expect=(201,))
    comment_id = data.get("id") if isinstance(data, dict) else None
    return {"ok": True, "dry_run": False, "posted": True, "number": number, "comment_id": comment_id}


def publish(repo, number, body, *, client=None):
    if not body.startswith('<!-- platform-apps-digest -->') or len(body.encode('utf-8')) > MAX_BYTES:
        raise ValueError('expected a bounded platform-apps digest')
    client = client or Client()
    repo = _repo(repo)
    comments = client.paginate(f'/repos/{repo}/issues/{int(number)}/comments')
    for comment in comments:
        if ((comment.get('user') or {}).get('login') == 'github-actions[bot]'
                and (comment.get('body') or '').rstrip('\n') == body.rstrip('\n')):
            return {'ok': True, 'posted': False, 'duplicate': True, 'comment_id': comment['id']}
    return issue_comment_create(repo, number, body=body, write=True, client=client)


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
    except (OSError, ValueError, DigestError) as error:
        parser.exit(2, f'error: {error}\n')
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
