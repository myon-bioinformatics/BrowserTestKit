"""Exercise real Chromium against a deterministic local HTTP fixture."""
import functools, json, subprocess, sys, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path
import pytest


@pytest.mark.browser
def test_dom_capture_local_page(tmp_path):
    (tmp_path/'index.html').write_text('<!doctype html><meta charset="utf-8"><main id="body"><h1>日本語</h1><p>Body &amp; text</p></main>',encoding='utf-8')
    handler=functools.partial(SimpleHTTPRequestHandler,directory=str(tmp_path))
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        script=Path(__file__).resolve().parents[2]/'examples/capture_browser_dom.py'
        output=tmp_path/'capture.json'
        args=[sys.executable,str(script),f'http://127.0.0.1:{server.server_port}/index.html',
              '--selector','#body','--output',str(output)]
        run=subprocess.run(args,capture_output=True,text=True,timeout=90)
        assert run.returncode==0,run.stderr
        result=json.loads(output.read_text())
        assert result['schema']=='btk-browser-dom/1'
        assert 'Body &amp; text' in result['html']
        assert result['visible'].split()==['日本語','Body','&','text']
        assert result['content_verified'] is False
        # A repeated capture must preserve the original evidence byte for byte.
        original = output.read_bytes()
        repeat = subprocess.run(args, capture_output=True, text=True, timeout=10)
        assert repeat.returncode == 2
        assert 'output already exists' in repeat.stderr
        assert output.read_bytes() == original
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


@pytest.mark.browser
@pytest.mark.parametrize('selector,code,error', [
    ('#missing', 2, 'Timeout'),
    ('p', 1, 'ambiguous_selector'),
])
def test_selector_mismatch_does_not_save(tmp_path, selector, code, error):
    (tmp_path / 'index.html').write_text('<main><p>one</p><p>two</p></main>', encoding='utf-8')
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        script = Path(__file__).resolve().parents[2] / 'examples/capture_browser_dom.py'
        output = tmp_path / 'capture.json'
        run = subprocess.run(
            [sys.executable, str(script), f'http://127.0.0.1:{server.server_port}/index.html',
             '--selector', selector, '--output', str(output), '--timeout-ms', '3000'],
            capture_output=True, text=True, timeout=30)
        assert run.returncode == code, run.stderr
        assert error in run.stderr
        assert not output.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.browser
def test_aria_snapshot_and_same_origin_only(tmp_path):
    (tmp_path/'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><style>a::before{content:"["}a::after{content:"]"}</style>'
        '<main id="body"><a href="/x">GitHub</a><button aria-label="Delete">\U0001F5D1\ufe0f</button>'
        '<img src="https://example.invalid/pixel.png" alt="Logo"></main>', encoding='utf-8')
    handler=functools.partial(SimpleHTTPRequestHandler,directory=str(tmp_path))
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        script=Path(__file__).resolve().parents[2]/'examples/capture_browser_dom.py'
        output=tmp_path/'capture.json'
        run=subprocess.run([sys.executable,str(script),f'http://127.0.0.1:{server.server_port}/index.html',
                            '--selector','#body','--output',str(output),'--aria-snapshot','--same-origin-only'],
                           capture_output=True,text=True,timeout=90)
        assert run.returncode==0,run.stderr
        result=json.loads(output.read_text(encoding='utf-8'))
        # CSS generated content is part of the browser's accessible name, not of the HTML.
        assert '- link "[GitHub]"' in result['aria_snapshot']
        assert '- button "Delete"' in result['aria_snapshot'] and '- img "Logo"' in result['aria_snapshot']
        assert result['blocked_requests']==['https://example.invalid/pixel.png']
        assert result['browser_version']
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def _capture_module():
    import importlib.util
    path = Path(__file__).resolve().parents[2] / 'examples/capture_browser_dom.py'
    spec = importlib.util.spec_from_file_location('capture_browser_dom', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('a, b, same', [
    ('http://LOCALHOST:8000/a', 'http://localhost:8000/b', True),
    ('http://example.test:80/', 'http://example.test/x', True),
    ('https://Example.Test:443/', 'https://example.test/', True),
    ('http://example.test/', 'https://example.test/', False),
    ('http://example.test:8080/', 'http://example.test/', False),
    ('http://example.test/', 'http://example.test.evil/', False),
    ('http://example.test/', 'http://example.test@evil.test/', False),
])
def test_same_origin_key_uses_scheme_host_and_effective_port(a, b, same):
    key = _capture_module().same_origin_key
    assert (key(a) == key(b)) is same
    assert key(a) is not None


def test_same_origin_key_without_host_or_with_bad_port():
    key = _capture_module().same_origin_key
    assert key('data:text/plain,x') is None
    assert key('http://example.test:99999/') is None
