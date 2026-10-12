"""Fake GitHub/GitLab upstream for the CI webhook smoke. Stdlib only, test use only.

Serves metadata and a raw diff for the fixture identities (GitHub ``octo/repo#42``, GitLab
project ``123`` ``!7``) and answers 404 for anything else. It exists so the compose stack can
run the webhook -> inbox -> worker -> context flow without a real provider or network access.
"""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
DIFF = (
    "diff --git a/hello.py b/hello.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/hello.py\n"
    "+++ b/hello.py\n"
    "@@ -1,2 +1,2 @@\n"
    " print('hello')\n"
    "-print('old')\n"
    "+print('new')\n"
)
_GITHUB_PULL = re.compile(r"^/repos/octo/repo/pulls/42$")
_GITLAB_MR = re.compile(r"^/api/v4/projects/123/merge_requests/7(/raw_diffs)?$")
_GITHUB_DIFF_ACCEPT = "application/vnd.github.diff"

_GITHUB_METADATA = {
    "title": "Add feature",
    "state": "open",
    "changed_files": 1,
    "head": {"sha": HEAD_SHA, "ref": "feature"},
    "base": {"sha": BASE_SHA, "ref": "main"},
}
_GITLAB_METADATA = {
    "title": "Fix bug",
    "state": "opened",
    "sha": HEAD_SHA,
    "source_branch": "fix",
    "target_branch": "main",
    "changes_count": "1",
    "diff_refs": {"base_sha": BASE_SHA, "head_sha": HEAD_SHA, "start_sha": BASE_SHA},
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if _GITHUB_PULL.match(path):
            if self.headers.get("Accept") == _GITHUB_DIFF_ACCEPT:
                self._send(DIFF.encode(), "text/plain; charset=utf-8")
            else:
                self._send(json.dumps(_GITHUB_METADATA).encode(), "application/json")
            return
        match = _GITLAB_MR.match(path)
        if match:
            if match.group(1):
                self._send(DIFF.encode(), "text/plain; charset=utf-8")
            else:
                self._send(json.dumps(_GITLAB_METADATA).encode(), "application/json")
            return
        self._send(b'{"message": "not found"}', "application/json", status=404)

    def _send(self, body: bytes, content_type: str, *, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # Paths only; request headers (tokens) are never logged.
        sys.stderr.write(f"vcs-stub: {self.command} {self.path.split('?', 1)[0]}\n")


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _Handler)


if __name__ == "__main__":
    make_server("0.0.0.0", 8080).serve_forever()  # noqa: S104 - container-internal test stub
