#!/usr/bin/env python3
"""btk: browser-test-kit's stdlib-only operations in one file.

Commands:
  fetch     fetch one archive from an allowed host, verify its integrity, and extract only named members
  plan      recommend the lightest ``playwright install`` for each workflow job of a checkout
  observe, act, locate, count, describe, compare, operations
            loopback page inspection (see ``btk.py observe --help``)

Stdlib only. Run as ``python -S scripts/btk.py <command> ...``. This one file is the unit to copy when the
kit is vendored: nothing else in scripts/ is needed for these commands.
"""
from __future__ import annotations

import argparse, base64, binascii, contextvars, hashlib, io, json, math, os, platform, re, sys, tarfile, time, urllib.parse, urllib.request
from pathlib import Path

# ---- fetch -----------------------------------------------------------------------------

ALLOWED_HOSTS = {"registry.npmjs.org", "nodejs.org", "codeload.github.com"}
MAX_BYTES = 200 * 1024 * 1024


class FetchError(Exception):
    pass


def parse_integrity(text: str) -> tuple[str, bytes]:
    """Accept SRI (sha512-BASE64, sha256-BASE64) or a bare 64-character sha256 hex digest."""
    if text.startswith(("sha512-", "sha256-")):
        algorithm, _, encoded = text.partition("-")
        try:
            return algorithm, base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise FetchError("integrity is not valid base64") from exc
    if len(text) == 64:
        try:
            return "sha256", bytes.fromhex(text)
        except ValueError as exc:
            raise FetchError("integrity is not a sha256 hex digest") from exc
    raise FetchError("integrity must be sha512-BASE64, sha256-BASE64, or a sha256 hex digest")


def check_member_name(name: str) -> str:
    path = Path(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise FetchError(f"member name is not a plain relative path: {name}")
    return name


def read_archive(source: str) -> bytes:
    parts = urllib.parse.urlsplit(source)
    if parts.scheme == "https":
        if parts.hostname not in ALLOWED_HOSTS:
            raise FetchError(f"host is not allowed: {parts.hostname}")
        with urllib.request.urlopen(source, timeout=60) as response:
            data = response.read(MAX_BYTES + 1)
    elif parts.scheme == "" and os.path.isfile(source):
        # A local file path is accepted for tests and for archives already on disk.
        data = Path(source).read_bytes()
    else:
        raise FetchError("source must be an https URL on an allowed host, or a local file")
    if len(data) > MAX_BYTES:
        raise FetchError("archive is larger than the 200 MiB limit")
    return data


def fetch_members(source: str, integrity: str, members: list[str], dest: str) -> dict:
    algorithm, expected = parse_integrity(integrity)
    if not members:
        raise FetchError("name at least one member to extract")
    names = [check_member_name(name) for name in members]
    blob = read_archive(source)
    digest = hashlib.new(algorithm, blob).digest()
    if digest != expected:
        raise FetchError("archive does not match the expected integrity; nothing was extracted")
    target = Path(dest)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
        for name in names:
            try:
                member = archive.getmember(name)
            except KeyError as exc:
                raise FetchError(f"member not found in archive: {name}") from exc
            if not member.isfile():
                raise FetchError(f"member is not a regular file: {name}")
            out = target / name
            out.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source_file:
                out.write_bytes(source_file.read())
            if member.mode & 0o111:
                out.chmod(0o755)
            written.append(str(out))
    return {"ok": True, "algorithm": algorithm, "files": written}


def load_part(lock_path: str, name: str) -> dict:
    """One part from parts.lock.json: its source, integrity, and the archive member it needs."""
    try:
        lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FetchError(f"cannot read lock file: {lock_path}") from exc
    if lock.get("schema") != "btk-parts-lock/1":
        raise FetchError("lock file schema is not btk-parts-lock/1")
    part = (lock.get("parts") or {}).get(name)
    if not isinstance(part, dict):
        raise FetchError(f"no part named {name} in the lock file")
    if not part.get("integrity") or not part.get("source") or not part.get("member"):
        raise FetchError(f"part {name} cannot be fetched: it has no source, integrity, or member")
    return part


def fetch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="btk.py fetch", description="Fetch one archive member; integrity is checked before extraction.")
    parser.add_argument("source", nargs="?", help="https URL on an allowed host, or a local archive path")
    parser.add_argument("--integrity", help="sha512-BASE64 or sha256-BASE64 from the registry")
    parser.add_argument("--member", action="append", default=[], help="path inside the archive (repeatable)")
    parser.add_argument("--lock", help="parts.lock.json; use with --part instead of source, integrity, and member")
    parser.add_argument("--part", help="part name in the lock file, for example node")
    parser.add_argument("--dest", required=True, help="directory to extract into")
    return parser


def resolve(args) -> tuple[str, str, list[str]]:
    if args.lock or args.part:
        if not (args.lock and args.part):
            raise FetchError("--lock and --part go together")
        part = load_part(args.lock, args.part)
        return part["source"], part["integrity"], [part["member"]]
    if not (args.source and args.integrity and args.member):
        raise FetchError("give source, --integrity, and --member, or use --lock with --part")
    return args.source, args.integrity, args.member


def fetch_cli(argv: list[str] | None = None) -> int:
    args = fetch_parser().parse_args(argv)
    try:
        source, integrity, members = resolve(args)
        payload = fetch_members(source, integrity, members, args.dest)
    except (FetchError, OSError, tarfile.TarError) as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
        return 2
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    return 0


# ---- plan ------------------------------------------------------------------------------

INSTALL = re.compile(r"playwright(?:@[\w.]+)?\s+install(?![\w-])([^\n]*)")
CONFIG_NAMES = re.compile(r"^playwright\.config\.(?:ts|js|mjs|cjs)$")
DEVICES = {"Desktop Chrome": "chromium", "Pixel": "chromium", "Galaxy": "chromium", "Desktop Edge": "chromium",
           "Desktop Firefox": "firefox", "Desktop Safari": "webkit", "iPhone": "webkit", "iPad": "webkit"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "build", "dist"}


def walk(root):
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            yield os.path.join(directory, name)


def read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


def config_facts(root):
    """Union over Playwright configs: engines used and whether the full build is required."""
    engines, full_reasons = set(), []
    for path in walk(root):
        if not CONFIG_NAMES.match(os.path.basename(path)):
            continue
        text = read(path)
        rel = os.path.relpath(path, root)
        engines |= set(re.findall(r"browserName\s*:\s*['\"](chromium|firefox|webkit)['\"]", text))
        engines |= set(re.findall(r"name\s*:\s*['\"](?:[\w-]*-)?(chromium|firefox|webkit)['\"]", text))
        for device, engine in DEVICES.items():
            if re.search(r"devices\[\s*['\"]" + re.escape(device), text):
                engines.add(engine)
        if re.search(r"channel\s*:\s*['\"]chromium['\"]", text):
            full_reasons.append(f"{rel} uses channel 'chromium'")
        if re.search(r"headless\s*:\s*false", text):
            full_reasons.append(f"{rel} sets headless: false")
    return engines, full_reasons


def jobs(text):
    """Split a workflow into (job name, start line, lines) by indentation under ``jobs:``."""
    lines = text.splitlines()
    out, current, start, inside = [], None, 0, False
    for number, line in enumerate(lines, 1):
        if re.match(r"^jobs\s*:", line):
            inside = True
            continue
        match = re.match(r"^  ([A-Za-z0-9_-]+)\s*:", line) if inside else None
        if match:
            if current:
                out.append((current, start, lines[start - 1:number - 1]))
            current, start = match.group(1), number
    if current:
        out.append((current, start, lines[start - 1:]))
    return out


def chromium_modes(after):
    """Return whether later commands launch bundled Chromium headed and/or headless.

    ``for name in ... chromium ...`` makes ``$name`` a Chromium launch. A later
    ``for`` of the same name replaces that set. ``--channel`` is a branded browser.
    """
    chromium_vars = {}
    for line in after.splitlines():
        match = re.search(r"\bfor\s+(\w+)\s+in\s+([^;\n]+)", line)
        if match:
            chromium_vars[match.group(1)] = bool(re.search(r"\bchromium\b", match.group(2)))
    full = shell = False
    for line in after.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for target in re.findall(r"--browser(?:=|\s+)[\"']?\$?(\w+)", line):
            if target != "chromium" and not chromium_vars.get(target):
                continue
            if re.search(r"--channel\b", line):
                continue
            if re.search(r"--headed\b", line):
                full = True
            else:
                shell = True
    return full, shell


def plan(root):
    engines, full_reasons = config_facts(root)
    rows = []
    workflows = os.path.join(root, ".github", "workflows")
    names = sorted(n for n in os.listdir(workflows) if n.endswith((".yml", ".yaml"))) if os.path.isdir(workflows) else []
    for name in names:
        rel = os.path.join(".github", "workflows", name)
        for job, start, body in jobs(read(os.path.join(workflows, name))):
            for offset, line in enumerate(body):
                match = INSTALL.search(line)
                if not match or line.lstrip().startswith("#"):
                    continue
                words = match.group(1).split()
                flags = {w for w in words if w.startswith("-")}
                targets = {w for w in words if not w.startswith("-")}
                where = f"{rel}:{start + offset}"
                after = "\n".join(body[offset + 1:])
                if targets - {"chromium", "firefox", "webkit"} or "chromium" not in targets:
                    if engines and targets & {"firefox", "webkit"} - engines:
                        rows.append(("warn", where, f"installs {', '.join(sorted(targets & {'firefox', 'webkit'} - engines))} but no config project uses it"))
                    continue
                uses_config = bool(re.search(r"playwright\s+test\b|npm\s+(?:run\s+)?test|npx\s+playwright\s+test", after))
                uses_cli = bool(re.search(r"playwright\s+(?:screenshot|pdf)\b", after))
                headed = bool(re.search(r"--headed\b", after))
                _launch_full, launch_shell = chromium_modes(after)
                needs_full = _launch_full or headed or (uses_config and bool(full_reasons))
                needs_shell = launch_shell or uses_cli or not (uses_config and full_reasons) and not headed
                if needs_full and needs_shell:
                    want, why = None, "job uses both the full build and the shell"
                elif needs_full:
                    want, why = "--no-shell", "; ".join(full_reasons) if not headed else "job runs --headed"
                else:
                    want, why = "--only-shell", "job runs headless only"
                have = "--only-shell" if "--only-shell" in flags else ("--no-shell" if "--no-shell" in flags else None)
                if want == have:
                    rows.append(("ok", where, f"{have or 'full + shell'} ({why})"))
                elif want is None:
                    rows.append(("suggest", where, f"remove {have} ({why})"))
                else:
                    rows.append(("suggest", where, f"{'replace ' + have + ' with' if have else 'add'} {want} ({why})"))
                if engines and targets & {"firefox", "webkit"} - engines:
                    rows.append(("warn", where, f"installs {', '.join(sorted(targets & {'firefox', 'webkit'} - engines))} but no config project uses it"))
    return rows


def plan_cli(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Recommend the lightest playwright install for each workflow job.")
    parser.add_argument("root", nargs="?", default=".", help="checkout to inspect (default: .)")
    root = parser.parse_args(argv).root
    if not os.path.isdir(root):
        print(f"btk.py plan: not a directory: {root}", file=sys.stderr)
        return 2
    rows = plan(root)
    for state, where, text in rows:
        print(f"{state:7} {where} {text}")
    if not rows:
        print("no playwright install lines in .github/workflows")
    return 1 if any(state == "suggest" for state, _, _ in rows) else 0


# ---- page inspection -------------------------------------------------------------------


SCHEMA_INSPECT = "btk-page-inspect/1"
SCHEMA_OBSERVE = "btk-page-observe/1"
SCHEMA_COMPARE = "btk-page-compare/1"
SCHEMA_RUN = "btk-page-observe-run/1"
COMPARE_ENVIRONMENT_KEYS = ("os", "browser", "profile", "headless", "viewport", "emulation", "has_touch", "real_device")
COMPLETE_COVERAGE = {"complete", "complete_within_window"}
INTERACTIVE_ROLES = {
    "button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox",
    "slider", "spinbutton", "switch", "tab", "menuitem",
}
STEP_ACTIONS = {
    "click", "dblclick", "rightclick", "hover", "drag", "scroll", "scroll-into-view",
    "type", "fill", "clear", "press", "shortcut", "focus", "blur",
    "check", "uncheck", "select", "set-files", "submit",
    "goto", "back", "forward", "reload", "wait", "resize", "settle",
    "tap", "long-press", "swipe", "touch-scroll",
}
ACT_ACTIONS = set(STEP_ACTIONS) | {"wait_for_timeout"}
VALUE_ACTIONS = {"type", "fill", "select"}
LOCATOR_ACTIONS = {
    "click", "dblclick", "rightclick", "hover", "scroll-into-view", "clear",
    "focus", "blur", "check", "uncheck", "submit", "tap", "long-press", "swipe", "touch-scroll",
}
TAP_NEEDS_TOUCH = "tap needs a hasTouch context; a mouse click was not substituted"
KEY_MODIFIERS = {"Primary", "Control", "Ctrl", "Meta", "Command", "Alt", "Shift", "ControlOrMeta"}
PAGE_LOAD_STATES = {"load", "domcontentloaded", "networkidle", "commit"}
LOCATOR_STATES = {"attached", "detached", "visible", "hidden"}
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VIEWPORT_RE = re.compile(r"^([1-9][0-9]{0,4})x([1-9][0-9]{0,4})$")
MAX_STEPS = 64
ACT_TEXT_ACTIONS = {"fill", "press", "select", "goto"}
MAX_VALUE_CHARS = 65536
# Stagehand quietWait (packages/integrations/core/src/facade/runtime.ts): past "loading",
# then 400 ms with no DOM mutation, polled every 100 ms. A timeout is quiet:false.
QUIET_MS = 400
QUIET_POLL_S = 0.1
SETTLE_UNSUPPORTED = (
    "settle waits until readyState is past loading and the main document has not mutated for 400 ms, "
    "using Chromium CDP Page lifecycle events and DOM mutation events. "
    "This engine has no CDP DOM mutation stream, and a page script was not substituted"
)
NAVIGATION_TIMEOUT_MS = 30000
POPUP_EVENT_TIMEOUT_MS = 1000
SCROLL_SETTLE_MS = 200
LONG_PRESS_DEFAULT_MS = 700
LONG_PRESS_MIN_MS = 500
LONG_PRESS_MAX_MS = 3000
SWIPE_DEFAULT_DX = 160
SWIPE_DEFAULT_DY = 0
TOUCH_SCROLL_DEFAULT_DX = 0
TOUCH_SCROLL_DEFAULT_DY = 400
GESTURE_POINT_PX = 20
GESTURE_MIN_STEPS = 2
GESTURE_MAX_STEPS = 30
TOUCH_MOVE_PAUSE_S = 0.016
TOUCH_DWELL_S = 0.12
TOUCH_STROKE_MARGIN = 8
TOUCH_SCROLL_PASSES = 8
TOUCH_GESTURES = {"long-press", "swipe", "touch-scroll"}
TOUCH_ENGINE_LIMIT = (
    "Firefox Page.dispatchTapEvent and WebKit Input.dispatchTapEvent are a point tap with no hold, move, or touch scroll, "
    "and those engines have no CDP Input session. A mouse event was not substituted. "
    "Chromium uses Input.synthesizeTapGesture for long-press and Input.dispatchTouchEvent for swipe and touch-scroll when hasTouch is set."
)
_secrets: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar("page_inspect_secrets", default=())
HARD_CAP = 500
SURVEY_LIMIT_DEFAULT = 50
SURVEY_LIMIT_MAX = 200
PAGE_LIMIT_DEFAULT = 20
PAGE_LIMIT_MAX = 100
CONTEXT_DEFAULT = 1
CONTEXT_MAX = 5
SUGGESTIONS = 3
THRESHOLD_DEFAULT = 1.0
SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
PROFILES = {"desktop": None, "iphone": "iPhone 13", "pixel": "Pixel 7"}
DESKTOP_VIEWPORT = {"width": 1280, "height": 800}
_ROLE_ADDRESS = re.compile(r"^([^/]*)/([^#]*)#(\d+)$")
_ROLE_EQ = re.compile(r'^role=([A-Za-z0-9_-]+)\[name="([^"]*)"\](?:#(\d+))?$')
_GET_BY_ROLE = re.compile(
    r"""getByRole\(\s*['"]([A-Za-z0-9_-]+)['"]\s*,\s*\{\s*name\s*:\s*['"]([^'"]*)['"]\s*\}\s*\)$"""
)
_QUOTED_ROLE = re.compile(r'^([A-Za-z0-9_-]+)\s+"([^"]*)"$')
_HASH_ID = re.compile(r"^#([A-Za-z_][A-Za-z0-9_-]*)$")
_TESTID_ATTR = re.compile(r'^\[data-testid="([^"]+)"\]$')
_EQ_ID = re.compile(r"^(id|testid)=([A-Za-z_][A-Za-z0-9_-]*)$")


class InspectError(Exception):
    def __init__(self, error: str, hint: str, *, stage: str = "input", suggestions: list | None = None, code: int = 2):
        super().__init__(error)
        self.error = error
        self.hint = hint
        self.stage = stage
        self.suggestions = suggestions or []
        self.code = code


def utf16_units(text: str) -> tuple[int, ...]:
    """UTF-16 code units, including unpaired surrogates that str.encode rejects."""
    units = []
    for char in text:
        code = ord(char)
        if code > 0xFFFF:
            code -= 0x10000
            units.append(0xD800 + (code >> 10))
            units.append(0xDC00 + (code & 0x3FF))
        else:
            units.append(code)
    return tuple(units)


def unit_len(text: str) -> int:
    return len(utf16_units(text))


def lcs_length(left: str, right: str) -> int:
    """Longest contiguous shared slice, after simple lowercasing, in UTF-16 units."""
    first = utf16_units(left.lower())
    second = utf16_units(right.lower())
    if not first or not second:
        return 0
    previous = [0] * (len(second) + 1)
    best = 0
    for unit in first:
        current = [0] * (len(second) + 1)
        for index, other in enumerate(second, 1):
            if unit == other:
                current[index] = previous[index - 1] + 1
                if current[index] > best:
                    best = current[index]
        previous = current
    return best


def round6(value: float) -> float:
    return math.floor(value * 1000000 + 0.5) / 1000000


def similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    first, second = left.lower(), right.lower()
    return round6(lcs_length(first, second) / min(unit_len(first), unit_len(second)))


def _unpaired_surrogate(units: tuple[int, ...]) -> bool:
    index = 0
    while index < len(units):
        unit = units[index]
        if 0xD800 <= unit <= 0xDBFF:
            if index + 1 < len(units) and 0xDC00 <= units[index + 1] <= 0xDFFF:
                index += 2
                continue
            return True
        if 0xDC00 <= unit <= 0xDFFF:
            return True
        index += 1
    return False


def storage_fingerprint(value: str) -> dict:
    """SHA-256 of a storage value. The value itself is not returned."""
    if not isinstance(value, str):
        raise InspectError("storage value must be a string", "fingerprint the host string, then drop the value")
    units = utf16_units(value)
    if _unpaired_surrogate(units):
        raw = bytes([0xFF]) + b"".join(unit.to_bytes(2, "little") for unit in units)
        encoding = "utf16le-unpaired"
    else:
        raw = value.encode("utf-8")
        encoding = "utf8"
    return {"fingerprint_version": 2, "encoding": encoding, "sha256": hashlib.sha256(raw).hexdigest()}


def evidence_relation(left, right) -> str:
    """Same, different, or unknown. Missing, truncated, and unrequested coverage is never same."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return "unknown"
    if left.get("coverage") not in COMPLETE_COVERAGE or right.get("coverage") not in COMPLETE_COVERAGE:
        return "unknown"
    left_digest = left.get("digest")
    right_digest = right.get("digest")
    if not isinstance(left_digest, str) or not isinstance(right_digest, str) or not left_digest or not right_digest:
        return "unknown"
    return "same" if left_digest == right_digest else "different"


def loopback_target(target: str) -> str:
    """Accept only http(s) on 127.0.0.1 or ::1 with an explicit port."""
    if not isinstance(target, str) or not (target.startswith("http://") or target.startswith("https://")):
        raise InspectError(
            "target must be an http or https loopback URL",
            "hostnames, file URLs, and other schemes are rejected",
        )
    try:
        parts = urllib.parse.urlsplit(target)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise InspectError(
            "target is not a loopback URL",
            "use http://127.0.0.1:PORT/path or http://[::1]:PORT/path",
        ) from exc
    if parts.username or parts.password:
        raise InspectError(
            "URL must not include credentials",
            "use http://127.0.0.1:PORT/path with no user or password",
        )
    if host not in {"127.0.0.1", "::1"}:
        raise InspectError(
            "target host must be a loopback address",
            "use 127.0.0.1 or [::1] with an explicit port; localhost and other hosts are rejected",
        )
    if port is None:
        raise InspectError(
            "target needs an explicit port",
            "write the port; implicit 80 and 443 are rejected",
        )
    return target


def _secret_needles() -> list[str]:
    needles = []
    for secret in _secrets.get():
        if not isinstance(secret, str) or secret == "":
            continue
        for form in (secret, urllib.parse.quote(secret, safe=""), urllib.parse.quote_plus(secret)):
            if form and form not in needles:
                needles.append(form)
    needles.sort(key=len, reverse=True)
    return needles


# Stagehand sanitizeErrorMessage (browserbase/stagehand
# packages/integrations/core/src/harness/redact.ts, MIT). Exact env:/file: values
# are replaced first, so a known secret is removed in full rather than leaving a key prefix.
KEY_REDACTIONS = (
    (re.compile(r"([?&](?:signingKey|apiKey|api_key|token|key)=)[^&\s\"']+", re.IGNORECASE), r"\1[redacted]"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+"), r"\1[redacted]"),
    (re.compile(r"\b(bb_(?:live|test)_[A-Za-z0-9]{4})[A-Za-z0-9_-]+"), r"\1[redacted]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "AIza[redacted]"),
    (re.compile(r"\b(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE), r"\1[redacted]"),
)


def redact_text(value: str) -> str:
    """Replace typed values, their percent-encoded forms, and key-like strings."""
    if not isinstance(value, str) or not value:
        return value
    for needle in _secret_needles():
        if needle in value:
            value = value.replace(needle, "[redacted]")
    for pattern, replacement in KEY_REDACTIONS:
        value = pattern.sub(replacement, value)
    return re.sub(r"https?://[^\s\"'<>]+", lambda match: strip_userinfo(match.group()), value)


def redact_obj(value):
    """Walk a JSON-like tree and redact string values. Object keys stay stable."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_obj(item) for key, item in value.items()}
    return value


class bound_secrets:
    """Hold typed values for redaction and drop them when the session ends."""

    def __init__(self, values: list[str]):
        self._values = tuple(item for item in values if isinstance(item, str))
        self._token = None

    def __enter__(self):
        self._token = _secrets.set(self._values)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._token is not None:
            _secrets.reset(self._token)
        return False


def clip(value: str, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", redact_text(value or "")).strip()[:limit]


def os_name() -> str:
    return {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(platform.system(), platform.system())


def _require_str(item: dict, key: str) -> str:
    value = item.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InspectError(f"{key} must be a string", "the observe payload was modified; run observe again", stage="inspect")
    return value


def _require_bool(item: dict, key: str, default: bool | None = None) -> bool:
    if key not in item and default is not None:
        return default
    value = item.get(key)
    if not isinstance(value, bool):
        raise InspectError(f"{key} must be a boolean", "the observe payload was modified; run observe again", stage="inspect")
    return value


def _box(item: dict) -> dict:
    raw = item.get("box")
    if not isinstance(raw, dict):
        raise InspectError("box must be an object", "the observe payload was modified; run observe again", stage="inspect")
    box = {}
    for key in ("x", "y", "width", "height"):
        value = raw.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise InspectError(f"box.{key} must be a finite number", "the observe payload was modified; run observe again", stage="inspect")
        box[key] = int(math.floor(float(value)))
    return box


def encode_address(value: str) -> str:
    """Percent-encode like encodeURIComponent, so the Node twin builds the same address."""
    return urllib.parse.quote(value, safe="!'()*")


def _role_address(role: str, name: str, nth: int) -> str:
    return f"role:{encode_address(role)}/{encode_address(name)}#{nth}"


def _locator(kind: str, selector: str, unique: bool, nth: int | None) -> dict:
    return {"kind": kind, "selector": selector, "unique": unique, "nth": nth}


def normalize_elements(raw: dict, *, start: int = 0, limit: int = SURVEY_LIMIT_DEFAULT) -> dict:
    """Turn collected controls into addressed elements and one page of them."""
    if not isinstance(raw, dict) or not isinstance(raw.get("elements"), list):
        raise InspectError("observe payload has no elements list", "run observe again", stage="inspect")
    _check_page(start, limit, SURVEY_LIMIT_MAX)
    prepared = []
    counts: dict[tuple[str, str], int] = {}
    nths = []
    for item in raw["elements"]:
        if not isinstance(item, dict):
            raise InspectError("observe element must be an object", "run observe again", stage="inspect")
        role = clip(_require_str(item, "role"), 40) or "generic"
        name = clip(_require_str(item, "name"))
        key = (role, name)
        nths.append(counts.get(key, 0))
        counts[key] = counts.get(key, 0) + 1
        prepared.append((item, role, name))
    built = []
    for index, ((item, role, name), nth) in enumerate(zip(prepared, nths)):
        element_id = clip(_require_str(item, "id"), 200)
        testid = clip(_require_str(item, "testid"), 200)
        css = _require_str(item, "css")
        id_unique = _require_bool(item, "id_unique", False) and bool(SAFE.fullmatch(element_id))
        testid_unique = _require_bool(item, "testid_unique", False) and bool(SAFE.fullmatch(testid))
        css_unique = _require_bool(item, "css_unique", False) and bool(css)
        locators = []
        if id_unique:
            locators.append(_locator("id", "#" + element_id, True, None))
        if testid_unique:
            locators.append(_locator("testid", f'[data-testid="{testid}"]', True, None))
        role_safe = role not in {"", "generic"} and name and not any(char in name for char in '"\\]\n')
        if role_safe:
            locators.append(_locator("role", f'role={role}[name="{name}"]', counts[(role, name)] == 1, nth))
        if css:
            locators.append(_locator("css", css, css_unique, None))
        if id_unique:
            address = f"id:{element_id}"
        elif testid_unique:
            address = f"testid:{testid}"
        elif role_safe:
            address = _role_address(role, name, nth)
        elif css:
            address = "css:" + css
        else:
            address = f"h:{index}"
        built.append({
            "handle": f"h:{index}",
            "index": index,
            "address": address,
            "tag": clip(_require_str(item, "tag"), 40),
            "role": role,
            "name": name,
            "text": clip(_require_str(item, "text")),
            "id": element_id,
            "testid": testid,
            "href": _require_str(item, "href")[:500],
            "visible": _require_bool(item, "visible"),
            "enabled": _require_bool(item, "enabled"),
            "in_viewport": _require_bool(item, "in_viewport"),
            "obscured": _require_bool(item, "obscured"),
            "box": _box(item),
            "parent_tag": clip(_require_str(item, "parent_tag"), 40),
            "locators": locators,
        })
    reported = raw.get("total", len(built))
    if isinstance(reported, bool) or not isinstance(reported, int) or reported < len(built):
        reported = len(built)
    page = [element for element in built if start <= element["index"] < start + limit]
    truncated = bool(raw.get("truncated")) or len(built) < reported or start + len(page) < len(built) or start + len(page) < reported
    return {"elements": page, "total": reported, "truncated": truncated, "start": start, "limit": limit, "collected": len(built)}


def _check_page(start: int, limit: int, limit_max: int) -> None:
    if type(start) is not int or start < 0 or start > 100000:
        raise InspectError("start must be an integer from 0 to 100000", "pass --start as a whole number")
    if type(limit) is not int or limit < 1 or limit > limit_max:
        raise InspectError(f"limit must be an integer from 1 to {limit_max}", "lower --limit and page with --start")


def _check_threshold(threshold: float) -> None:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(float(threshold)):
        raise InspectError("threshold must be a number from 0 to 1", "pass --threshold 1 for containment, or a lower score")
    if not 0 <= float(threshold) <= 1:
        raise InspectError("threshold must be a number from 0 to 1", "pass --threshold 1 for containment, or a lower score")


def field_score(element: dict, query: str) -> tuple[float, str | None]:
    best = 0.0
    matched = None
    for key in ("name", "text", "id", "testid", "role"):
        value = element.get(key) or ""
        if not value:
            continue
        score = similarity(query, value)
        if score > best:
            best = score
            matched = key
    return best, matched


def _role_ok(element: dict, role: str) -> bool:
    return not role or str(element.get("role") or "").lower() == role.lower()


def suggest(elements: list, query: str, limit: int = SUGGESTIONS) -> list:
    if not query:
        return []
    ranked = []
    for element in elements:
        score, _matched = field_score(element, query)
        if score > 0:
            ranked.append((score, element))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].get("index", 0)))
    return [{
        "address": element["address"], "handle": element["handle"], "name": element.get("name") or "",
        "role": element.get("role") or "", "score": score,
    } for score, element in ranked[:limit]]


def _view(element: dict) -> dict:
    item = dict(element)
    item.setdefault("role", "")
    item.setdefault("name", "")
    item.setdefault("text", "")
    item.setdefault("id", "")
    item.setdefault("testid", "")
    if type(item.get("index")) is not int:
        item["index"] = 0
    return item


def locate(elements: list, *, query: str = "", role: str = "", threshold: float = THRESHOLD_DEFAULT,
           start: int = 0, limit: int = PAGE_LIMIT_DEFAULT, unique: bool = False) -> dict:
    _check_page(start, limit, PAGE_LIMIT_MAX)
    _check_threshold(threshold)
    chosen = [_view(element) for element in elements if _role_ok(element, role)]
    if query:
        scored = []
        for element in chosen:
            score, matched = field_score(element, query)
            if score + 1e-9 >= float(threshold):
                item = dict(element)
                item["score"] = score
                item["matched"] = matched
                scored.append(item)
        query_lower = query.lower()
        scored.sort(key=lambda element: (
            -element["score"],
            0 if (element.get("name") or "").lower() == query_lower else 1,
            unit_len(element.get("name") or ""),
            element["index"],
        ))
    else:
        scored = []
        for element in chosen:
            item = dict(element)
            item["score"] = None
            item["matched"] = None
            scored.append(item)
    total = len(scored)
    page = scored[start:start + limit]
    next_start = start + len(page)
    if next_start >= total:
        next_start = None
    payload = {
        "schema": SCHEMA_INSPECT,
        "ok": True,
        "operation": "locate",
        "pagination": {"start": start, "limit": limit, "returned": len(page), "total": total, "next_start": next_start},
        "items": page,
        "hint": _locate_hint(query, unique, total, next_start),
    }
    if query and total == 0:
        payload.update({
            "ok": False,
            "stage": "input",
            "error": "no element matches",
            "hint": "lower --threshold, or reuse a suggest address. count lists role totals without element bodies",
            "suggestions": suggest(chosen if role else elements, query),
        })
        return payload
    if unique and query and total:
        top = scored[0]["score"]
        tied = [element for element in scored if element["score"] == top]
        if len(tied) > 1:
            shown = tied[:limit]
            payload.update({
                "ok": False,
                "stage": "input",
                "error": "more than one element shares the top score",
                "hint": "pass one address to describe; do not guess",
                "items": shown,
                "suggestions": suggest_from(tied),
            })
            payload["pagination"] = {
                "start": 0, "limit": limit, "returned": len(shown), "total": len(tied), "next_start": None,
            }
            return payload
        payload["items"] = scored[:1]
        payload["pagination"] = {"start": 0, "limit": limit, "returned": 1, "total": 1, "next_start": None}
        payload["hint"] = "unique top score; lower scores were omitted. Omit --unique to see the whole page"
    return payload


def suggest_from(elements: list) -> list:
    return [{
        "address": element["address"], "handle": element["handle"], "name": element.get("name") or "",
        "role": element.get("role") or "", "score": element.get("score"),
    } for element in elements[:SUGGESTIONS]]


def _locate_hint(query: str, unique: bool, total: int, next_start: int | None) -> str:
    if not query:
        extra = f" Pass --start {next_start} for the next page." if next_start is not None else ""
        return "pass --query to rank by longest-common-substring similarity." + extra
    if next_start is not None:
        return f"showing a page of {total}. Pass --start {next_start} for the next page, or count for totals only"
    return f"{total} match(es). describe one address to read a single control"


def count_elements(elements: list, *, query: str = "", role: str = "", threshold: float = THRESHOLD_DEFAULT) -> dict:
    _check_threshold(threshold)
    chosen = [_view(element) for element in elements if _role_ok(element, role)]
    if query:
        matched = []
        for element in chosen:
            score, _field = field_score(element, query)
            if score + 1e-9 >= float(threshold):
                matched.append(element)
        chosen = matched
    by_role: dict[str, int] = {}
    visible = enabled = 0
    for element in chosen:
        key = element.get("role") or ""
        by_role[key] = by_role.get(key, 0) + 1
        visible += 1 if element.get("visible") else 0
        enabled += 1 if element.get("enabled") else 0
    return {
        "schema": SCHEMA_INSPECT,
        "ok": True,
        "operation": "count",
        "total": len(chosen),
        "visible": visible,
        "enabled": enabled,
        "by_role": {key: by_role[key] for key in sorted(by_role)},
        "hint": "locate --start 0 --limit 20 returns one page of controls; count does not",
    }


def decode_address(value: str) -> str:
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise InspectError("role address has a bad percent escape", "copy the address from observe or locate")
    return urllib.parse.unquote(value)


def _split_role_address(value: str) -> tuple[str, str, int]:
    match = _ROLE_ADDRESS.fullmatch(value)
    if not match:
        raise InspectError("role address must look like role:button/Save%20draft#0", "copy an address from locate or observe")
    role, name, nth = match.groups()
    return decode_address(role), decode_address(name), int(nth)


def stored_nth(elements: list, element: dict) -> int:
    for locator in element.get("locators") or []:
        if locator.get("kind") == "role" and type(locator.get("nth")) is int:
            return locator["nth"]
    return _role_nth(elements, element)


def _by_role_name(elements: list, role: str, name: str, nth: int | None) -> list[tuple[dict, str]]:
    found = []
    for element in elements:
        if (element.get("role") or "").lower() == role.lower() and (element.get("name") or "") == name:
            if nth is None or stored_nth(elements, element) == nth:
                found.append((element, "role"))
    return found


def _role_nth(elements: list, element: dict) -> int:
    nth = 0
    for other in elements:
        if other is element or other.get("handle") == element.get("handle"):
            return nth
        if (other.get("role"), other.get("name")) == (element.get("role"), element.get("name")):
            nth += 1
    return nth


def resolve_target(elements: list, target: str) -> dict:
    text = (target or "").strip()
    if not text:
        raise InspectError("target is empty", "pass an address from locate, such as id:save-draft")
    if re.fullmatch(r"0x[0-9A-Fa-f]+", text):
        raise InspectError(
            "memory addresses are not element addresses",
            "use id:, testid:, role:, css:, or a snapshot h:N handle",
        )
    views = [_view(element) for element in elements]
    kind, matches = _match_target(views, text)
    if len(matches) == 1:
        return {"ok": True, "element": matches[0], "resolved_as": kind}
    if len(matches) > 1:
        return {
            "schema": SCHEMA_INSPECT, "ok": False, "operation": "describe", "stage": "input",
            "error": "target matches more than one element",
            "hint": "pass one full address, including the role nth",
            "suggestions": [{
                "address": element["address"], "handle": element["handle"], "name": element.get("name") or "",
                "role": element.get("role") or "", "score": None,
            } for element in matches[:SUGGESTIONS]],
        }
    folded = _casefold_name(views, text) if kind == "bare" else []
    if len(folded) == 1:
        return {"ok": True, "element": folded[0], "resolved_as": "name-casefold"}
    return {
        "schema": SCHEMA_INSPECT, "ok": False, "operation": "describe", "stage": "input",
        "error": "target is not in this snapshot",
        "hint": "h:N is valid only in the snapshot that minted it; reuse an address or run observe again",
        "suggestions": suggest(views, text),
    }


def _casefold_name(elements: list, text: str) -> list:
    lowered = text.lower()
    return [element for element in elements if (element.get("name") or "").lower() == lowered or (element.get("id") or "").lower() == lowered]


def _match_target(elements: list, text: str) -> tuple[str, list]:
    if text.startswith("h:"):
        return "handle", [element for element in elements if element.get("handle") == text]
    if text.startswith("id:"):
        return "id", [element for element in elements if element.get("id") == text[3:]]
    if text.startswith("testid:"):
        return "testid", [element for element in elements if element.get("testid") == text[7:]]
    if text.startswith("role:"):
        role, name, nth = _split_role_address(text[5:])
        return "role", [element for element, _kind in _by_role_name(elements, role, name, nth)]
    if text.startswith("css:"):
        selector = text[4:]
        return "css", [element for element in elements if element.get("address") == text or _has_selector(element, selector)]
    hash_id = _HASH_ID.fullmatch(text)
    if hash_id:
        return "id", [element for element in elements if element.get("id") == hash_id.group(1)]
    testid = _TESTID_ATTR.fullmatch(text)
    if testid:
        return "testid", [element for element in elements if element.get("testid") == testid.group(1)]
    role_eq = _ROLE_EQ.fullmatch(text)
    if role_eq:
        role, name, nth = role_eq.groups()
        return "role", [element for element, _kind in _by_role_name(elements, role, name, int(nth) if nth is not None else None)]
    getter = _GET_BY_ROLE.fullmatch(text)
    if getter:
        return "role", [element for element, _kind in _by_role_name(elements, getter.group(1), getter.group(2), None)]
    quoted = _QUOTED_ROLE.fullmatch(text)
    if quoted:
        return "role", [element for element, _kind in _by_role_name(elements, quoted.group(1), quoted.group(2), None)]
    equals = _EQ_ID.fullmatch(text)
    if equals:
        key = "id" if equals.group(1) == "id" else "testid"
        return key, [element for element in elements if element.get(key) == equals.group(2)]
    exact = [element for element in elements if element.get("id") == text]
    if exact:
        return "id", exact
    exact = [element for element in elements if element.get("testid") == text]
    if exact:
        return "testid", exact
    exact = [element for element in elements if element.get("address") == text]
    if exact:
        return "address", exact
    return "bare", [element for element in elements if element.get("name") == text]


def _has_selector(element: dict, selector: str) -> bool:
    return any(locator.get("selector") == selector for locator in element.get("locators") or [])


def element_line(element: dict, *, marker: bool = False) -> str:
    score = element.get("score")
    score_text = "" if not isinstance(score, (int, float)) or isinstance(score, bool) else f" score={score:.6f}"
    mark = "* " if marker else ""
    name = element.get("name") or ""
    return f"{mark}{element.get('handle')} {element.get('role')} \"{name}\" {element.get('address')}{score_text}"


def describe_element(elements: list, target: str, *, context: int = CONTEXT_DEFAULT) -> dict:
    if type(context) is not int or not 0 <= context <= CONTEXT_MAX:
        raise InspectError(f"context must be an integer from 0 to {CONTEXT_MAX}", "pass --context 1 for one neighbor on each side")
    resolved = resolve_target(elements, target)
    if not resolved["ok"]:
        resolved["operation"] = "describe"
        return resolved
    element = resolved["element"]
    position = next(index for index, item in enumerate(elements) if item.get("handle") == element.get("handle"))
    lines = [
        element_line(elements[index], marker=index == position)
        for index in range(max(0, position - context), min(len(elements), position + context + 1))
    ]
    return {
        "schema": SCHEMA_INSPECT,
        "ok": True,
        "operation": "describe",
        "resolved_as": resolved["resolved_as"],
        "item": element,
        "context": lines,
        "hint": (
            f"resolved as {resolved['resolved_as']}. Neighbors are adjacent controls in this snapshot, "
            f"not DOM siblings. Reuse {element.get('address')}"
        ),
    }


def operations() -> dict:
    items = [
        {"name": "observe", "needs": "playwright", "summary": "Passive CDP snapshot, or that snapshot before and after each --step",
         "limit_default": SURVEY_LIMIT_DEFAULT, "limit_max": SURVEY_LIMIT_MAX},
        {"name": "scenario", "needs": "playwright", "summary": "Bounded OS/browser environment and fault probes",
         "limit_default": None, "limit_max": None},
        {"name": "act", "needs": "playwright", "summary": "One declared Playwright action that re-queries a locator",
         "limit_default": None, "limit_max": None},
        {"name": "pages", "needs": "playwright", "summary": "Discover same-origin pages from a loopback start and give each an emoji",
         "limit_default": None, "limit_max": None},
        {"name": "count", "needs": "snapshot", "summary": "Count matches and roles without element bodies",
         "limit_default": None, "limit_max": None},
        {"name": "locate", "needs": "snapshot", "summary": "Rank controls by longest-common-substring similarity and page them",
         "limit_default": PAGE_LIMIT_DEFAULT, "limit_max": PAGE_LIMIT_MAX},
        {"name": "describe", "needs": "snapshot", "summary": "Return one control, its locators, and a few neighbor lines",
         "limit_default": CONTEXT_DEFAULT, "limit_max": CONTEXT_MAX},
        {"name": "compare", "needs": "snapshot", "summary": "Compare two observe snapshots by section relation and counts",
         "limit_default": None, "limit_max": None},
        {"name": "operations", "needs": "none", "summary": "List these commands",
         "limit_default": None, "limit_max": None},
    ]
    return {
        "schema": SCHEMA_INSPECT, "ok": True, "operation": "operations", "items": items,
        "actions": [_action_catalog(name) for name in sorted(STEP_ACTIONS)],
        "hint": "observe, locate, and act are separate. count before locate. steps re-query an address and reject h:N. Primary is Playwright ControlOrMeta",
    }


def emulation_label(profile: str) -> dict:
    """Phone profiles are Playwright device descriptors, never a physical device."""
    device = PROFILES[profile]
    if device:
        return {"emulation": True, "emulated_device": device, "real_device": False}
    return {"emulation": False, "emulated_device": None, "real_device": False}


def parse_viewport(text: str) -> dict:
    match = VIEWPORT_RE.fullmatch((text or "").strip())
    if not match:
        raise InspectError("viewport must look like 390x844", "pass width x height, each from 1 to 99999")
    width, height = int(match.group(1)), int(match.group(2))
    if width > 8192 or height > 8192:
        raise InspectError("viewport is larger than 8192 on one side", "pass a smaller width x height")
    return {"width": width, "height": height}


def viewport_source_for(profile: str, headed: bool, viewport_flag: dict | None) -> str:
    if viewport_flag is not None:
        return "flag"
    if PROFILES[profile]:
        return "device_descriptor"
    if headed:
        return "headed_window"
    return "desktop_default"


def resolve_key(key: str) -> str:
    """Map Primary to Playwright's ControlOrMeta. The OS choice stays in Playwright."""
    parts = []
    for part in key.split("+"):
        if part == "Primary":
            parts.append("ControlOrMeta")
        elif part == "Ctrl":
            parts.append("Control")
        elif part == "Command":
            parts.append("Meta")
        else:
            parts.append(part)
    return "+".join(parts)


def _action_catalog(action: str) -> dict:
    support, reason = action_support(action, has_touch=True)
    item = {"name": action, "support": support, "note": reason}
    if action == "tap" or action in TOUCH_GESTURES:
        item["without_has_touch"] = action_support(action, has_touch=False)[0]
    if action == "tap":
        item["note"] = "tap is supported on Chromium, Firefox, and WebKit only when the context hasTouch is true"
    return item


def action_support(action: str, *, has_touch: bool) -> tuple[dict, str | None]:
    browsers = ("chromium", "firefox", "webkit")
    if action in TOUCH_GESTURES:
        if not has_touch:
            return (
                {name: "unsupported" for name in browsers},
                f"{action} needs a hasTouch context; a mouse event was not substituted",
            )
        return {
            "chromium": "supported",
            "firefox": "unsupported",
            "webkit": "unsupported",
        }, TOUCH_ENGINE_LIMIT
    if action == "tap" and not has_touch:
        return {name: "unsupported" for name in browsers}, TAP_NEEDS_TOUCH
    if action == "settle":
        return {
            "chromium": "supported",
            "firefox": "unsupported",
            "webkit": "unsupported",
        }, SETTLE_UNSUPPORTED
    return {name: "supported" for name in browsers}, None


def _check_key(key: str) -> None:
    parts = (key or "").split("+")
    if not key or len(parts) > 4 or any(not part for part in parts):
        raise InspectError("key must look like Enter or Primary+k", "use Playwright key names, plus Primary for the platform modifier")
    *mods, main = parts
    for mod in mods:
        if mod not in KEY_MODIFIERS:
            raise InspectError(f"unknown modifier {mod}", "use Primary, Control, Alt, Shift, or Meta")
    if main in KEY_MODIFIERS or not re.fullmatch(r"[A-Za-z0-9]+", main):
        raise InspectError("key combination needs a non-modifier key", "for example Primary+k or Enter")


def _read_text_file(path: Path) -> str:
    if not path.is_file():
        raise InspectError("value file is not a file", "pass file:PATH to a UTF-8 text file. Do not put a secret in the command arguments")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InspectError("value file could not be read", "choose a readable UTF-8 text file") from exc
    if len(data) > MAX_VALUE_CHARS:
        raise InspectError("value file is too long", f"the limit is {MAX_VALUE_CHARS} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeError as exc:
        raise InspectError("value file is not UTF-8 text", "type and fill read text. set-files passes a path to Playwright") from exc


def load_action_value(raw: str) -> dict:
    """Read a typed value from the environment or a file. The value is not returned in JSON."""
    if raw.startswith("env:"):
        name = raw[4:]
        if not ENV_NAME.fullmatch(name):
            raise InspectError("environment variable name is not valid", "use env:NAME with a shell-style name")
        if name not in os.environ:
            raise InspectError(
                f"environment variable {name} is not set",
                "export the value in the environment. Do not put a secret in the command arguments",
            )
        value = os.environ[name]
        if len(value) > MAX_VALUE_CHARS:
            raise InspectError("environment value is too long", f"the limit is {MAX_VALUE_CHARS} characters")
        return {"source": "env", "name": name, "value": value}
    if raw.startswith("file:"):
        return {"source": "file", "name": None, "value": _read_text_file(Path(raw[5:]))}
    if len(raw) > MAX_VALUE_CHARS:
        raise InspectError("value is too long", f"the limit is {MAX_VALUE_CHARS} characters. Prefer env:NAME or file:PATH")
    return {"source": "cli", "name": None, "value": raw}


def load_file_path(raw: str) -> dict:
    """Pass a filesystem path to Playwright. File contents are not copied into the result."""
    if raw.startswith("env:"):
        raise InspectError("set-files takes a path", "pass the path, or file:PATH. Contents are not written to the output")
    source = "file" if raw.startswith("file:") else "cli"
    path_text = raw[5:] if raw.startswith("file:") else raw
    path = Path(path_text)
    if not path.is_file():
        raise InspectError("set-files path is not a file", "pass the file path. Contents are not copied into the output")
    return {"source": source, "path": str(path)}


def _parse_signed_pair(text: str, action: str) -> tuple[int, int]:
    coords = (text or "").split(",", 1)
    if len(coords) != 2:
        raise InspectError(f"{action} needs DX,DY", "use two integers separated by a comma")
    try:
        dx = int(coords[0].strip(), 10)
        dy = int(coords[1].strip(), 10)
    except ValueError as exc:
        raise InspectError(f"{action} needs integer DX and DY", f"example: {action}:id:card=0,400") from exc
    if abs(dx) > 20000 or abs(dy) > 20000:
        raise InspectError(f"{action} delta is too large", "keep DX and DY within 20000")
    if dx == 0 and dy == 0:
        raise InspectError(f"{action} delta is zero", "pass a non-zero DX,DY. A mouse event was not substituted")
    return dx, dy


def _parse_long_press_ms(spec: str | None) -> int:
    if spec is None or spec == "":
        return LONG_PRESS_DEFAULT_MS
    if not re.fullmatch(r"[1-9][0-9]*", spec.strip()):
        raise InspectError(
            "long-press duration must be a whole number of milliseconds",
            "example: long-press:id:card=700",
        )
    duration = int(spec.strip())
    if duration < LONG_PRESS_MIN_MS or duration > LONG_PRESS_MAX_MS:
        raise InspectError(
            "long-press duration must be from 500 to 3000 milliseconds",
            "a shorter press is a tap. Pass long-press:LOCATOR=700",
        )
    return duration


def _split_locator_value(rest: str) -> tuple[str, str | None]:
    if rest.startswith("css:"):
        index = rest.rfind("=")
    else:
        index = rest.find("=")
    if index < 0:
        return rest, None
    return rest[:index], rest[index + 1:]


def _require_locator(text: str) -> str:
    locator = (text or "").strip()
    if not locator:
        raise InspectError("this step needs a locator", "pass id:, testid:, role:, or css:")
    if locator.startswith("h:"):
        raise InspectError("a step does not accept a snapshot ordinal", "pass id:, testid:, role:, or css: so the control is queried again")
    if locator.startswith("id:"):
        if not SAFE.fullmatch(locator[3:]):
            raise InspectError("id locator has unsafe characters", "use the id: value from observe")
        return locator
    if locator.startswith("testid:"):
        if not SAFE.fullmatch(locator[7:]):
            raise InspectError("testid locator has unsafe characters", "use the testid: value from observe")
        return locator
    if locator.startswith("css:"):
        if not locator[4:] or "javascript:" in locator.lower():
            raise InspectError("css locator cannot carry a script URL", "pass a CSS selector")
        return locator
    if locator.startswith("role:"):
        _split_role_address(locator[5:])
        return locator
    raise InspectError("locator must be id:, testid:, role:, or css:", "observe prints those addresses; h:N is not accepted")


def _blank_step(action: str) -> dict:
    return {
        "action": action,
        "locator": None,
        "to": None,
        "key": None,
        "url": None,
        "viewport": None,
        "wait": None,
        "secret": None,
        "dx": None,
        "dy": None,
        "value_source": None,
        "value_name": None,
        "select_mode": None,
        "file_path": None,
    }


def parse_step(text: str) -> dict:
    """Parse one ``--step`` token into an action that re-queries locators on each call."""
    raw = (text or "").strip()
    match = re.fullmatch(r"([a-z][a-z0-9-]*)(?::([\s\S]*))?", raw)
    if not match:
        raise InspectError("step must start with an action name", "for example click:id:save or type:id:search=env:SEARCH")
    action, rest = match.group(1), match.group(2)
    if action not in STEP_ACTIONS:
        raise InspectError(
            f"unknown step action {action}",
            "use the stage 1-2 vocabulary: keyboard, mouse, form, page, and touch actions",
        )
    step = _blank_step(action)
    rest = "" if rest is None else rest
    if action in {"back", "forward", "reload", "settle"}:
        if rest:
            raise InspectError(f"{action} does not take an argument", f"pass --step {action}")
        return step
    if action == "goto":
        step["url"] = loopback_target(rest)
        return step
    if action == "resize":
        step["viewport"] = parse_viewport(rest)
        return step
    if action == "scroll":
        coords = rest.split(",", 1)
        if len(coords) != 2:
            raise InspectError("scroll needs scroll:DX,DY", "use two integers separated by a comma")
        try:
            dx = int(coords[0].strip(), 10)
            dy = int(coords[1].strip(), 10)
        except ValueError as exc:
            raise InspectError("scroll needs integer DX and DY", "example: scroll:0,400") from exc
        if abs(dx) > 20000 or abs(dy) > 20000:
            raise InspectError("scroll delta is too large", "keep DX and DY within 20000")
        step["dx"] = dx
        step["dy"] = dy
        return step
    if action == "long-press":
        locator, spec = _split_locator_value(rest)
        step["locator"] = _require_locator(locator)
        step["duration_ms"] = _parse_long_press_ms(spec)
        return step
    if action in TOUCH_GESTURES:
        locator, spec = _split_locator_value(rest)
        step["locator"] = _require_locator(locator)
        if spec is None:
            if action == "swipe":
                step["dx"], step["dy"] = SWIPE_DEFAULT_DX, SWIPE_DEFAULT_DY
            else:
                step["dx"], step["dy"] = TOUCH_SCROLL_DEFAULT_DX, TOUCH_SCROLL_DEFAULT_DY
        else:
            step["dx"], step["dy"] = _parse_signed_pair(spec, action)
        return step
    if action == "wait":
        step["wait"] = _parse_wait(rest)
        if step["wait"]["kind"] == "locator":
            step["locator"] = step["wait"]["locator"]
        return step
    if action in {"press", "shortcut"}:
        if rest.startswith(("id:", "testid:", "role:", "css:")):
            locator, key = _split_locator_value(rest)
            step["locator"] = _require_locator(locator)
            if not key:
                raise InspectError(f"{action} needs a key", "for example press:id:search=Enter or shortcut:Primary+k")
        else:
            key = rest
        _check_key(key)
        step["key"] = key
        return step
    if action == "drag":
        locator, target = _split_locator_value(rest)
        step["locator"] = _require_locator(locator)
        if not target:
            raise InspectError("drag needs a target locator", "for example drag:id:source=id:drop")
        step["to"] = _require_locator(target)
        return step
    if action == "set-files":
        locator, spec = _split_locator_value(rest)
        step["locator"] = _require_locator(locator)
        if spec is None:
            raise InspectError("set-files needs a path", "pass set-files:id:upload=file:PATH. Contents are not copied into the output")
        loaded = load_file_path(spec)
        step["file_path"] = loaded["path"]
        step["value_source"] = loaded["source"]
        step["secret"] = loaded["path"]
        return step
    if action in VALUE_ACTIONS:
        locator, spec = _split_locator_value(rest)
        step["locator"] = _require_locator(locator)
        if spec is None:
            raise InspectError(f"{action} needs a value", "pass =text, =env:NAME, or =file:PATH. The value is redacted in the output")
        mode = "label"
        if action == "select":
            if spec.startswith("label:"):
                mode, spec = "label", spec[6:]
            elif spec.startswith("value:"):
                mode, spec = "value", spec[6:]
        loaded = load_action_value(spec)
        step["select_mode"] = mode if action == "select" else None
        step["secret"] = loaded["value"]
        step["value_source"] = loaded["source"]
        step["value_name"] = loaded.get("name")
        return step
    step["locator"] = _require_locator(rest)
    return step


def _parse_wait(rest: str) -> dict:
    if rest.startswith("timeout:"):
        number = rest[8:]
        if not re.fullmatch(r"[1-9][0-9]*", number):
            raise InspectError("wait timeout must be a positive whole number of milliseconds", "for example wait:timeout:50")
        value = int(number)
        if value > 120000:
            raise InspectError("wait timeout must be at most 120000", "pass a shorter wait:timeout")
        return {"kind": "timeout", "timeout_ms": value}
    if rest in PAGE_LOAD_STATES:
        return {"kind": "load", "state": rest}
    if rest.startswith(("id:", "testid:", "role:", "css:")):
        locator, state = _split_locator_value(rest)
        state = state or "visible"
        if state not in LOCATOR_STATES:
            raise InspectError("wait state must be attached, detached, visible, or hidden", "for example wait:id:results or wait:id:results=hidden")
        return {"kind": "locator", "locator": _require_locator(locator), "state": state}
    raise InspectError(
        "wait needs a locator, a load state, or timeout:N",
        "for example wait:id:results, wait:load, or wait:timeout:50",
    )


def act_arguments_to_step(action: str, locator: str, text: str | None, text_env: str | None,
                          text_file: str | None, to: str | None, timeout_ms: int) -> dict:
    """Build the same step record the observe ``--step`` grammar uses."""
    if action == "wait_for_timeout":
        return parse_step(f"wait:timeout:{timeout_ms}")
    supplied = []
    if text is not None:
        supplied.append(text)
    if text_env:
        supplied.append(f"env:{text_env}")
    if text_file:
        supplied.append(f"file:{text_file}")
    if len(supplied) > 1:
        raise InspectError("pass only one of --text, --text-env, and --text-file", "secrets belong in the environment or a file, and are redacted in the output")
    value = supplied[0] if supplied else None
    if action in {"back", "forward", "reload", "settle"}:
        return parse_step(action)
    if action == "goto":
        if value is None:
            raise InspectError("goto needs a loopback URL", "pass --text with the http://127.0.0.1:PORT/path destination")
        return parse_step(f"goto:{value}")
    if action == "resize":
        if value is None:
            raise InspectError("resize needs WIDTHxHEIGHT", "pass --text 390x844. A viewport is not a physical device")
        return parse_step(f"resize:{value}")
    if action == "drag":
        if not to:
            raise InspectError("drag needs --to", "pass the destination id:, testid:, role:, or css: locator")
        return parse_step(f"drag:{locator}={to}")
    if action in {"press", "shortcut"}:
        if value is None:
            raise InspectError(f"{action} needs a key", "pass --text Enter or --text Primary+k")
        if locator:
            return parse_step(f"{action}:{locator}={value}")
        return parse_step(f"{action}:{value}")
    if action == "wait":
        if locator:
            suffix = f"={value}" if value else ""
            return parse_step(f"wait:{locator}{suffix}")
        if value is None:
            raise InspectError("wait needs a locator, a load state, or a timeout", "pass --locator id:results, or use wait_for_timeout")
        return parse_step(f"wait:{value}")
    if action in VALUE_ACTIONS or action == "set-files":
        if value is None:
            raise InspectError(f"{action} needs a value", "pass --text, --text-env, or --text-file. The value is redacted in the output")
        return parse_step(f"{action}:{locator}={value}")
    if action in TOUCH_GESTURES:
        if not locator:
            raise InspectError(f"{action} needs a locator", "pass id:, testid:, role:, or css:")
        if value is None:
            return parse_step(f"{action}:{locator}")
        return parse_step(f"{action}:{locator}={value}")
    if action not in STEP_ACTIONS:
        raise InspectError(f"unknown action {action}", "use the stage 1-2 vocabulary")
    if not locator:
        raise InspectError(f"{action} needs a locator", "pass id:, testid:, role:, or css:")
    return parse_step(f"{action}:{locator}")


def publish_step(step: dict, outcome: dict, *, url_before: str, url_after: str, index: int) -> dict:
    """Step record safe to embed in JSON. Typed values are not included."""
    record = {
        "index": index,
        "action": step["action"],
        "status": outcome["status"],
        "locator": step.get("locator") or "",
        "url_before": strip_userinfo(url_before),
        "url_after": strip_userinfo(url_after),
        "page_js_called": bool(outcome.get("page_js_called")),
        "support": outcome.get("support") or {},
        "input": outcome.get("input") or "none",
    }
    if step.get("locator"):
        record["target"] = step["locator"]
    elif step["action"] == "scroll" and step.get("dx") is not None:
        record["target"] = f"{step['dx']},{step['dy']}"
    if step.get("to"):
        record["to"] = step["to"]
    if step.get("key"):
        record["key_requested"] = step["key"]
        record["key"] = outcome.get("key_resolved") or resolve_key(step["key"])
        if "Primary" in step["key"].split("+"):
            record["primary"] = "ControlOrMeta"
            record["primary_owner"] = "playwright"
    if step.get("value_source"):
        record["value_recorded"] = False
        record["value_source"] = step["value_source"]
        if step.get("value_name"):
            record["value_name"] = step["value_name"]
        if step.get("secret") is not None:
            record["value_fingerprint"] = storage_fingerprint(step["secret"])["sha256"]
    if step.get("select_mode"):
        record["select_by"] = step["select_mode"]
    if step.get("wait"):
        record["wait"] = {key: value for key, value in step["wait"].items() if key != "locator"}
    if step.get("viewport"):
        record["viewport"] = step["viewport"]
    if step.get("duration_ms") is not None:
        record["duration_ms"] = step["duration_ms"]
    if step["action"] in TOUCH_GESTURES and step.get("dx") is not None:
        record["dx"] = step["dx"]
        record["dy"] = step["dy"]
    if outcome.get("reason"):
        record["reason"] = outcome["reason"]
    if outcome.get("unsupported_reason"):
        record["unsupported_reason"] = outcome["unsupported_reason"]
    if outcome.get("scroll_y") is not None:
        record["scroll_y"] = outcome["scroll_y"]
    if "quiet" in outcome:
        record["quiet"] = bool(outcome["quiet"])
    if step["action"] == "click" and outcome["status"] == "done":
        record["hint"] = "network and console on the following snapshot start after the previous snapshot; query-only and fragment-only navigation is intentionally indistinguishable in saved URLs"
    return record


def _secret_values(step: dict) -> list[str]:
    values = []
    secret = step.get("secret")
    if isinstance(secret, str) and secret:
        values.append(secret)
    path = step.get("file_path")
    if isinstance(path, str) and path:
        values.append(path)
        name = Path(path).name
        if name:
            values.append(name)
    return values


def _wait_quiet(page, client, sent: list, timeout_ms: int) -> bool:
    """True when Chromium went quiet before the timeout. A timeout is a result, not an error.

    readyState past loading comes from Page lifecycle events and Playwright's already-tracked
    domcontentloaded state. DOM mutation events stand in for Stagehand's MutationObserver.
    No page script is installed.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    quiet_s = QUIET_MS / 1000
    state = {"last": time.monotonic(), "ready": False}

    def on_mutation(*_args):
        state["last"] = time.monotonic()

    def on_document(*_args):
        state["ready"] = False
        state["last"] = time.monotonic()

    def on_lifecycle(*args):
        params = args[0] if args else None
        name = str(params.get("name") or "") if isinstance(params, dict) else ""
        if name in {"DOMContentLoaded", "load", "networkAlmostIdle", "networkIdle"}:
            state["ready"] = True

    client.on("DOM.attributeModified", on_mutation)
    client.on("DOM.characterDataModified", on_mutation)
    client.on("DOM.childNodeInserted", on_mutation)
    client.on("DOM.childNodeRemoved", on_mutation)
    client.on("DOM.documentUpdated", on_document)
    client.on("Page.lifecycleEvent", on_lifecycle)
    _cdp_send(client, "Page.enable", {}, sent)
    _cdp_send(client, "Page.setLifecycleEventsEnabled", {"enabled": True}, sent)
    _cdp_send(client, "DOM.enable", {}, sent)
    _cdp_send(client, "DOM.getDocument", {"depth": -1, "pierce": False}, sent)
    state["last"] = time.monotonic()
    while True:
        now = time.monotonic()
        if now >= deadline:
            return False
        if not state["ready"]:
            remaining_ms = max(1, int((deadline - now) * 1000))
            try:
                page.wait_for_load_state("domcontentloaded", timeout=min(50, remaining_ms))
            except PlaywrightTimeout:
                pass
            else:
                state["ready"] = True
        now = time.monotonic()
        if now >= deadline:
            return False
        if state["ready"] and (now - state["last"]) >= quiet_s and (state["last"] + quiet_s) <= deadline:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        page.wait_for_timeout(max(1, int(min(QUIET_POLL_S, remaining) * 1000)))


def _settle_quiet(page, *, cdp_client, cdp_sent: list | None, timeout_ms: int) -> bool:
    """Chromium-only quiet wait. The caller has already rejected other engines."""
    owns = cdp_client is None
    sent = cdp_sent if cdp_sent is not None else []
    client = cdp_client
    if owns:
        try:
            client = page.context.new_cdp_session(page)
        except Exception as exc:
            raise InspectError(
                "settle needs a Chromium CDP session",
                SETTLE_UNSUPPORTED,
                stage="inspect",
            ) from exc
    try:
        return _wait_quiet(page, client, sent, timeout_ms)
    finally:
        if owns:
            try:
                client.detach()
            except Exception:
                pass


def perform_action(page, step: dict, *, timeout_ms: int, has_touch: bool, browser_name: str,
                   cdp_client=None, cdp_sent: list | None = None) -> dict:
    """Run one step with Playwright's own methods. Unsupported actions are not imitated."""
    action = step["action"]
    support, reason = action_support(action, has_touch=has_touch)
    if support.get(browser_name) == "unsupported":
        return {
            "status": "unsupported",
            "support": support,
            "page_js_called": False,
            "input": "touch" if action in {"tap", "long-press", "swipe", "touch-scroll"} else "none",
            "reason": reason,
        }

    def target(locator=None):
        found, _calls_page = _action_locator(page, locator or step["locator"])
        return found

    try:
        if action == "click":
            _call_and_allow_popup(page, lambda: target().click(timeout=timeout_ms))
            return _done(support, "mouse", True)
        if action == "dblclick":
            _call_and_allow_popup(page, lambda: target().dblclick(timeout=timeout_ms))
            return _done(support, "mouse", True)
        if action == "rightclick":
            _call_and_allow_popup(page, lambda: target().click(button="right", timeout=timeout_ms))
            return _done(support, "mouse", True)
        if action == "hover":
            target().hover(timeout=timeout_ms)
            return _done(support, "mouse", True)
        if action == "drag":
            target().drag_to(target(step["to"]), timeout=timeout_ms)
            return _done(support, "mouse", True)
        if action == "scroll":
            page.mouse.wheel(step["dx"], step["dy"])
            page.wait_for_timeout(SCROLL_SETTLE_MS)
            extra = {}
            offsets = _read_visual_offsets(page, cdp_client, cdp_sent)
            if offsets is not None and offsets.get("pageY") is not None:
                extra["scroll_y"] = offsets["pageY"]
            return _done(support, "scroll", False, **extra)
        if action in {"scroll-into-view", "scroll_into"}:
            target().scroll_into_view_if_needed(timeout=timeout_ms)
            page.wait_for_timeout(SCROLL_SETTLE_MS)
            return _done(support, "scroll", True)
        if action == "type":
            target().press_sequentially(step["secret"], timeout=timeout_ms)
            return _done(support, "keyboard", True)
        if action == "fill":
            target().fill(step["secret"], timeout=timeout_ms)
            return _done(support, "keyboard", True)
        if action == "clear":
            target().clear(timeout=timeout_ms)
            return _done(support, "keyboard", True)
        if action in {"press", "shortcut"}:
            resolved = resolve_key(step["key"])
            if step.get("locator"):
                _call_and_allow_popup(page, lambda: target().press(resolved, timeout=timeout_ms))
                page_js = True
            else:
                _call_and_allow_popup(page, lambda: page.keyboard.press(resolved))
                page_js = False
            return _done(support, "keyboard", page_js, key_resolved=resolved)
        if action == "focus":
            target().focus(timeout=timeout_ms)
            return _done(support, "keyboard", True)
        if action == "blur":
            target().blur(timeout=timeout_ms)
            return _done(support, "keyboard", True)
        if action == "check":
            target().check(timeout=timeout_ms)
            return _done(support, "form", True)
        if action == "uncheck":
            target().uncheck(timeout=timeout_ms)
            return _done(support, "form", True)
        if action == "select":
            if step["select_mode"] == "value":
                target().select_option(value=step["secret"], timeout=timeout_ms)
            else:
                target().select_option(label=step["secret"], timeout=timeout_ms)
            return _done(support, "form", True)
        if action == "set-files":
            target().set_input_files(step["file_path"], timeout=timeout_ms)
            return _done(support, "form", True)
        if action == "submit":
            _call_and_allow_popup(
                page,
                lambda: target().locator("input[type='submit'], button[type='submit'], button:not([type])").first.click(timeout=timeout_ms),
            )
            return _done(support, "form", True)
        if action == "goto":
            page.goto(step["url"], wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
            return _done(support, "navigation", False)
        if action == "back":
            _history(page, "back")
            return _done(support, "navigation", False)
        if action == "forward":
            _history(page, "forward")
            return _done(support, "navigation", False)
        if action == "reload":
            page.reload(wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
            return _done(support, "navigation", False)
        if action == "resize":
            page.set_viewport_size(step["viewport"])
            return _done(support, "viewport", False)
        if action == "wait":
            return _perform_wait(page, step, timeout_ms=timeout_ms, support=support)
        if action == "settle":
            quiet = _settle_quiet(
                page, cdp_client=cdp_client, cdp_sent=cdp_sent, timeout_ms=timeout_ms,
            )
            return _done(support, "wait", False, quiet=quiet)
        if action == "tap":
            _call_and_allow_popup(page, lambda: target().tap(timeout=timeout_ms))
            return _done(support, "touch", True)
        if action in TOUCH_GESTURES:
            scroll_y = _perform_chromium_touch(page, step, timeout_ms=timeout_ms)
            extra = {"unsupported_reason": TOUCH_ENGINE_LIMIT}
            if scroll_y is not None:
                extra["scroll_y"] = scroll_y
            return _done(support, "touch", True, **extra)
    except InspectError:
        raise
    except Exception as exc:
        raise InspectError(
            redact_text(f"{type(exc).__name__}: {exc}"),
            "the locator was resolved on this call. If it missed, observe again and pass that address",
            stage="inspect",
        ) from exc
    raise InspectError(f"unknown action {action}", "use the stage 1-2 vocabulary", stage="inspect")


def _call_and_allow_popup(page, fn) -> None:
    """Run fn, then wait until a popup event arrives or the grace passes.

    Playwright's click returns before a target=_blank page exists. expect_popup
    is the standard wait. No popup is a completed action, not a failure.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    try:
        with page.expect_popup(timeout=POPUP_EVENT_TIMEOUT_MS):
            fn()
    except PlaywrightTimeout as exc:
        if 'event "popup"' not in str(exc):
            raise


def _done(support: dict, kind: str, page_js: bool, **extra) -> dict:
    payload = {"status": "done", "support": support, "page_js_called": page_js, "input": kind, "reason": None}
    payload.update(extra)
    return payload


def _history(page, direction: str) -> None:
    before = page.url
    if direction == "back":
        response = page.go_back(wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
        hint = "the page has no earlier history entry"
    else:
        response = page.go_forward(wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
        hint = "the page has no later history entry"
    if response is None and page.url == before:
        raise InspectError(f"cannot go {direction}", hint, stage="navigate")


def _perform_wait(page, step: dict, *, timeout_ms: int, support: dict) -> dict:
    wait = step["wait"]
    if wait["kind"] == "timeout":
        page.wait_for_timeout(wait["timeout_ms"])
        return _done(support, "wait", False)
    if wait["kind"] == "load":
        page.wait_for_load_state(wait["state"], timeout=timeout_ms)
        return _done(support, "wait", False)
    found, _calls_page = _action_locator(page, wait["locator"])
    found.wait_for(state=wait["state"], timeout=timeout_ms)
    return _done(support, "wait", True)


def _watch_pages(page, dialog_policy: str, dialog_items: list, popup_pages: list) -> None:
    """Record dialogs and popups. Accept only when the caller declared that policy."""

    def on_dialog(dialog):
        try:
            message = dialog.message if isinstance(dialog.message, str) else ""
            dialog_items.append({
                "type": dialog.type,
                "message": clip(message, 500),
                "has_default_value": bool(dialog.default_value),
                "action": dialog_policy,
            })
        finally:
            if dialog_policy == "accept":
                if dialog.type == "prompt":
                    dialog.accept("")
                else:
                    dialog.accept()
            else:
                dialog.dismiss()

    def on_page(new_page):
        # Attached after the main page exists, so every later page is a new tab.
        # opener() is null for rel=noopener; those tabs are still recorded.
        popup_pages.append(new_page)
        new_page.on("dialog", on_dialog)

    page.on("dialog", on_dialog)
    page.context.on("page", on_page)


def _popup_rows(pages: list) -> list:
    rows = []
    for popup in pages:
        load = "complete"
        closed = True
        url = ""
        try:
            closed = popup.is_closed()
            if not closed:
                try:
                    popup.wait_for_load_state("load", timeout=5000)
                except Exception:
                    load = "not_settled"
                closed = popup.is_closed()
            url = "" if closed else (popup.url or "")
        except Exception:
            load = "not_settled"
        rows.append({
            "url": strip_userinfo(url),
            "closed": closed,
            "load": load,
            "contents": "not_entered",
        })
    return rows


def _dialog_section(items: list, policy: str) -> dict:
    return _section("playwright:dialog", "complete", {
        "policy": policy,
        "accept_requires_declared_policy": True,
        "items": items,
    })


def _popup_section(pages: list) -> dict:
    rows = _popup_rows(pages)
    return _section("playwright:context.page", "complete", {
        "opened": len(rows),
        "contents": "not_entered",
        "items": rows,
    })


def build_run(observations: list, steps: list, *, dialog_policy: str, error: InspectError | None = None) -> dict:
    """One observe snapshot before the steps and one after each step, compared with the existing relation."""
    comparisons = []
    for left, right in zip(observations, observations[1:]):
        if isinstance(left, dict) and isinstance(right, dict) and left.get("schema") == SCHEMA_OBSERVE and right.get("schema") == SCHEMA_OBSERVE:
            comparisons.append(compare_snapshots(left, right))
    payload = {
        "schema": SCHEMA_RUN,
        "ok": error is None,
        "dialog_policy": dialog_policy,
        "steps": steps,
        "observations": observations,
        "comparisons": comparisons,
        "hint": (
            "observations are btk-page-observe/1 snapshots taken before the steps and after each step. "
            "comparisons reuse btk-page-compare/1 between each pair. Typed values are redacted. "
            "An unsupported step was not performed and was not imitated"
        ),
    }
    if error is not None:
        payload["stage"] = error.stage
        payload["error"] = redact_text(error.error)
        payload["ok"] = False
    return payload


def context_options(devices: dict, browser_name: str, profile: str, headed: bool, viewport: dict | None = None, touch: bool = False) -> dict:
    """Playwright Python context kwargs. Firefox rejects is_mobile on phone descriptors."""
    device = PROFILES[profile]
    if device:
        args = dict(devices[device])
    elif headed:
        args = {"no_viewport": True}
    else:
        args = {"viewport": dict(DESKTOP_VIEWPORT)}
    args.pop("default_browser_type", None)
    if browser_name == "firefox":
        args.pop("is_mobile", None)
    if viewport is not None:
        args.pop("no_viewport", None)
        args["viewport"] = {"width": viewport["width"], "height": viewport["height"]}
    if touch:
        args["has_touch"] = True
    return args


def strip_userinfo(url: str) -> str:
    """Evidence URL only: omit credentials, query and fragment; never use for navigation."""
    try:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = host
        if parts.port is not None:
            netloc = f"{netloc}:{parts.port}"
        return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    except ValueError:
        return "[invalid-url]"


def restrict_request(route):
    """Stay on loopback and do not follow redirects. This does not call the page."""
    try:
        loopback_target(route.request.url)
    except InspectError:
        route.abort("blockedbyclient")
        return
    response = route.fetch(max_redirects=0)
    if 300 <= response.status < 400:
        route.abort("blockedbyclient")
    else:
        route.fulfill(response=response)


def _section(source: str | None, coverage: str, body: dict | None = None) -> dict:
    payload = {"source": source, "coverage": coverage}
    if body:
        payload.update(body)
    if coverage in COMPLETE_COVERAGE:
        canonical = json.dumps(body or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        payload["digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def assemble_observe(raw: dict, *, environment: dict, url: str, start: int, limit: int, sections: dict, limits: dict) -> dict:
    normalized = normalize_elements(raw, start=start, limit=limit)
    hints = [
        "h:N is an ordinal in this snapshot only. act rejects it and queries the address again",
        "a missing or not_requested section is not evidence that the page had nothing",
    ]
    if raw.get("child_frames"):
        hints.append("child frames were counted and their documents were not entered")
    hints.append("DOM attributes are not read inside shadow roots; accessibility names may still include open-shadow controls")
    if normalized["truncated"]:
        hints.append("this snapshot is a page of the observe result; raise --limit or pass --role")
    child_frames = raw.get("child_frames") or 0
    if isinstance(child_frames, bool) or not isinstance(child_frames, int):
        raise InspectError("child_frames must be an integer", "run observe again", stage="inspect")
    return {
        "schema": SCHEMA_OBSERVE,
        "ok": True,
        "url": strip_userinfo(url),
        "title": clip(str(raw.get("title") or ""), 200),
        "environment": environment,
        "limits": limits,
        "sections": sections,
        "child_frames": child_frames,
        "truncated": normalized["truncated"],
        "total": normalized["total"],
        "start": start,
        "limit": limit,
        "hint": ". ".join(hints),
        "elements": normalized["elements"],
    }


def _close_browser(browser):
    if browser is None:
        return None
    try:
        browser.close()
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise InspectError(
            "Playwright is not available in this interpreter",
            "observe and act need the browser extra; count, locate, describe, and operations run under python -S",
            stage="launch",
        ) from exc
    return sync_playwright


def _environment(browser, browser_name, channel, headed, profile, viewport, *, has_touch=False, viewport_source="desktop_default") -> dict:
    viewport = viewport or {}
    label = emulation_label(profile)
    return {
        "os": os_name(),
        "browser": browser_name,
        "version": browser.version,
        "channel": channel,
        "headless": not headed,
        "profile": profile,
        "viewport": {"width": viewport.get("width"), "height": viewport.get("height")},
        "viewport_source": viewport_source,
        "has_touch": bool(has_touch),
        "emulation": label["emulation"],
        "emulated_device": label["emulated_device"],
        "real_device": False,
    }


def _unavailable_sections(reason: str) -> dict:
    body = {"reason": reason}
    return {
        "dom": _section(None, "unavailable", body),
        "accessibility": _section(None, "unavailable", body),
        "scripts": _section(None, "unavailable", body),
        "resources": _section(None, "unavailable", body),
        "network": _section(None, "unavailable", body),
        "console": _section(None, "unavailable", body),
        "storage": _section(None, "not_requested"),
        "frames": _section(None, "unavailable", body),
        "shadow_roots": _section(None, "unavailable", body),
        "dialogs": _section(None, "unavailable", body),
        "popups": _section(None, "unavailable", body),
    }


def _content_digest(items: list) -> str:
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ax_text(field) -> str:
    if isinstance(field, dict) and field.get("value") is not None:
        return redact_text(str(field.get("value")))
    return ""


def _walk_dom(node, found: list) -> bool:
    truncated = False
    if node.get("nodeType") == 1:
        if len(found) >= HARD_CAP:
            return True
        attrs = node.get("attributes") or []
        names = []
        values = {}
        for index in range(0, len(attrs) - 1, 2):
            names.append(str(attrs[index]))
            values[str(attrs[index])] = attrs[index + 1]
        tag = str(node.get("nodeName") or "").lower()
        found.append({
            "node_id": node.get("nodeId"),
            "backend_id": node.get("backendNodeId"),
            "tag": tag,
            "attr_names": names,
            "id": str(values.get("id") or ""),
            "testid": str(values.get("data-testid") or ""),
            "hidden_attr": "hidden" in names or values.get("aria-hidden") == "true",
            "script_src": str(values.get("src") or "") if tag == "script" else "",
            "inline_script": tag == "script" and "src" not in names,
        })
    for child in node.get("children") or []:
        if _walk_dom(child, found):
            truncated = True
            break
    return truncated


def _frame_count(node) -> int:
    return 1 + sum(_frame_count(child) for child in node.get("childFrames") or [])


def frame_rows(frame_tree: dict, *, limit: int = HARD_CAP) -> tuple[list, bool]:
    """Child frames in document order: name, URL, and origin only.

    Frame ids change per load, so they are not listed. Child documents are not entered.
    Ported from the frame list on feat/page-inspect-shadow-roots.
    """
    rows: list[dict] = []
    stack = list(reversed(frame_tree.get("childFrames") or []))
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if len(rows) >= limit:
            return rows, True
        frame = node.get("frame") or {}
        rows.append({
            "name": clip(str(frame.get("name") or ""), 120),
            "url": strip_userinfo(str(frame.get("url") or "")),
            "origin": clip(str(frame.get("securityOrigin") or ""), 200),
        })
        stack.extend(reversed(node.get("childFrames") or []))
    return rows, False


def count_shadow_roots(root: dict, *, limit: int = HARD_CAP * 10) -> dict:
    """Count open and closed shadow roots in a pierced DOM tree. Roots are counted, never entered.

    Walks children and shadowRoots of the main document only; child frame documents are not descended.
    """
    opened = closed = user_agent = visited = 0
    truncated = False
    stack = [root]
    while stack:
        node = stack.pop()
        visited += 1
        if visited > limit:
            truncated = True
            break
        for shadow in node.get("shadowRoots") or []:
            kind = shadow.get("shadowRootType")
            if kind == "user-agent":
                user_agent += 1
            elif kind == "closed":
                closed += 1
            else:
                opened += 1
            stack.append(shadow)
        stack.extend(node.get("children") or [])
    return {"open": opened, "closed": closed, "count": opened + closed, "user_agent": user_agent, "truncated": truncated}


def _controls_from_cdp(dom_nodes, ax_nodes, *, include_hidden: bool, role: str, document_url: str = "") -> tuple[list, dict]:
    by_backend = {node["backend_id"]: node for node in dom_nodes if node.get("backend_id") is not None}
    id_counts: dict[str, int] = {}
    testid_counts: dict[str, int] = {}
    for node in dom_nodes:
        if node["id"]:
            id_counts[node["id"]] = id_counts.get(node["id"], 0) + 1
        if node["testid"]:
            testid_counts[node["testid"]] = testid_counts.get(node["testid"], 0) + 1
    elements = []
    wanted = role.lower()
    for ax in ax_nodes:
        if ax.get("ignored") and not include_hidden:
            continue
        role_name = _ax_text(ax.get("role")).lower()
        if role_name not in INTERACTIVE_ROLES:
            continue
        if wanted and role_name != wanted:
            continue
        dom = by_backend.get(ax.get("backendDOMNodeId"), {})
        if dom.get("hidden_attr") and not include_hidden:
            continue
        disabled = False
        for prop in ax.get("properties") or []:
            if prop.get("name") == "disabled" and isinstance(prop.get("value"), dict) and prop["value"].get("value") is True:
                disabled = True
        element_id = dom.get("id") or ""
        testid = dom.get("testid") or ""
        name = clip(_ax_text(ax.get("name")))
        elements.append({
            "tag": dom.get("tag") or "",
            "role": role_name,
            "name": name,
            "text": name,
            "id": element_id,
            "testid": testid,
            "href": "",
            "visible": not bool(ax.get("ignored") or dom.get("hidden_attr")),
            "enabled": not disabled,
            "in_viewport": True,
            "obscured": False,
            "box": {"x": 0, "y": 0, "width": 0, "height": 0},
            "parent_tag": "",
            "css": f"#{element_id}" if element_id and id_counts.get(element_id) == 1 else "",
            "id_unique": bool(element_id) and id_counts.get(element_id) == 1,
            "testid_unique": bool(testid) and testid_counts.get(testid) == 1,
            "css_unique": bool(element_id) and id_counts.get(element_id) == 1,
        })
    scripts = []
    for node in dom_nodes:
        if node["tag"] != "script":
            continue
        if node["script_src"]:
            # CDP exposes the literal src attribute (often relative); publish
            # a canonical URL without exposing the document's query/fragment.
            script_url = urllib.parse.urljoin(document_url, node["script_src"])
            scripts.append({"kind": "external", "url": strip_userinfo(script_url)})
        elif node["inline_script"]:
            scripts.append({"kind": "inline"})
    return elements, {"scripts": scripts, "dom_nodes": len(dom_nodes), "ax_nodes": len(ax_nodes)}


def _storage_section(client, origin: str, *, keys: bool, fingerprints: bool, sent: list) -> dict:
    if not keys and not fingerprints:
        return _section(None, "not_requested")
    if fingerprints and not keys:
        raise InspectError(
            "storage fingerprints require key names",
            "pass --include-storage-keys with --include-storage-fingerprints",
        )
    try:
        sent.append("DOMStorage.enable")
        client.send("DOMStorage.enable")
        sent.append("DOMStorage.getDOMStorageItems")
        local = client.send("DOMStorage.getDOMStorageItems", {"storageId": {"securityOrigin": origin, "isLocalStorage": True}})
        session = client.send("DOMStorage.getDOMStorageItems", {"storageId": {"securityOrigin": origin, "isLocalStorage": False}})
        sent.append("Network.getCookies")
        cookies = client.send("Network.getCookies")
    except Exception as exc:
        return _section("cdp:DOMStorage", "unavailable", {"reason": type(exc).__name__})
    areas = []
    for kind, rows in (("local", local.get("entries") or []), ("session", session.get("entries") or [])):
        items = []
        for row in rows:
            if not isinstance(row, list) or not row:
                continue
            key = str(row[0])
            item = {"key": key}
            if fingerprints and len(row) > 1:
                item.update(storage_fingerprint(str(row[1])))
            items.append(item)
        areas.append({"area": kind, "items": items})
    cookie_items = []
    for cookie in cookies.get("cookies") or []:
        item = {"key": str(cookie.get("name") or ""), "domain": cookie.get("domain") or "", "path": cookie.get("path") or ""}
        if fingerprints:
            item.update(storage_fingerprint(str(cookie.get("value") or "")))
        cookie_items.append(item)
    areas.append({"area": "cookie", "items": cookie_items})
    return _section("cdp:DOMStorage+Network.getCookies", "complete", {"areas": areas, "values": "redacted"})


def _offsets_from_metrics(metrics) -> dict | None:
    visual = metrics.get("cssVisualViewport") if isinstance(metrics, dict) else None
    if not isinstance(visual, dict):
        return None
    offsets = {}
    for key in ("pageX", "pageY"):
        value = visual.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            offsets[key] = int(round(value))
    return offsets or None


def _read_visual_offsets(page, client, sent: list | None) -> dict | None:
    """Read cssVisualViewport from the observe session when that session exists."""
    if client is not None and sent is not None:
        try:
            metrics = _cdp_send(client, "Page.getLayoutMetrics", {}, sent)
        except InspectError:
            raise
        except Exception:
            return None
        return _offsets_from_metrics(metrics)
    return _css_visual_offsets(page)


def _css_visual_offsets(page) -> dict | None:
    """CSS visual viewport offsets from Page.getLayoutMetrics. No page JavaScript."""
    client = None
    try:
        client = page.context.new_cdp_session(page)
        metrics = client.send("Page.getLayoutMetrics")
    except Exception:
        return None
    finally:
        if client is not None:
            try:
                client.detach()
            except Exception:
                pass
    return _offsets_from_metrics(metrics)


def _layout_offsets(client) -> dict | None:
    try:
        return _offsets_from_metrics(client.send("Page.getLayoutMetrics"))
    except Exception:
        return None


def _dispatch_touch(client, kind: str, x: float | None = None, y: float | None = None) -> None:
    if kind == "touchEnd":
        points: list[dict] = []
    else:
        points = [{"x": int(round(x)), "y": int(round(y)), "id": 1}]
    client.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points, "modifiers": 0})


def _interpolate_points(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    """Stagehand drag path: t = i / steps from the start point to the end point."""
    distance = math.hypot(x1 - x0, y1 - y0)
    steps = max(GESTURE_MIN_STEPS, min(GESTURE_MAX_STEPS, math.ceil(distance / GESTURE_POINT_PX)))
    points = []
    for index in range(1, steps + 1):
        t = index / steps
        points.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return points


def _touch_drag(client, x0: float, y0: float, x1: float, y1: float) -> None:
    """Finger path via Input.dispatchTouchEvent.

    Sleep instead of page.wait_for_timeout. A Playwright wait while the finger
    is down was measured to drop the rest of the gesture on headless Chromium.
    The dwell before touchEnd stops the fling that would scroll past the drag.
    """
    started = False
    try:
        _dispatch_touch(client, "touchStart", x0, y0)
        started = True
        time.sleep(TOUCH_MOVE_PAUSE_S)
        for x, y in _interpolate_points(x0, y0, x1, y1):
            _dispatch_touch(client, "touchMove", x, y)
            time.sleep(TOUCH_MOVE_PAUSE_S)
        _dispatch_touch(client, "touchMove", x1, y1)
        time.sleep(TOUCH_DWELL_S)
    finally:
        if started:
            try:
                _dispatch_touch(client, "touchEnd")
            except Exception:
                pass


def _fit_stroke(origin_x: float, origin_y: float, finger_dx: float, finger_dy: float, viewport: dict) -> tuple[float, float, float, float]:
    """Place the finger path inside the viewport, shifting the start so the stroke is not clipped."""
    margin = TOUCH_STROKE_MARGIN
    max_x = max(margin + 1, viewport["width"] - margin)
    max_y = max(margin + 1, viewport["height"] - margin)

    def clamp_finger(finger: float, limit: float) -> float:
        span = limit - margin
        return max(-span, min(span, finger))

    finger_dx = clamp_finger(finger_dx, max_x)
    finger_dy = clamp_finger(finger_dy, max_y)

    def start_on(origin: float, finger: float, limit: float) -> float:
        low = max(margin, margin - finger)
        high = min(limit, limit - finger)
        if low > high:
            return margin
        if origin < low:
            return low
        if origin > high:
            return high
        return origin

    start_x = start_on(origin_x, finger_dx, max_x)
    start_y = start_on(origin_y, finger_dy, max_y)
    return start_x, start_y, start_x + finger_dx, start_y + finger_dy


def _gesture_origin(page, locator: str, timeout_ms: int, *, bring_into_view: bool) -> tuple[float, float, dict]:
    """Center of the visible box. A missing box is not dispatched at 0,0."""
    found, _calls_page = _action_locator(page, locator)
    if bring_into_view:
        found.scroll_into_view_if_needed(timeout=timeout_ms)
    box = found.bounding_box()
    viewport = page.viewport_size or dict(DESKTOP_VIEWPORT)
    if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
        center_x = box["x"] + box["width"] / 2
        center_y = box["y"] + box["height"] / 2
        inside = 1 <= center_x <= viewport["width"] - 1 and 1 <= center_y <= viewport["height"] - 1
        if inside:
            return center_x, center_y, viewport
    if bring_into_view:
        raise InspectError(
            "element has no visible box",
            "the locator was scrolled into view and still has no positive box inside the viewport. The gesture was not sent at 0,0",
            stage="inspect",
        )
    return viewport["width"] / 2, viewport["height"] / 2, viewport


def _perform_chromium_touch(page, step: dict, *, timeout_ms: int) -> int | None:
    """Chromium CDP touch gestures. Firefox and WebKit never reach this."""
    try:
        client = page.context.new_cdp_session(page)
    except Exception as exc:
        raise InspectError(
            "CDP Input session is not available",
            "Chromium touch gestures need a CDP session",
            stage="inspect",
        ) from exc
    try:
        action = step["action"]
        if action == "long-press":
            x, y, _viewport = _gesture_origin(page, step["locator"], timeout_ms, bring_into_view=True)
            client.send("Input.synthesizeTapGesture", {
                "x": x,
                "y": y,
                "duration": int(step["duration_ms"]),
                "gestureSourceType": "touch",
            })
            return None
        if action == "swipe":
            x, y, viewport = _gesture_origin(page, step["locator"], timeout_ms, bring_into_view=True)
            start_x, start_y, end_x, end_y = _fit_stroke(x, y, step["dx"], step["dy"], viewport)
            if abs(end_x - start_x) < 1 and abs(end_y - start_y) < 1:
                raise InspectError(
                    "swipe does not fit in the viewport",
                    "use a shorter DX,DY. A mouse drag was not substituted",
                    stage="inspect",
                )
            _touch_drag(client, start_x, start_y, end_x, end_y)
            return None
        remaining_x = float(step["dx"])
        remaining_y = float(step["dy"])
        for pass_index in range(TOUCH_SCROLL_PASSES):
            if abs(remaining_x) < 1 and abs(remaining_y) < 1:
                break
            x, y, viewport = _gesture_origin(
                page, step["locator"], timeout_ms, bring_into_view=pass_index == 0,
            )
            start_x, start_y, end_x, end_y = _fit_stroke(x, y, -remaining_x, -remaining_y, viewport)
            traveled_x = start_x - end_x
            traveled_y = start_y - end_y
            if abs(traveled_x) < 1 and abs(traveled_y) < 1:
                break
            before = _layout_offsets(client)
            _touch_drag(client, start_x, start_y, end_x, end_y)
            time.sleep(0.05)
            after = _layout_offsets(client)
            if before and after and after.get("pageX") == before.get("pageX") and after.get("pageY") == before.get("pageY"):
                break
            remaining_x -= traveled_x
            remaining_y -= traveled_y
        offsets = _layout_offsets(client)
        if offsets is None:
            return None
        return offsets.get("pageY")
    finally:
        try:
            client.detach()
        except Exception:
            pass


def _cdp_send(client, method: str, params: dict | None, sent: list):
    if method in {"Runtime.evaluate", "Runtime.callFunctionOn"}:
        raise InspectError(
            "observe does not call page JavaScript",
            "act is the command that lets Playwright resolve a locator",
            stage="inspect",
        )
    sent.append(method)
    return client.send(method, params or {})


def _with_browser(url, browser_name, profile, headed, channel, viewport=None, touch=False):
    sync_playwright = _playwright()
    playwright = sync_playwright().start()
    browser = None
    try:
        kwargs = {"headless": not headed}
        if channel:
            kwargs["channel"] = channel
        try:
            browser = getattr(playwright, browser_name).launch(**kwargs)
        except Exception as exc:
            raise InspectError(
                f"{type(exc).__name__}: {exc}",
                "install the browser that matches this Playwright build. Headless commands use the shell build; --headed needs the full browser",
                stage="launch",
            ) from exc
        options = context_options(playwright.devices, browser_name, profile, headed, viewport=viewport, touch=touch)
        has_touch = bool(options.get("has_touch"))
        context = browser.new_context(**options, service_workers="block")
        context.route("**/*", restrict_request)
        page = context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(30000)
        return playwright, browser, page, has_touch
    except Exception:
        if browser is not None:
            _close_browser(browser)
        playwright.stop()
        raise


def _finish(playwright, browser, failure, result):
    close_note = _close_browser(browser)
    try:
        playwright.stop()
    except Exception as exc:
        extra = f"{type(exc).__name__}: {exc}"
        close_note = f"{close_note}; {extra}" if close_note else extra
    if failure is not None:
        if close_note:
            failure.hint = failure.hint + f" Browser close also failed: {close_note}"
        raise failure
    if close_note:
        raise InspectError("browser close failed", close_note, stage="cleanup")
    return result


def observe_page(*, url: str, browser_name: str, profile: str, headed: bool, channel: str | None,
                 include_hidden: bool, include_storage_keys: bool, include_storage_fingerprints: bool,
                 role: str, start: int, limit: int, steps: list | None = None, want_run: bool = False,
                 dialog_policy: str = "dismiss", timeout_ms: int = 5000, viewport: dict | None = None,
                 touch: bool = False) -> dict:
    """Passive snapshot, or the same snapshot before and after each declared step."""
    url = loopback_target(url)
    steps = list(steps or [])
    source = viewport_source_for(profile, headed, viewport)
    playwright, browser, page, has_touch = _with_browser(
        url, browser_name, profile, headed, channel, viewport=viewport, touch=touch,
    )
    failure = None
    results: list[dict] = []
    public_steps: list[dict] = []
    secret_values: list[str] = []
    try:
        sent: list[str] = []
        network: list[dict] = []
        console: list[dict] = []
        dialog_items: list[dict] = []
        popup_pages: list = []
        state = {"network_truncated": False, "console_truncated": False}
        client = None
        try:
            client = page.context.new_cdp_session(page)
        except Exception as exc:
            if not steps:
                seen = page.viewport_size or {}
                limits = {
                    "page_js_called": False,
                    "navigation_runs_page_scripts": False,
                    "service_workers": "blocked",
                    "reason": "cdp_session_unavailable",
                    "detail": type(exc).__name__,
                    "cdp_methods": sent,
                    "dialog_policy": dialog_policy,
                }
                results.append(_note_emulation(assemble_observe(
                    {"title": "", "elements": [], "child_frames": 0, "total": 0, "truncated": False},
                    environment=_environment(
                        browser, browser_name, channel, headed, profile, seen,
                        has_touch=has_touch, viewport_source=source,
                    ),
                    url=url, start=start, limit=limit, sections=_unavailable_sections("cdp_session_unavailable"),
                    limits=limits,
                )))
                return _finish(playwright, browser, None, {"observations": results, "run": None, "ok": True})
            client = None
            cdp_detail = type(exc).__name__
        else:
            cdp_detail = None
        # Network and console come from Playwright events, which exist in every engine. Listeners
        # are attached before navigation; the page itself is not called for them.
        def on_response(response):
            if len(network) >= 100:
                state["network_truncated"] = True
                return
            request = response.request
            network.append({
                "method": request.method or "",
                "status": response.status,
                "mime": (response.headers.get("content-type") or "").split(";")[0].strip(),
                "type": request.resource_type or "",
                "url": strip_userinfo(response.url or ""),
            })

        def on_request_failed(request):
            if len(network) >= 100:
                state["network_truncated"] = True
                return
            network.append({
                "method": request.method or "",
                "status": None,
                "mime": "",
                "type": request.resource_type or "",
                "url": strip_userinfo(request.url or ""),
                "failure": clip(request.failure or "", 200),
            })

        def on_console(message):
            if len(console) >= 50:
                state["console_truncated"] = True
                return
            console.append({"type": message.type or "", "argument_count": len(message.args)})

        page.on("response", on_response)
        page.on("requestfailed", on_request_failed)
        page.on("console", on_console)
        _watch_pages(page, dialog_policy, dialog_items, popup_pages)
        try:
            page.goto(url, wait_until="load", timeout=30000)
        except Exception as exc:
            raise InspectError(
                f"{type(exc).__name__}: {exc}",
                "check that the loopback server is up. Core checks serve a local fixture on 127.0.0.1",
                stage="navigate",
            ) from exc

        def _env():
            return _environment(
                browser, browser_name, channel, headed, profile, page.viewport_size or {},
                has_touch=has_touch, viewport_source=source,
            )

        def _limits(page_js: bool, step: dict | None, *, reason: str | None = None) -> dict:
            limits = {
                "page_js_called": page_js,
                "navigation_runs_page_scripts": True,
                "network_source": "playwright events",
                "console_source": "playwright events; argument values are not read",
                "service_workers": "blocked",
                "redirects": "blocked",
                "non_loopback": "blocked",
                "cdp_methods": list(sent),
                "dialog_policy": dialog_policy,
            }
            if reason:
                limits["reason"] = reason
                if cdp_detail:
                    limits["detail"] = cdp_detail
            if step is not None:
                limits["action_status"] = step.get("status")
                if step.get("page_js_called"):
                    limits["selector_engine"] = "playwright"
                if step.get("action") == "click" and step.get("status") == "done":
                    limits["click_runs_page_scripts"] = True
                    limits["selector_engine"] = "playwright"
                if step.get("action") == "hover" and step.get("status") == "done":
                    limits["hover_may_trigger_css"] = True
                    limits["selector_engine"] = "playwright"
                if step.get("action") in {"scroll", "scroll_into", "scroll-into-view"} and step.get("status") == "done":
                    limits["scroll_may_run_page_scripts"] = True
                if step.get("action") in {"scroll_into", "scroll-into-view"} and step.get("status") == "done":
                    limits["scroll_into_view"] = True
                    limits["selector_engine"] = "playwright"
                if step.get("scroll_y") is not None:
                    limits["scroll_y"] = step["scroll_y"]
                if "quiet" in step:
                    limits["quiet"] = bool(step["quiet"])
            return limits

        def _take_intervals():
            network_items = list(network)
            console_items = list(console)
            dialogs = list(dialog_items)
            popups = list(popup_pages)
            flags = (state["network_truncated"], state["console_truncated"])
            network.clear()
            console.clear()
            dialog_items.clear()
            popup_pages.clear()
            state["network_truncated"] = False
            state["console_truncated"] = False
            return network_items, console_items, dialogs, popups, flags

        def _capture_without_cdp(step: dict | None, page_js: bool) -> dict:
            network_items, console_items, dialogs, popups, _flags = _take_intervals()
            sections = _unavailable_sections("cdp_session_unavailable")
            sections["dialogs"] = _dialog_section(dialogs, dialog_policy)
            sections["popups"] = _popup_section(popups)
            sections["network"] = _section("playwright:request", "attach_limited", {
                "prior_activity_available": False, "bodies": "not_requested", "items": network_items,
            })
            snapshot = assemble_observe(
                {"title": "", "elements": [], "child_frames": 0, "total": 0, "truncated": False},
                environment=_env(), url=page.url, start=start, limit=limit, sections=sections,
                limits=_limits(page_js, step, reason="cdp_session_unavailable"),
            )
            return _note_emulation(snapshot, step)

        def capture(step: dict | None, page_js: bool) -> dict:
            if client is None:
                return _capture_without_cdp(step, page_js)
            document = _cdp_send(client, "DOM.getDocument", {"depth": -1, "pierce": False}, sent)
            pierced = _cdp_send(client, "DOM.getDocument", {"depth": -1, "pierce": True}, sent)
            shadow = count_shadow_roots(pierced.get("root") or {})
            ax = _cdp_send(client, "Accessibility.getFullAXTree", {}, sent)
            resources = _cdp_send(client, "Page.getResourceTree", {}, sent)
            frames = _cdp_send(client, "Page.getFrameTree", {}, sent)
            dom_nodes: list[dict] = []
            dom_truncated = _walk_dom(document.get("root") or {}, dom_nodes)
            elements, meta = _controls_from_cdp(
                dom_nodes, ax.get("nodes") or [], include_hidden=include_hidden, role=role,
                document_url=page.url,
            )
            title = ""
            for ax_node in ax.get("nodes") or []:
                if _ax_text(ax_node.get("role")) == "RootWebArea":
                    title = clip(_ax_text(ax_node.get("name")), 200)
                    break
            child_frames = max(0, _frame_count(frames.get("frameTree") or {}) - 1)
            resource_rows = []
            for item in (resources.get("frameTree") or {}).get("resources") or []:
                resource_rows.append({
                    "type": item.get("type") or "",
                    "mime": item.get("mimeType") if isinstance(item.get("mimeType"), str) else "",
                    "url": strip_userinfo(item.get("url") or ""),
                })
            document_url = strip_userinfo(((frames.get("frameTree") or {}).get("frame") or {}).get("url") or "")
            frame_items, frame_truncated = frame_rows(frames.get("frameTree") or {})
            controls_digest = _content_digest([
                [e.get("address"), e.get("role"), e.get("name"), e.get("text")] for e in elements
            ])
            names_digest = _content_digest([
                [_ax_text(n.get("role")), _ax_text(n.get("name"))]
                for n in (ax.get("nodes") or []) if not n.get("ignored")
            ])
            origin = urllib.parse.urlsplit(page.url)
            origin_text = f"{origin.scheme}://{origin.netloc}"
            storage = _storage_section(
                client, origin_text, keys=include_storage_keys, fingerprints=include_storage_fingerprints, sent=sent,
            )
            network_items, console_items, dialogs, popups, flags = _take_intervals()
            network_coverage = "truncated" if flags[0] else "attach_limited"
            console_coverage = "truncated" if flags[1] else "attach_limited"
            sections = {
                "dom": _section("cdp:DOM.getDocument", "truncated" if dom_truncated else "complete", {
                    "nodes": meta["dom_nodes"], "attribute_values": "id and data-testid only",
                    "document_url": document_url, "title": title, "controls_sha256": controls_digest,
                }),
                "accessibility": _section("cdp:Accessibility.getFullAXTree", "complete", {
                    "nodes": meta["ax_nodes"], "names_sha256": names_digest,
                }),
                "scripts": _section("cdp:DOM.getDocument", "complete_within_window", {
                    "items": meta["scripts"], "note": "scripts with no DOM node are outside this section",
                }),
                "resources": _section("cdp:Page.getResourceTree", "complete", {
                    "items": resource_rows, "document_url": document_url,
                }),
                "network": _section("playwright:page.response", network_coverage, {
                    "prior_activity_available": False, "bodies": "not_requested", "items": network_items,
                }),
                "console": _section("playwright:page.console", console_coverage, {
                    "text": "not_requested", "prior_activity_available": False, "items": console_items,
                }),
                "storage": storage,
                "frames": _section("cdp:Page.getFrameTree", "truncated" if frame_truncated else "complete", {
                    "child_frames": child_frames, "contents": "not_entered", "items": frame_items,
                }),
                "shadow_roots": _section("cdp:DOM.getDocument pierce", "truncated" if shadow["truncated"] else "complete", {
                    "open": shadow["open"], "closed": shadow["closed"], "count": shadow["count"],
                    "user_agent": shadow["user_agent"], "contents": "not_entered",
                }),
                "dialogs": _dialog_section(dialogs, dialog_policy),
                "popups": _popup_section(popups),
            }
            snapshot = assemble_observe(
                {"title": title, "elements": elements, "child_frames": child_frames, "total": len(elements), "truncated": dom_truncated},
                environment=_env(), url=page.url, start=start, limit=limit, sections=sections,
                limits=_limits(page_js, step),
            )
            return _note_emulation(snapshot, step)

        results.append(capture(None, False))
        for action_index, step in enumerate(steps, start=1):
            raw_before = page.url
            url_before = strip_userinfo(raw_before)
            secret_values.extend(_secret_values(step))
            _secrets.set(tuple(secret_values))
            try:
                outcome = perform_action(
                    page, step, timeout_ms=timeout_ms, has_touch=has_touch, browser_name=browser_name,
                    cdp_client=client, cdp_sent=sent,
                )
                if outcome.get("status") == "done" and page.url != raw_before:
                    page.wait_for_load_state("load", timeout=NAVIGATION_TIMEOUT_MS)
            except InspectError as exc:
                caught = exc
            except Exception as exc:
                caught = InspectError(
                    redact_text(f"{type(exc).__name__}: {exc}"),
                    "the step did not settle. The following snapshot was not written",
                    stage="navigate",
                )
            else:
                caught = None
            if caught is not None:
                public_steps.append(publish_step(
                    step,
                    {"status": "error", "page_js_called": False, "support": {}, "input": "none", "reason": caught.error},
                    url_before=url_before, url_after=strip_userinfo(page.url), index=action_index,
                ))
                failure = caught
                break
            public = publish_step(
                step, outcome, url_before=url_before, url_after=strip_userinfo(page.url), index=action_index,
            )
            public_steps.append(public)
            embedded = dict(public)
            embedded["index"] = action_index + 1
            results.append(capture(embedded, bool(outcome.get("page_js_called"))))
    except InspectError as exc:
        failure = exc
    except Exception as exc:
        failure = InspectError(
            redact_text(f"{type(exc).__name__}: {exc}"),
            "the CDP read failed; the snapshot was not written",
            stage="inspect",
        )
    if failure is not None and not want_run:
        failure.error = redact_text(failure.error)
        failure.hint = redact_text(failure.hint)
        _secrets.set(())
        return _finish(playwright, browser, failure, None)
    run = build_run(results, public_steps, dialog_policy=dialog_policy, error=failure) if want_run else None
    payload = redact_obj({"observations": results, "run": run, "ok": failure is None})
    _secrets.set(())
    return _finish(playwright, browser, None, payload)





def _action_locator(page, locator: str):
    text = (locator or "").strip()
    if text.startswith("h:"):
        raise InspectError(
            "act does not accept a snapshot ordinal",
            "pass id:, testid:, role:, or css: so the control is queried again",
        )
    if text.startswith("id:"):
        ident = text[3:]
        if not SAFE.fullmatch(ident):
            raise InspectError("id locator has unsafe characters", "use the id: value from observe")
        return page.locator("#" + ident), True
    if text.startswith("testid:"):
        ident = text[7:]
        if not SAFE.fullmatch(ident):
            raise InspectError("testid locator has unsafe characters", "use the testid: value from observe")
        return page.get_by_test_id(ident), True
    if text.startswith("css:"):
        selector = text[4:]
        if "javascript:" in selector.lower():
            raise InspectError("css locator cannot carry a script URL", "pass a CSS selector")
        return page.locator(selector), True
    if text.startswith("role:"):
        role, name, nth = _split_role_address(text[5:])
        return page.get_by_role(role, name=name, exact=True).nth(nth), True
    raise InspectError(
        "act locator must be id:, testid:, role:, or css:",
        "observe prints those addresses; h:N is not accepted",
    )


def _note_emulation(snapshot: dict, step: dict | None = None) -> dict:
    env = snapshot.get("environment") or {}
    if env.get("emulation") and env.get("emulated_device"):
        snapshot["hint"] = snapshot.get("hint", "") + (
            f". {env['emulated_device']} is Playwright device emulation, not a physical device"
        )
    if step is not None:
        snapshot["step"] = step
    return snapshot


def act_page(*, url: str, browser_name: str, profile: str, headed: bool, channel: str | None,
             action: str, locator: str, text: str, timeout_ms: int, text_env: str | None = None,
             text_file: str | None = None, to: str | None = None, touch: bool = False,
             viewport: dict | None = None, dialog_policy: str = "dismiss") -> dict:
    step = act_arguments_to_step(action, locator, text, text_env, text_file, to, timeout_ms)
    url = loopback_target(url)
    source = viewport_source_for(profile, headed, viewport)
    secret_values = _secret_values(step)
    playwright, browser, page, has_touch = _with_browser(
        url, browser_name, profile, headed, channel, viewport=viewport, touch=touch,
    )
    failure = None
    result = None
    _secrets.set(tuple(secret_values))
    try:
        dialog_items: list[dict] = []
        popup_pages: list = []
        _watch_pages(page, dialog_policy, dialog_items, popup_pages)
        try:
            page.goto(url, wait_until="load", timeout=30000)
        except Exception as exc:
            raise InspectError(
                f"{type(exc).__name__}: {exc}",
                "check that the loopback server is up",
                stage="navigate",
            ) from exc
        outcome = perform_action(page, step, timeout_ms=timeout_ms, has_touch=has_touch, browser_name=browser_name)
        page_js = bool(outcome.get("page_js_called"))
        if browser_name == "chromium":
            try:
                client = page.context.new_cdp_session(page)
                ax = _cdp_send(client, "Accessibility.getFullAXTree", {}, [])
                names = []
                for node in ax.get("nodes") or []:
                    role_name = _ax_text(node.get("role")).lower()
                    if role_name in INTERACTIVE_ROLES and not node.get("ignored"):
                        names.append(clip(_ax_text(node.get("name"))))
                ax_section = _section("cdp:Accessibility.getFullAXTree", "complete", {"names": names})
            except Exception as exc:
                ax_section = _section("cdp:Accessibility.getFullAXTree", "unavailable", {"reason": type(exc).__name__})
        else:
            ax_section = _section(None, "not_requested")
        seen = page.viewport_size or {}
        published = publish_step(step, outcome, url_before=strip_userinfo(url), url_after=strip_userinfo(page.url), index=1)
        result = {
            "schema": SCHEMA_INSPECT,
            "ok": True,
            "operation": "act",
            "action": action,
            "status": outcome["status"],
            "locator": locator,
            "url": strip_userinfo(page.url),
            "support": outcome.get("support") or {},
            "environment": _environment(
                browser, browser_name, channel, headed, profile, seen,
                has_touch=has_touch, viewport_source=source,
            ),
            "limits": {
                "page_js_called": page_js,
                "bounded_to": [action],
                **({"quiet": outcome["quiet"]} if "quiet" in outcome else {}),
                "ordinals": "rejected",
                "dom": "not_requested",
                "selector_engine": "playwright" if page_js else "none",
                "dialog_policy": dialog_policy,
                "action_status": outcome["status"],
            },
            "sections": {
                "url": _section("playwright:page.url", "complete", {"url": strip_userinfo(page.url)}),
                "accessibility": ax_section,
                "dom": _section(None, "not_requested"),
                "storage": _section(None, "not_requested"),
                "dialogs": _dialog_section(dialog_items, dialog_policy),
                "popups": _popup_section(popup_pages),
            },
            "step": published,
            "hint": "url was captured. dom was not requested, so this result cannot say the markup was unchanged. typed values are redacted",
        }
        if "quiet" in outcome:
            result["limits"]["quiet"] = bool(outcome["quiet"])
        if outcome.get("reason"):
            result["reason"] = outcome["reason"]
        result = _note_emulation(redact_obj(result))
    except InspectError as exc:
        failure = exc
    except Exception as exc:
        failure = InspectError(
            redact_text(f"{type(exc).__name__}: {exc}"),
            "the action failed before it could write a result",
            stage="inspect",
        )
    finally:
        _secrets.set(())
    return _finish(playwright, browser, failure, result)


def read_snapshot(path: str) -> dict:
    file = Path(path)
    if not file.is_file():
        raise InspectError(f"snapshot not found: {path}", "run observe --out this path first")
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InspectError("snapshot is not JSON", "run observe again and pass that file") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_OBSERVE:
        raise InspectError("snapshot schema is not btk-page-observe/1", "pass a file written by observe, not a locate result")
    elements = data.get("elements")
    if not isinstance(elements, list):
        raise InspectError("snapshot has no elements list", "run observe again")
    for element in elements:
        if not isinstance(element, dict) or not isinstance(element.get("address"), str) or not isinstance(element.get("handle"), str):
            raise InspectError("snapshot element is missing address or handle", "run observe again")
    return data


def _shadow_counts(snapshot: dict) -> dict | None:
    section = (snapshot.get("sections") or {}).get("shadow_roots")
    if not isinstance(section, dict) or section.get("coverage") not in COMPLETE_COVERAGE:
        return None
    counts = {}
    for key in ("open", "closed", "count"):
        value = section.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        counts[key] = value
    return counts


def _count_pair(left, right) -> dict:
    delta = right - left if isinstance(left, int) and isinstance(right, int) else None
    return {"left": left, "right": right, "delta": delta}


def compare_snapshots(left: dict, right: dict) -> dict:
    """Section relations and counts for two observe snapshots. A missing side is never same."""
    left_sections = left.get("sections") or {}
    right_sections = right.get("sections") or {}
    sections = {}
    for name in sorted(set(left_sections) | set(right_sections)):
        left_part = left_sections.get(name)
        right_part = right_sections.get(name)
        sections[name] = {
            "relation": evidence_relation(left_part, right_part),
            "left": left_part.get("coverage") if isinstance(left_part, dict) else None,
            "right": right_part.get("coverage") if isinstance(right_part, dict) else None,
        }
    left_shadow = _shadow_counts(left)
    right_shadow = _shadow_counts(right)
    shadow = {key: _count_pair(*_side(left_shadow, right_shadow, key)) for key in ("open", "closed", "count")}
    left_env = left.get("environment") if isinstance(left.get("environment"), dict) else {}
    right_env = right.get("environment") if isinstance(right.get("environment"), dict) else {}
    environment = {key: [left_env.get(key), right_env.get(key)] for key in COMPARE_ENVIRONMENT_KEYS}
    same_environment = all(pair[0] == pair[1] for pair in environment.values())
    hints = [
        "same, different, and unknown are section relations; a missing or truncated section is never same",
        "shadow_roots counts are null when that section was not complete on one side",
    ]
    if not same_environment:
        hints.append("environments differ; section differences may come from the browser, profile, touch, emulation, or viewport, not only the page")
    return {
        "schema": SCHEMA_COMPARE,
        "ok": True,
        "left_url": strip_userinfo(left["url"]) if isinstance(left.get("url"), str) else None,
        "right_url": strip_userinfo(right["url"]) if isinstance(right.get("url"), str) else None,
        "same_environment": same_environment,
        "environment": environment,
        "sections": sections,
        "shadow_roots": shadow,
        "child_frames": _count_pair(left.get("child_frames"), right.get("child_frames")),
        "elements": _count_pair(left.get("total"), right.get("total")),
        "hint": ". ".join(hints),
    }


def _side(left_shadow, right_shadow, key):
    return (
        left_shadow[key] if left_shadow else None,
        right_shadow[key] if right_shadow else None,
    )


def dumps(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


# Ported from Stagehand's sanitizeErrorMessage (packages/integrations/core/src/harness/redact.ts).
# Playwright exception text can carry URLs with keys, bearer tokens, or provider keys.
REDACTIONS = (
    (re.compile(r"([?&](?:signingKey|apiKey|api_key|token|key)=)[^&\s\"']+", re.IGNORECASE), r"\1[redacted]"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+"), r"\1[redacted]"),
    (re.compile(r"\b(bb_(?:live|test)_[A-Za-z0-9]{4})[A-Za-z0-9_-]+"), r"\1[redacted]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "AIza[redacted]"),
    (re.compile(r"\b(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE), r"\1[redacted]"),
)




# Ported from Playwright's resolveSmartModifierString (packages/playwright-core/src/server/input.ts).
# ControlOrMeta is Meta on macOS and Control elsewhere. Chords are split on "+" and each part resolved.
def resolve_smart_key(key: str, platform_name: str | None = None) -> str:
    name = sys.platform if platform_name is None else platform_name
    smart = "Meta" if name == "darwin" else "Control"
    return "+".join(smart if part == "ControlOrMeta" else part for part in key.split("+"))


def error_envelope(operation: str, exc: InspectError) -> dict:
    payload = {
        "schema": SCHEMA_INSPECT, "ok": False, "operation": operation, "stage": exc.stage,
        "error": redact_text(exc.error), "hint": redact_text(exc.hint),
    }
    if exc.suggestions:
        payload["suggestions"] = exc.suggestions
    return payload


def error_text(payload: dict) -> str:
    lines = [f"stage: {payload.get('stage', 'input')}", f"error: {payload.get('error')}", f"hint: {payload.get('hint')}"]
    for item in payload.get("suggestions") or []:
        score = item.get("score")
        score_text = "" if not isinstance(score, (int, float)) or isinstance(score, bool) else f" {score:.6f}"
        lines.append(f"suggest: {item.get('address')}{score_text} {item.get('name')}")
    return "\n".join(lines) + "\n"


def observe_text(payload: dict) -> str:
    env = payload["environment"]
    mode = "headless" if env.get("headless") else "headed"
    lines = [
        f"btk-page-observe/1 {env.get('os')} {env.get('browser')} {env.get('profile')} {mode} "
        f"elements={payload.get('total')} showing={len(payload.get('elements') or [])}"
    ]
    if env.get("emulation"):
        lines.append(f"emulation: {env.get('emulated_device')} real_device=false")
    elif env.get("viewport_source") == "flag":
        lines.append(f"viewport: {env.get('viewport', {}).get('width')}x{env.get('viewport', {}).get('height')} source=flag real_device=false")
    lines.extend(element_line(element) for element in payload.get("elements") or [])
    lines.append(f"hint: {payload.get('hint')}")
    return "\n".join(lines) + "\n"


def run_text(payload: dict) -> str:
    lines = [
        f"btk-page-observe-run/1 steps={len(payload.get('steps') or [])} "
        f"observations={len(payload.get('observations') or [])} ok={str(payload.get('ok')).lower()}"
    ]
    for step in payload.get("steps") or []:
        locator = step.get("locator") or ""
        redacted = " redacted" if step.get("value_source") else ""
        reason = ""
        if step.get("status") == "unsupported" and step.get("reason"):
            reason = " unsupported"
        lines.append(f"{step.get('index')} {step.get('action')} {locator}{redacted} {step.get('status')}{reason}".rstrip())
    if payload.get("error"):
        lines.append(f"error: {payload['error']}")
    if payload.get("files"):
        lines.append(f"files={len(payload['files'])}")
    lines.append(f"hint: {payload.get('hint')}")
    return "\n".join(lines) + "\n"


def locate_text(payload: dict) -> str:
    if not payload.get("ok"):
        return error_text(payload)
    lines = [element_line(element) for element in payload.get("items") or []]
    page = payload["pagination"]
    lines.append(f"returned {page['returned']} of {page['total']}; start {page['start']}")
    lines.append(f"hint: {payload.get('hint')}")
    return "\n".join(lines) + "\n"


def count_text(payload: dict) -> str:
    lines = [f"total={payload['total']} visible={payload['visible']} enabled={payload['enabled']}"]
    for role, count in payload["by_role"].items():
        lines.append(f"role {role}={count}")
    lines.append(f"hint: {payload['hint']}")
    return "\n".join(lines) + "\n"


def describe_text(payload: dict) -> str:
    if not payload.get("ok"):
        return error_text(payload)
    lines = [f"resolved_as={payload['resolved_as']}", *payload["context"], f"hint: {payload['hint']}"]
    return "\n".join(lines) + "\n"


def compare_text(payload: dict) -> str:
    lines = [f"btk-page-compare/1 same_environment={str(payload['same_environment']).lower()}"]
    for name, item in payload["sections"].items():
        lines.append(f"section {name}={item['relation']} left={item['left']} right={item['right']}")
    for key in ("open", "closed", "count"):
        pair = payload["shadow_roots"][key]
        lines.append(f"shadow_roots {key} left={pair['left']} right={pair['right']} delta={pair['delta']}")
    for key in ("elements", "child_frames"):
        pair = payload[key]
        lines.append(f"{key} left={pair['left']} right={pair['right']} delta={pair['delta']}")
    lines.append(f"hint: {payload['hint']}")
    return "\n".join(lines) + "\n"


def operations_text(payload: dict) -> str:
    lines = [f"{item['name']}: {item['summary']}" for item in payload["items"]]
    lines.append(f"hint: {payload['hint']}")
    return "\n".join(lines) + "\n"


def _bounded(maximum: int):
    def parse(text: str) -> int:
        if not re.fullmatch(r"(?:0|[1-9][0-9]*)", text):
            raise argparse.ArgumentTypeError("must be a whole number")
        value = int(text)
        if value > maximum:
            raise argparse.ArgumentTypeError(f"must be at most {maximum}")
        return value
    return parse


def _viewport_arg(text: str) -> dict:
    try:
        return parse_viewport(text)
    except InspectError as exc:
        raise argparse.ArgumentTypeError(exc.error) from exc


def _threshold(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise argparse.ArgumentTypeError("must be from 0 to 1")
    return value


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    common.add_argument("--json", action="store_true", help="write the JSON document to stdout")
    common.add_argument("--out", help="write the JSON document to this path; the parent directory must exist")
    parser = argparse.ArgumentParser(prog="btk.py", description=__doc__.split("\n\n")[0], allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    observe = sub.add_parser("observe", parents=[common], help="passive CDP snapshot of a loopback page", allow_abbrev=False)
    observe.add_argument("target", help="http://127.0.0.1:PORT/path or http://[::1]:PORT/path")
    observe.add_argument("--browser", choices=("chromium", "firefox", "webkit"), default="chromium")
    observe.add_argument("--profile", choices=tuple(PROFILES), default="desktop")
    observe.add_argument("--channel", help="branded browser channel, for example chrome")
    observe.add_argument("--headed", action="store_true", help="launch the full browser instead of headless")
    observe.add_argument("--include-hidden", action="store_true", help="keep controls the accessibility tree ignored")
    observe.add_argument("--include-storage-keys", action="store_true", help="record storage key names, never values")
    observe.add_argument("--include-storage-fingerprints", action="store_true", help="add SHA-256 fingerprints; requires keys")
    observe.add_argument("--role", default="", help="only this role")
    observe.add_argument("--start", type=_bounded(100000), default=0)
    observe.add_argument("--limit", type=_bounded(SURVEY_LIMIT_MAX), default=SURVEY_LIMIT_DEFAULT)
    observe.add_argument("--routes", default=None, help="route-to-emoji JSON")
    observe.add_argument("--trail", default=None, help="emoji route trail; requires --routes and --out-dir")
    observe.add_argument("--viewport", type=_viewport_arg, default=None, help="WIDTHxHEIGHT size. Not a physical device")
    observe.add_argument("--touch", action="store_true", help="set hasTouch on the context. Not a physical device")
    observe.add_argument("--dialog-policy", choices=("dismiss", "accept"), default="dismiss",
                         help="record JavaScript dialogs. accept only when this is accept; the default dismisses")
    observe.add_argument("--timeout-ms", type=_bounded(120000), default=5000)
    observe.add_argument("--step", action="append", default=None,
                         help="repeatable action such as click:id:save or type:id:search=env:SEARCH. Values are redacted")
    observe.add_argument(
        "--out-dir",
        help="with --step, also write each observation as its own btk-page-observe/1 file in this existing directory",
    )
    observe.add_argument(
        "--after-action",
        metavar="SPEC",
        help="one step, then a second snapshot: click:LOCATOR, hover:LOCATOR, scroll:DX,DY, or scroll-into:LOCATOR",
    )
    observe.add_argument("--after-click", metavar="LOCATOR", help="alias for --after-action click:LOCATOR")
    observe.add_argument("--after-out", help="write the second snapshot here; requires --after-action (or --after-click) and --out")
    act = sub.add_parser("act", parents=[common], help="one declared action that re-queries a locator", allow_abbrev=False)
    act.add_argument("target", help="loopback URL; a snapshot ordinal is not accepted")
    act.add_argument("--action", required=True, choices=tuple(sorted(ACT_ACTIONS)))
    act.add_argument("--locator", default="", help="id:, testid:, role:, or css: from observe")
    act.add_argument("--text", default=None, help="non-secret literal. Secrets use --text-env or --text-file and are redacted")
    act.add_argument("--text-env", default=None, help="read the action value from this environment variable")
    act.add_argument("--text-file", default=None, help="read the action value from this UTF-8 file")
    act.add_argument("--to", default=None, help="destination locator for drag")
    act.add_argument("--timeout-ms", type=_bounded(120000), default=5000)
    act.add_argument("--viewport", type=_viewport_arg, default=None, help="WIDTHxHEIGHT size. Not a physical device")
    act.add_argument("--touch", action="store_true", help="set hasTouch on the context. Not a physical device")
    act.add_argument("--dialog-policy", choices=("dismiss", "accept"), default="dismiss")
    act.add_argument("--browser", choices=("chromium", "firefox", "webkit"), default="chromium")
    act.add_argument("--profile", choices=tuple(PROFILES), default="desktop")
    act.add_argument("--channel", help="branded browser channel, for example chrome")
    act.add_argument("--headed", action="store_true")
    act.add_argument("--routes", metavar="FILE", help="JSON {route: emoji} from btk.py pages; lets goto take an emoji")
    pages_parser = sub.add_parser("pages", parents=[common], help="discover same-origin pages and give each an emoji", allow_abbrev=False)
    pages_parser.add_argument("target", help="loopback start URL")
    pages_parser.add_argument("--browser", choices=("chromium", "firefox", "webkit"), default="chromium")
    pages_parser.add_argument("--profile", choices=tuple(PROFILES), default="desktop")
    pages_parser.add_argument("--channel", help="branded browser channel, for example chrome")
    pages_parser.add_argument("--headed", action="store_true")
    pages_parser.add_argument("--max-pages", type=_bounded(len(ROUTE_POOL)), default=20)
    pages_parser.add_argument("--routes-out", metavar="FILE", help="write the {route: emoji} JSON here")
    scenario = sub.add_parser("scenario", parents=[common], help="bounded platform and fault observation", allow_abbrev=False)
    scenario.add_argument("target", help="loopback scenario fixture URL")
    scenario.add_argument("--case", required=True, choices=("display", "shortcut", "offline", "http-error", "latency", "permission", "upload", "download"))
    scenario.add_argument("--browser", choices=("chromium", "firefox", "webkit"), default="chromium")
    scenario.add_argument("--profile", choices=tuple(PROFILES), default="desktop")
    scenario.add_argument("--channel", default=None, help="chromium only: chrome or msedge")
    scenario.add_argument("--headed", action="store_true")
    scenario.add_argument("--viewport", default=None, help="WIDTHxHEIGHT, each 200..4096")
    scenario.add_argument("--color-scheme", choices=("light", "dark", "no-preference"), default="light")
    scenario.add_argument("--reduced-motion", choices=("reduce", "no-preference"), default="no-preference")
    scenario.add_argument("--locale", default="en-US")
    scenario.add_argument("--timezone", default="UTC")
    scenario.add_argument("--device-scale-factor", type=float, default=1.0)
    scenario.add_argument("--latency-ms", type=int, default=100)
    scenario.add_argument("--permission", choices=("notifications", "geolocation"), default="notifications")
    scenario.add_argument("--permission-state", choices=("grant", "clear"), default="grant")
    locate_parser = sub.add_parser("locate", parents=[common], help="rank controls in a snapshot", allow_abbrev=False)
    locate_parser.add_argument("snapshot")
    locate_parser.add_argument("--query", default="")
    locate_parser.add_argument("--role", default="")
    locate_parser.add_argument("--threshold", type=_threshold, default=THRESHOLD_DEFAULT)
    locate_parser.add_argument("--start", type=_bounded(100000), default=0)
    locate_parser.add_argument("--limit", type=_bounded(PAGE_LIMIT_MAX), default=PAGE_LIMIT_DEFAULT)
    locate_parser.add_argument("--unique", action="store_true", help="fail when the top score is shared")
    count_parser = sub.add_parser("count", parents=[common], help="count controls without listing them", allow_abbrev=False)
    count_parser.add_argument("snapshot")
    count_parser.add_argument("--query", default="")
    count_parser.add_argument("--role", default="")
    count_parser.add_argument("--threshold", type=_threshold, default=THRESHOLD_DEFAULT)
    describe_parser = sub.add_parser("describe", parents=[common], help="one control and neighbor lines", allow_abbrev=False)
    describe_parser.add_argument("snapshot")
    describe_parser.add_argument("--target", required=True)
    describe_parser.add_argument("--context", type=_bounded(CONTEXT_MAX), default=CONTEXT_DEFAULT)
    compare_parser = sub.add_parser("compare", parents=[common], help="compare two observe snapshots", allow_abbrev=False)
    compare_parser.add_argument("left")
    compare_parser.add_argument("right")
    sub.add_parser("operations", parents=[common], help="list commands", allow_abbrev=False)
    return parser


def parse_after_action(spec: str) -> dict:
    """Parse --after-action into one step. The same performer runs --step."""
    text = (spec or "").strip()
    if not text:
        raise InspectError("after-action is empty", "use click:, hover:, scroll:, or scroll-into:")
    lowered = text.lower()
    if lowered.startswith("click:"):
        return {"kind": "click", "locator": _require_locator(text[6:])}
    if lowered.startswith("hover:"):
        return {"kind": "hover", "locator": _require_locator(text[6:])}
    if lowered.startswith("scroll-into:"):
        return {"kind": "scroll_into", "locator": _require_locator(text[12:])}
    if lowered.startswith("scroll:"):
        body = text[7:]
        if body.startswith(("id:", "testid:", "role:", "css:")):
            return {"kind": "scroll_into", "locator": _require_locator(body)}
        step = parse_step(f"scroll:{body}")
        return {"kind": "scroll", "dx": step["dx"], "dy": step["dy"]}
    raise InspectError(
        "after-action must start with click:, hover:, scroll:, or scroll-into:",
        "example: --after-action hover:id:menu",
    )


def after_action_to_step(parsed: dict) -> dict:
    kind = parsed["kind"]
    if kind == "scroll":
        return parse_step(f"scroll:{parsed['dx']},{parsed['dy']}")
    if kind == "scroll_into":
        step = parse_step(f"scroll-into-view:{parsed['locator']}")
        step["action"] = "scroll_into"
        return step
    return parse_step(f"{kind}:{parsed['locator']}")


def resolve_observe_after_action(args: argparse.Namespace) -> dict | None:
    if args.after_action and args.after_click:
        raise InspectError(
            "pass only one of --after-action and --after-click",
            "--after-click is an alias for --after-action click:LOCATOR",
        )
    if args.after_action:
        return parse_after_action(args.after_action)
    if args.after_click:
        return parse_after_action(f"click:{args.after_click.strip()}")
    return None


def _check_action_locator(locator: str | None) -> None:
    """Shared by act and observe --after-click: an address from observe, never a snapshot ordinal."""
    text = (locator or "").strip()
    if not text or text.startswith("h:"):
        raise InspectError(
            "act does not accept a snapshot ordinal",
            "pass id:, testid:, role:, or css: so the control is queried again",
        )
    if not text.startswith(("id:", "testid:", "role:", "css:")):
        raise InspectError(
            "act locator must be id:, testid:, role:, or css:",
            "observe prints those addresses; h:N is not accepted",
        )

def _check_sequence_args(args: argparse.Namespace, after_action: dict | None) -> None:
    if after_action is None or args.after_out is None or not args.out:
        raise InspectError(
            "after-action needs --out for the first snapshot and --after-out for the second",
            "pass both file paths; each file is a btk-page-observe/1 snapshot",
        )
    if Path(args.out).resolve() == Path(args.after_out).resolve():
        raise InspectError("--out and --after-out must be different files", "give each snapshot its own path")
    for path in (args.out, args.after_out):
        if not Path(path).parent.is_dir():
            raise InspectError(f"directory does not exist: {Path(path).parent}", "create the directory before --out or --after-out")


# Emoji routes. A routes file maps each route to one emoji (the format of
# fixtures/route_trail_registry.json). Trails use route_trail.py's grammar.
ROUTE_GO, ROUTE_ROUND = "→", "↔"
ROUTE_POOL = ("🏠", "🗺", "🌍", "🕒", "🔎", "📄", "📁", "💬", "🔔", "⭐",
              "🧭", "🧪", "📊", "🛒", "👤", "🔑", "📝", "🎯", "🚪", "🧩",
              "💡", "🔗", "🎨", "🧾", "🗓", "🔐", "📣", "📷", "🎵", "🏆")
MAX_ROUTES_FILE_BYTES = 64 * 1024


def emoji_key(text: str) -> str:
    """Compare emoji without spaces or variation selectors (U+FE0E, U+FE0F)."""
    return re.sub(r"[\s\ufe0e\ufe0f]", "", text)


def load_routes(path: str) -> dict:
    """Read {route: emoji} and return {emoji key: route}. One emoji per route, no collisions."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise InspectError(f"cannot read routes file: {exc}", "pass the JSON file that btk.py pages wrote") from exc
    if len(raw) > MAX_ROUTES_FILE_BYTES:
        raise InspectError("routes file is too large", f"keep at most {len(ROUTE_POOL)} routes")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise InspectError("routes file is not JSON", 'write a JSON object such as {"/": "🏠"}') from exc
    if not isinstance(data, dict) or not 1 <= len(data) <= len(ROUTE_POOL):
        raise InspectError("routes file must map 1 to %d routes to emoji" % len(ROUTE_POOL), 'write a JSON object such as {"/": "🏠"}')
    routes: dict = {}
    for route, emoji in data.items():
        if not isinstance(route, str) or not route.startswith("/") or len(route) > 512 or any(c.isspace() or c == "#" for c in route):
            raise InspectError(f"bad route {route!r}", "routes start with / and contain no spaces or #")
        key = emoji_key(emoji) if isinstance(emoji, str) else ""
        if not key:
            raise InspectError(f"route {route} needs one emoji as its value", 'write {"/": "🏠"}')
        if key in routes:
            raise InspectError(f"emoji {emoji} is used by two routes", "give each route its own emoji")
        routes[key] = route
    return routes


def parse_trail(trail: str) -> list:
    """'A → B ↔ C' -> ['A', 'B', 'C', 'B'] as emoji keys, in visiting order (same grammar as route_trail.py)."""
    text = re.sub(r"\s", "", trail).replace("<->", ROUTE_ROUND).replace("->", ROUTE_GO)
    tokens = re.split(f"([{ROUTE_GO}{ROUTE_ROUND}])", text)
    if not tokens[0] or any(not part for part in tokens[2::2]):
        raise InspectError("trail must alternate pages and arrows", "write e.g. '🏠 → 🗺 ↔ 🌍'")
    steps = [emoji_key(tokens[0])]
    for arrow, page in zip(tokens[1::2], tokens[2::2]):
        previous = steps[-1]
        steps.append(emoji_key(page))
        if arrow == ROUTE_ROUND:
            steps.append(previous)
    if len(steps) > MAX_STEPS:
        raise InspectError(f"trail has more than {MAX_STEPS} steps", "split it into more than one observe")
    return steps


def origin_of(target: str) -> str:
    parts = urllib.parse.urlsplit(loopback_target(target))
    return f"{parts.scheme}://{parts.netloc}"


def route_url(emoji: str, origin: str, routes: dict | None) -> str:
    if routes is None:
        raise InspectError(f"{emoji} is not a URL", "pass a loopback URL, or --routes FILE to use an emoji")
    route = routes.get(emoji_key(emoji))
    if route is None:
        raise InspectError(f"no route for emoji {emoji}", "run btk.py pages to list the pages and their emoji")
    return loopback_target(origin + route)


def goto_target(text: str, origin: str, routes: dict | None) -> str:
    """A goto target is a loopback URL, or a page emoji from --routes."""
    if text.startswith(("http://", "https://")):
        return loopback_target(text)
    return route_url(text, origin, routes)


def trail_urls(trail: str, origin: str, routes: dict) -> list:
    return [route_url(key, origin, routes) for key in parse_trail(trail)]


PAGE_LINK_LIMIT = 256
TOTAL_LINK_LIMIT = 2048


def discover_pages(*, url: str, browser_name: str, profile: str, headed: bool, channel: str | None, max_pages: int) -> dict:
    """Breadth-first walk of same-origin links from a loopback start page; emoji are assigned afterwards."""
    start = loopback_target(url)
    origin = origin_of(start)
    start_route = urllib.parse.urlsplit(start).path or "/"
    playwright, browser, page, _has_touch = _with_browser(start, browser_name, profile, headed, channel)
    failure = None
    result = None
    try:
        queue = [start_route]
        seen = {start_route}
        found: list = []
        skipped = 0
        inspected = 0
        limited = False
        queue_peak = 1
        index = 0
        while index < len(queue) and len(found) < max_pages:
            route = queue[index]
            index += 1
            try:
                page.goto(origin + route, wait_until="load", timeout=30000)
            except Exception as exc:
                raise InspectError(
                    f"{type(exc).__name__}: {exc}",
                    "check that the loopback server is up",
                    stage="navigate",
                ) from exc
            found.append(route)
            anchors = page.locator("a[href]")
            link_count = anchors.count()
            budget = min(PAGE_LINK_LIMIT, TOTAL_LINK_LIMIT - inspected)
            if link_count > budget:
                limited = True
            for number in range(min(link_count, budget)):
                anchor = anchors.nth(number)
                inspected += 1
                # Attribute reads only: pages never runs page script, so observe's one-evaluate rule holds.
                link = urllib.parse.urlsplit(urllib.parse.urljoin(page.url, anchor.get_attribute("href") or ""))
                if f"{link.scheme}://{link.netloc}" != origin:
                    skipped += 1
                    continue
                path = link.path or "/"
                if path not in seen:
                    if len(queue) >= max_pages:
                        limited = True
                        continue
                    seen.add(path)
                    queue.append(path)
                    queue_peak = max(queue_peak, len(queue))
        routes = {route: ROUTE_POOL[number] for number, route in enumerate(found)}
        result = {
            "schema": "btk-page-routes/1",
            "ok": True,
            "origin": origin,
            "routes": routes,
            "pages": [{"route": route, "emoji": routes[route]} for route in found],
            "truncated": limited or index < len(queue),
            "links_inspected": inspected,
            "link_limits": {"per_page": PAGE_LINK_LIMIT, "total": TOTAL_LINK_LIMIT},
            "queue_peak": queue_peak,
            "skipped_external_links": skipped,
            "hint": "pass the routes file to observe --trail or act --action goto; link text is not recorded; query strings and fragments are ignored",
        }
    except InspectError as exc:
        failure = exc
    except Exception as exc:
        failure = InspectError(f"{type(exc).__name__}: {exc}", "run pages again", stage="inspect")
    return _finish(playwright, browser, failure, result)


def _parse_step(raw: str, index: int, origin: str | None = None, routes: dict | None = None) -> dict:
    parts = raw.strip().split(None, 2)
    if not parts:
        raise InspectError(f"step {index} is empty", "use ACTION LOCATOR [TEXT]; LOCATOR - means none")
    action = parts[0]
    if action not in {"click", "dblclick", "hover", "focus", "check", "uncheck", "fill", "press", "select", "wait_for_timeout", "settle", "goto"}:
        raise InspectError(f"step {index}: unknown action {action}", "use one of " + ", ".join(sorted(ACT_ACTIONS)))
    locator = "" if len(parts) < 2 or parts[1] == "-" else parts[1]
    text = parts[2] if len(parts) == 3 else None
    if action in ACT_TEXT_ACTIONS and text is None:
        raise InspectError(f"step {index}: {action} needs TEXT", "write ACTION LOCATOR TEXT; text is not recorded in the output")
    if action == "goto":
        text = goto_target(text, origin or "", routes)  # resolved before any browser launch
    if action == "wait_for_timeout":
        if text is None or not re.fullmatch(r"[0-9]{1,6}", text) or int(text) > 120000:
            raise InspectError(f"step {index}: wait_for_timeout needs milliseconds 0..120000 as TEXT", "write wait_for_timeout - 500")
    elif action not in ("settle", "goto") and not (action == "press" and not locator):
        _check_action_locator(locator)
    return {"action": action, "locator": locator, "text": text}


def _check_step_args(args: argparse.Namespace) -> None:
    if args.after_click is not None or args.after_out is not None:
        raise InspectError("--step and --after-click cannot be combined", "use --step for every action")
    if args.out or not args.out_dir:
        raise InspectError("--step writes one file per snapshot, so it needs --out-dir and not --out", "pass --out-dir")
    if not Path(args.out_dir).is_dir():
        raise InspectError(f"directory does not exist: {args.out_dir}", "create the directory before --out-dir")
    if len(args.step) > 20:
        raise InspectError("at most 20 steps", "split the sequence into more than one observe")


def _write_snapshot(path: str, payload: dict) -> None:
    try:
        Path(path).write_text(dumps(payload), encoding="utf-8", newline="\n")
    except OSError as exc:
        raise InspectError(f"cannot write {path}: {exc}", "choose a writable file path") from exc


def dispatch(args: argparse.Namespace) -> tuple[dict, str, int]:
    if args.command == "operations":
        payload = operations()
        return payload, operations_text(payload), 0
    if args.command == "observe":
        if args.limit < 1:
            raise InspectError("limit must be at least 1", f"use 1..{SURVEY_LIMIT_MAX}")
        if args.timeout_ms < 1:
            raise InspectError("timeout-ms must be at least 1", "use 1..120000")
        legacy = bool(args.trail is not None or (args.step and any(" " in item.split(":", 1)[0] for item in args.step)))
        start_url = args.target
        legacy_steps = []
        if args.trail is not None:
            if args.step or args.after_click is not None or args.after_action is not None:
                raise InspectError("--trail cannot be combined with other steps", "use one sequence mode")
            if not args.routes:
                raise InspectError("--trail needs --routes", "pass the routes JSON")
            if not args.out_dir:
                raise InspectError("--trail needs --out-dir", "pass an existing directory")
            routes = load_routes(args.routes)
            trail = parse_trail(args.trail)
            origin = origin_of(args.target)
            targets = [goto_target(emoji, origin, routes) for emoji in trail]
            start_url = targets[0]
            legacy_steps = [{"action": "goto", "locator": "", "text": target} for target in targets[1:]]
        elif legacy:
            _check_step_args(args)
            routes = load_routes(args.routes) if args.routes else None
            legacy_steps = [_parse_step(item, index, origin_of(args.target), routes) for index, item in enumerate(args.step, 1)]
        steps: list = []
        want_run = False
        after_action = resolve_observe_after_action(args)
        if args.step and (after_action is not None or args.after_out is not None):
            raise InspectError(
                "--step and --after-action cannot be combined",
                "pass --step for a sequence, or --after-action with --after-out for one step",
            )
        if not legacy and args.out_dir and (not args.step or after_action is not None or args.after_out is not None):
            raise InspectError(
                "--out-dir is the per-step slice of an observe --step run",
                "pass --step. A single snapshot still uses --out. --after-action keeps --out and --after-out",
            )
        if args.out_dir and not Path(args.out_dir).is_dir():
            raise InspectError(
                f"directory does not exist: {args.out_dir}",
                "create the directory before --out-dir",
            )
        if legacy:
            steps = [act_arguments_to_step(item["action"], item["locator"], item["text"], None, None, None, int(item["text"]) if item["action"] == "wait_for_timeout" else args.timeout_ms) for item in legacy_steps]
            want_run = True
        elif after_action is not None or args.after_out is not None:
            _check_sequence_args(args, after_action)
            steps = [after_action_to_step(after_action)]
        elif args.step:
            if len(args.step) > MAX_STEPS:
                raise InspectError(f"at most {MAX_STEPS} steps", "split the session into shorter observe commands")
            steps = [parse_step(item) for item in args.step]
            want_run = True
        session = observe_page(
            url=start_url, browser_name=args.browser, profile=args.profile, headed=args.headed,
            channel=args.channel or None, include_hidden=args.include_hidden,
            include_storage_keys=args.include_storage_keys,
            include_storage_fingerprints=args.include_storage_fingerprints,
            role=args.role, start=args.start, limit=args.limit, steps=steps, want_run=want_run,
            dialog_policy=args.dialog_policy, timeout_ms=args.timeout_ms, viewport=args.viewport,
            touch=args.touch,
        )
        if legacy:
            observations = session["observations"]
            for index, observation in enumerate(observations[1:], 2):
                observation["step"]["index"] = index
            files = write_observation_slices(args.out_dir, observations)
            payload = {"schema": "btk-page-observe-steps/1", "ok": session["ok"], "files": files, "steps": len(steps)}
            return payload, "\n".join(files) + "\n", 0 if session["ok"] else 2
        if want_run:
            payload = session["run"]
            if args.out_dir:
                payload = dict(payload)
                payload["files"] = write_observation_slices(args.out_dir, payload.get("observations") or [])
                payload["hint"] = (payload.get("hint") or "") + (
                    " files are the same btk-page-observe/1 objects as observations, one per snapshot. "
                    "The run envelope stays btk-page-observe-run/1; there is no second schema."
                )
            return payload, run_text(payload), 0 if payload.get("ok") else 2
        observations = session["observations"]
        if args.after_click is not None and len(observations) == 2:
            observations[1]["step"]["index"] = 2
        payload = observations[0]
        text_out = observe_text(payload)
        if len(observations) == 2:
            _write_snapshot(args.after_out, observations[1])
            step = observations[1].get("step") or {}
            label = f"{step.get('action', 'step')}:{step.get('target', '')}"
            text_out += f"after-action {label}: second snapshot written to {args.after_out}\n"
        return payload, text_out, 0
    if args.command == "scenario":
        from page_inspect_scenarios import run_scenario
        payload = run_scenario(args, sys.modules[__name__])
        return payload, observe_text(payload), 0
    if args.command == "act":
        if args.timeout_ms < 1:
            raise InspectError("timeout-ms must be at least 1", "use 1..120000")
        if args.action == "goto":
            args.text = goto_target(args.text, origin_of(args.target), load_routes(args.routes) if args.routes else None)
        payload = act_page(
            url=args.target, browser_name=args.browser, profile=args.profile, headed=args.headed,
            channel=args.channel or None, action=args.action, locator=args.locator,
            text=args.text, timeout_ms=args.timeout_ms, text_env=args.text_env, text_file=args.text_file,
            to=args.to, touch=args.touch, viewport=args.viewport, dialog_policy=args.dialog_policy,
        )
        text_out = f"action={payload['action']} status={payload.get('status')} url={payload['url']}\nhint: {payload['hint']}\n"
        return payload, text_out, 0
    if args.command == "pages":
        if args.max_pages < 1:
            raise InspectError("max-pages must be at least 1", "use 1..%d" % len(ROUTE_POOL))
        if args.routes_out and not Path(args.routes_out).parent.is_dir():
            raise InspectError(f"directory does not exist: {Path(args.routes_out).parent}", "create the directory before --routes-out")
        payload = discover_pages(url=args.target, browser_name=args.browser, profile=args.profile, headed=args.headed,
                                 channel=args.channel or None, max_pages=args.max_pages)
        if args.routes_out:
            try:
                Path(args.routes_out).write_text(json.dumps(payload["routes"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except OSError as exc:
                raise InspectError(f"cannot write {args.routes_out}: {exc}", "choose a writable file path") from exc
            payload["routes_file"] = args.routes_out
        text_out = "".join(f"{page['emoji']} {page['route']}\n" for page in payload["pages"])
        return payload, text_out, 0
    if args.command == "compare":
        payload = compare_snapshots(read_snapshot(args.left), read_snapshot(args.right))
        return payload, compare_text(payload), 0
    snapshot = read_snapshot(args.snapshot)
    elements = snapshot["elements"]
    if args.command == "count":
        payload = count_elements(elements, query=args.query, role=args.role, threshold=args.threshold)
        return payload, count_text(payload), 0
    if args.command == "locate":
        if args.limit < 1:
            raise InspectError("limit must be at least 1", f"use 1..{PAGE_LIMIT_MAX}")
        payload = locate(
            elements, query=args.query, role=args.role, threshold=args.threshold,
            start=args.start, limit=args.limit, unique=args.unique,
        )
        return payload, locate_text(payload), 0 if payload["ok"] else 1
    if args.context < 0:
        raise InspectError("context must be at least 0", f"use 0..{CONTEXT_MAX}")
    payload = describe_element(elements, args.target, context=args.context)
    return payload, describe_text(payload), 0 if payload["ok"] else 1


def observation_file_name(index: int, observation: dict) -> str:
    """00-initial.json, then NN-action.json. The index keeps repeated actions distinct."""
    if index == 0:
        return "00-initial.json"
    step = observation.get("step") if isinstance(observation, dict) else None
    action = step.get("action") if isinstance(step, dict) else None
    slug = re.sub(r"[^a-z0-9-]", "-", str(action or "step"))
    return f"{index:02d}-{slug}.json"


def write_observation_slices(directory: str, observations: list) -> list[str]:
    """Write each btk-page-observe/1 object already stored on the run. No second schema."""
    files = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict) or observation.get("schema") != SCHEMA_OBSERVE:
            continue
        path = str(Path(directory) / observation_file_name(index, observation))
        _write_snapshot(path, observation)
        files.append(path)
    return files


def emit(payload: dict, text: str, code: int, args: argparse.Namespace) -> int:
    payload = redact_obj(payload)
    text = redact_text(text)
    raw = dumps(payload)
    if args.out:
        path = Path(args.out)
        if not path.parent.exists():
            sys.stderr.write(f"error: directory does not exist: {path.parent}\nhint: create the directory before --out\n")
            return 2
        try:
            path.write_text(raw, encoding="utf-8", newline="\n")
        except OSError as exc:
            sys.stderr.write(f"error: cannot write {path}: {exc}\nhint: choose a writable file path\n")
            return 2
    if args.json or not payload.get("ok", True):
        if args.json:
            sys.stdout.write(raw)
        else:
            sys.stderr.write(text)
    else:
        sys.stdout.write(text)
    return code


def page_main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code)
    try:
        payload, text, code = dispatch(args)
    except InspectError as exc:
        payload = error_envelope(args.command, exc)
        return emit(payload, error_text(payload), exc.code, args)
    return emit(payload, text, code, args)


# ---- command line ----------------------------------------------------------------------

FETCH_COMMANDS = ("fetch",)
PLAN_COMMANDS = ("plan",)
PAGE_COMMANDS = ("observe", "act", "pages", "scenario", "locate", "count", "describe", "compare", "operations")
COMMANDS = FETCH_COMMANDS + PLAN_COMMANDS + PAGE_COMMANDS


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write("usage: btk.py {" + ",".join(COMMANDS) + "} ...\n")
        return 0 if argv else 2
    if argv[0] in FETCH_COMMANDS:
        return fetch_cli(argv[1:])
    if argv[0] in PLAN_COMMANDS:
        return plan_cli(argv[1:])
    if argv[0] in PAGE_COMMANDS:
        return page_main(argv)
    sys.stderr.write(f"btk.py: unknown command {argv[0]!r}; expected one of {COMMANDS}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
