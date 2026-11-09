"""Dependency-free CLI contract checks, not browser execution certification."""
import subprocess, sys
from pathlib import Path
import pytest

SCRIPT=Path(__file__).resolve().parents[2]/'examples/capture_browser_dom.py'

@pytest.mark.parametrize('args,code',[(['--help'],0),(['file:///etc/passwd','--output','unused.json'],2),(['https://example.com','--output','unused.json','--timeout-ms','0'],2)])
def test_cli_preflight_without_browser(args,code,tmp_path):
    run=subprocess.run([sys.executable,str(SCRIPT),*args],cwd=tmp_path,capture_output=True,text=True)
    assert run.returncode==code
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('kind', ['file', 'directory', 'dangling_symlink', 'missing_parent'])
def test_unusable_output_rejected_before_browser_import_or_network(tmp_path, kind):
    import os
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'<main>must not be fetched</main>')

        def log_message(self, *args):
            pass

    output = tmp_path / 'capture.json'
    if kind == 'file':
        output.write_bytes(b'existing evidence')
    elif kind == 'directory':
        output.mkdir()
    elif kind == 'dangling_symlink':
        output.symlink_to(tmp_path / 'missing.json')
    else:
        output = tmp_path / 'missing' / 'capture.json'
    # The guard must run before even importing Playwright; no installation needed.
    package = tmp_path / 'playwright'
    package.mkdir()
    marker = tmp_path / 'browser-imported'
    (package / '__init__.py').write_text(
        'from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n'
        'raise AssertionError("Playwright must not be imported")\n', encoding='utf-8')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        run = subprocess.run([sys.executable, '-S', str(SCRIPT),
                              f'http://127.0.0.1:{server.server_port}/', '--output', str(output)],
                             env={**os.environ, 'PYTHONPATH': str(tmp_path)},
                             capture_output=True, text=True, timeout=10)
        assert run.returncode == 2
        assert ('output already exists' if kind != 'missing_parent' else 'output parent is not a directory') in run.stderr
        assert not marker.exists()
        assert requests == []
        if kind == 'file':
            assert output.read_bytes() == b'existing evidence'
        elif kind == 'directory':
            assert output.is_dir() and not list(output.iterdir())
        elif kind == 'dangling_symlink':
            assert output.is_symlink() and not output.exists()
        else:
            assert not output.parent.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
