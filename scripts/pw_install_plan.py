#!/usr/bin/env python3
"""Recommend the lightest ``playwright install`` for each workflow job of a checkout.

``playwright install chromium`` downloads both the full Chromium and the headless
shell. Headless launches (the default, ``playwright screenshot``/``pdf``, pytest/Node
code without ``headless=False``) use only the shell; a config with
``channel: 'chromium'`` (new headless) or ``headless: false`` uses only the full
build. A job that launches bundled Chromium both ways (a collect/launch without
``--headed``, and another with ``--headed``) needs both downloads, so the install
line should carry neither flag. ``--channel`` selects a branded browser and uses
neither build. For every install line in ``.github/workflows`` this looks at what
the same job runs afterwards and at the Playwright configs, then prints one line::

    ok      .github/workflows/ci.yml:22 --only-shell (job runs headless only)
    suggest .github/workflows/portal.yml:37 add --no-shell (config uses channel 'chromium')
    ok      .github/workflows/matrix.yml:40 full + shell (job uses both the full build and the shell)
    warn    .github/workflows/e2e.yml:40 installs firefox but no config project uses it

Heuristics, not a proof: commands are matched by text and configs by regex.
Stdlib only; runs under ``python -S``::

    python -S scripts/pw_install_plan.py [ROOT]

Exit codes: 0 = every install line already matches; 1 = a suggestion; 2 = usage error.
"""
from __future__ import annotations

import os, re, sys

INSTALL = re.compile(r"playwright(?:@[\w.]+)?\s+install\b([^\n]*)")
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


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", nargs="?", default=".", help="checkout to inspect (default: .)")
    root = parser.parse_args(argv).root
    if not os.path.isdir(root):
        print(f"pw_install_plan: not a directory: {root}", file=sys.stderr)
        return 2
    rows = plan(root)
    for state, where, text in rows:
        print(f"{state:7} {where} {text}")
    if not rows:
        print("no playwright install lines in .github/workflows")
    return 1 if any(state == "suggest" for state, _, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
