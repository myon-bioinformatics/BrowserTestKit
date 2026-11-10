#!/usr/bin/env python3
"""Read-only cgroup v2 telemetry and an isolated stdlib child-command meter."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import time
from threading import Event, Thread


def reading(value=None, reason=None):
    return {'value': value, 'status': 'unmeasured' if value is None else 'measured',
            'reason': reason}


def read_cgroup(root):
    """Explicit path only: never guess host visibility from /proc/self/cgroup."""
    root = Path(root) if root is not None else None
    result = {}
    for name in ('cpu.stat', 'cpu.max', 'memory.current', 'memory.peak',
                 'memory.max', 'memory.events'):
        try:
            if root is None:
                raise ValueError('no cgroup path supplied')
            text = (root / name).read_text().strip()
            if name in ('cpu.stat', 'memory.events'):
                value = dict((key, int(number)) for key, number in
                             (line.split() for line in text.splitlines()))
                if not value or any(number < 0 for number in value.values()):
                    raise ValueError('invalid counter')
            elif name == 'cpu.max':
                quota, period = text.split()
                value = {'quota_usec': None if quota == 'max' else int(quota),
                         'period_usec': int(period)}
                if value['period_usec'] <= 0 or (quota != 'max' and int(quota) <= 0):
                    raise ValueError('invalid CPU quota')
            else:
                value = text if text == 'max' and name == 'memory.max' else int(text)
                if isinstance(value, int) and value < 0:
                    raise ValueError('negative memory')
            result[name] = reading(value)
        except (OSError, ValueError) as exc:
            result[name] = reading(reason=f'{type(exc).__name__}: {exc}')
    return result


def counter_delta(before, after, name, key):
    a, b = before[name]['value'], after[name]['value']
    if a is None or b is None or key not in a or key not in b:
        return reading(reason='counter unavailable')
    if b[key] < a[key]:
        return reading(reason='counter decreased; cgroup reset or replaced')
    return reading(b[key] - a[key])


class ScopeMeter:
    """Sample the supplied aggregate scope; unrelated members remain included."""
    def __init__(self, root=None, interval=0.05):
        if interval <= 0:
            raise ValueError('sampling interval must be positive')
        self.root, self.interval = root, interval
        self.stop_event = Event()
        self.samples = []

    def sample(self):
        current = read_cgroup(self.root)['memory.current']['value']
        if current is not None:
            self.samples.append(current)

    def start(self):
        self.before = read_cgroup(self.root)
        self.sample()
        def loop():
            while not self.stop_event.wait(self.interval):
                self.sample()
        self.thread = Thread(target=loop, daemon=True)
        self.thread.start()
        return self

    def finish(self):
        self.stop_event.set()
        self.thread.join()
        self.sample()
        after = read_cgroup(self.root)
        return {'scope': str(self.root) if self.root is not None else None,
                'budget_verified': False,
                'physical_host_peak_memory_bytes': reading(reason='physical host high-water mark unavailable'),
                'before': self.before, 'after': after,
                'sample_interval_seconds': self.interval,
                'samples': len(self.samples),
                'sampled_peak_memory_bytes': reading(max(self.samples) if self.samples else None,
                    None if self.samples else 'memory.current unavailable'),
                'peak_scope': 'supplied cgroup including all descendants; sampled lower bound; not physical host RAM',
                'cpu_usage_usec': counter_delta(self.before, after, 'cpu.stat', 'usage_usec'),
                'throttled_usec': counter_delta(self.before, after, 'cpu.stat', 'throttled_usec'),
                'nr_throttled': counter_delta(self.before, after, 'cpu.stat', 'nr_throttled')}


def measure(command, output, cgroup=None):
    """Separate interpreter avoids process-global RUSAGE_CHILDREN races in threads.

    CPU covers waited descendants. ru_maxrss is a maximum individual process RSS,
    never a simultaneous sum of the command tree. Detached descendants are excluded.
    """
    started = time.monotonic()
    try:
        import resource
    except ImportError:
        resource = None
    meter = ScopeMeter(cgroup).start()
    try:
        code = subprocess.call(command)
    finally:
        scope = meter.finish()
    report = {'wall_seconds': time.monotonic() - started, 'returncode': code,
              'cpu_seconds': reading(reason='resource module unavailable'),
              'peak_process_rss_bytes': reading(reason='RSS unit unsupported'),
              'worker_throttled_usec': reading(reason='no dedicated worker cgroup'),
              'scope': 'waited command descendants; RSS is largest process, not tree peak',
              'cgroup': scope}
    if resource is not None:
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        report['cpu_seconds'] = reading(usage.ru_utime + usage.ru_stime)
        if sys.platform in ('linux', 'darwin'):
            report['peak_process_rss_bytes'] = reading(int(usage.ru_maxrss) *
                                                     (1024 if sys.platform == 'linux' else 1))
    Path(output).write_text(json.dumps(report, indent=2) + '\n')
    return code


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cgroup', type=Path, help='explicit read-only cgroup v2 scope')
    parser.add_argument('output', type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command[:1] == ['--']:
        command = command[1:]
    if not command:
        parser.error('a command after OUTPUT -- is required')
    code = measure(command, args.output, args.cgroup)
    return code if code >= 0 else 128 - code


if __name__ == '__main__':
    raise SystemExit(main())
