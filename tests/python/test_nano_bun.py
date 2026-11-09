"""Offline contracts for the opt-in Bun installer; never launch a real installer."""
import copy, importlib.util, json, os, tempfile, unittest
from pathlib import Path; from types import SimpleNamespace; from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("nano_bun", ROOT / "tools" / "nano_bun.py")
nano = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nano)

TITLES = [
    "markdown-it and marked agree on the CommonMark fixture",
    "HTML reader emits complete facts accepted by compare",
]


def report():
    return {"errors": [], "suites": [{"title": "mirror.spec.ts", "specs": [
        {"title": title, "ok": True, "tests": [{"status": "expected", "results": [{"status": "passed"}]}]}
        for title in TITLES
    ]}]}


class InstallCommandTests(unittest.TestCase):
    def command(self, network=False):
        return [str(x) for x in nano.install_command(
            Path("/tools/bun"), Path("/private/project"),
            Path("/private/cache"), Path("/private/bunfig.toml"), network)]

    def test_offline_frozen_and_no_lifecycle_are_default(self):
        command = self.command()
        self.assertEqual(command[:2], ["/tools/bun", "install"])
        for flag in ("--offline", "--frozen-lockfile", "--ignore-scripts", "--no-env-file"):
            self.assertIn(flag, command)
        self.assertNotIn("--no-verify", command)
        self.assertNotIn("--force", command)
        self.assertNotIn("--production", command)

    def test_all_value_flags_use_documented_equals_form(self):
        command = self.command()
        self.assertIn("--linker=hoisted", command)
        self.assertIn("--registry=https://registry.npmjs.org/", command)
        self.assertIn("--cache-dir=/private/cache", command)
        self.assertIn("--config=/private/bunfig.toml", command)
        for flag in ("--linker", "--registry", "--cache-dir", "--config"):
            self.assertNotIn(flag, command)

    def test_explicit_network_mode_only_removes_offline_restriction(self):
        self.assertEqual([x for x in self.command() if x != "--offline"], self.command(True))

    def test_no_runtime_runner_or_implicit_fetch_fallback(self):
        command = self.command()
        self.assertFalse(any(x in command for x in ("npx", "bunx", "npm", "test", "run", "add")))


class ReportTests(unittest.TestCase):
    def test_two_original_cases_are_accepted(self):
        self.assertEqual(nano.validate_test_report(report()), {"unique_tests": 2, "passed": 2, "skipped": 0})

    def test_empty_or_missing_case_is_rejected(self):
        for specs in ([], report()["suites"][0]["specs"][:1]):
            value = report()
            value["suites"][0]["specs"] = specs
            with self.subTest(count=len(specs)), self.assertRaises(nano.Error):
                nano.validate_test_report(value)

    def test_duplicate_case_cannot_replace_other_case(self):
        value = report()
        value["suites"][0]["specs"][1] = copy.deepcopy(value["suites"][0]["specs"][0])
        with self.assertRaises(nano.Error):
            nano.validate_test_report(value)

    def test_extra_case_is_not_silently_ignored(self):
        value = report()
        extra = copy.deepcopy(value["suites"][0]["specs"][0])
        extra["title"] = "ExcelJS case outside the selected environment"
        value["suites"][0]["specs"].append(extra)
        with self.assertRaises(nano.Error):
            nano.validate_test_report(value)

    def test_skip_failure_timeout_or_retry_is_not_success(self):
        for status in ("skipped", "failed", "timedOut", "interrupted"):
            value = report()
            value["suites"][0]["specs"][0]["tests"][0]["results"][0]["status"] = status
            with self.subTest(status=status), self.assertRaises(nano.Error):
                nano.validate_test_report(value)
        value = report()
        value["suites"][0]["specs"][0]["tests"][0]["results"].insert(0, {"status": "failed"})
        with self.assertRaises(nano.Error):
            nano.validate_test_report(value)

    def test_global_runner_error_is_rejected_even_if_cases_pass(self):
        value = report()
        value["errors"] = [{"message": "worker teardown failed"}]
        with self.assertRaises(nano.Error):
            nano.validate_test_report(value)


class TemporaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def executable(self, name):
        path = self.root / name
        path.write_bytes(name.encode())
        path.chmod(0o700)
        return path


class EnvironmentTests(TemporaryTests):
    def test_untrusted_configuration_is_not_inherited(self):
        job = self.root / "job"
        job.mkdir()
        inherited = {"HOME": "/wrong/home", "PATH": "/wrong/bin", "NODE_OPTIONS": "--require evil.js",
                     "NODE_PATH": "/wrong/modules", "NODE_TLS_REJECT_UNAUTHORIZED": "0",
                     "npm_config_registry": "https://wrong.invalid/", "BUN_INSTALL_CACHE_DIR": "/wrong/cache",
                     "BUN_OPTIONS": "--bun", "SECRET_TOKEN": "not-to-be-inherited",
                     "HTTPS_PROXY": "http://proxy.invalid", "NO_PROXY": "localhost"}
        with mock.patch.dict(os.environ, inherited, clear=True):
            env = nano.clean_env(job, job / "bin")
        for name in inherited:
            if name not in ("HOME", "PATH", "HTTPS_PROXY", "NO_PROXY"):
                self.assertNotIn(name, env)
        self.assertEqual(env["PATH"], str(job / "bin"))
        self.assertEqual(env["HOME"], str(job / "home"))
        self.assertEqual(env["HTTPS_PROXY"], inherited["HTTPS_PROXY"])
        self.assertEqual(env["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"], "1")
        self.assertEqual(env["PLAYWRIGHT_BROWSERS_PATH"], str(job / "browsers"))

    def test_existing_readable_ca_is_preserved_without_disabling_tls(self):
        ca = self.root / "existing-ca.pem"
        ca.write_text("test-only certificate fixture")
        job = self.root / "job"
        job.mkdir()
        with mock.patch.dict(os.environ, {"NODE_EXTRA_CA_CERTS": str(ca), "SSL_CERT_FILE": str(ca)}, clear=True):
            env = nano.clean_env(job, job / "bin")
        self.assertEqual(env["NODE_EXTRA_CA_CERTS"], str(ca))
        self.assertEqual(env["SSL_CERT_FILE"], str(ca))
        self.assertNotIn("NODE_TLS_REJECT_UNAUTHORIZED", env)

    def test_relative_or_missing_ca_is_rejected(self):
        for ca in ("relative.pem", str(self.root / "missing.pem")):
            with self.subTest(ca=ca), mock.patch.dict(os.environ, {"SSL_CERT_FILE": ca}, clear=True):
                with self.assertRaises(nano.Error):
                    nano.clean_env(self.root, self.root / "bin")


class PreparedInputTests(TemporaryTests):
    def setUp(self):
        super().setUp()
        self.prepared = self.root / "prepared"
        project = self.prepared / "browser-test-kit"
        self.contents = {name: ("fixture " + name).encode() for name in nano.SOURCE_BLOBS}
        self.contents.update({"package.json": b'{"private":true}\n', "package-lock.json": b'{"lockfileVersion":3}\n'})
        blobs = {name: nano.hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                 for name, data in self.contents.items() if name not in ("package.json", "package-lock.json")}
        for name, data in self.contents.items():
            destination = project / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        (self.prepared / "receipt.json").write_text(json.dumps({"feature": nano.FEATURE, "source_commit": nano.SOURCE_COMMIT}))
        for name, value in (("SOURCE_BLOBS", blobs),
                            ("MANIFEST_SHA256", nano.sha256(self.contents["package.json"])),
                            ("LOCK_SHA256", nano.sha256(self.contents["package-lock.json"]))):
            patcher = mock.patch.object(nano, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_exact_nine_file_snapshot_is_read_without_running_commands(self):
        with mock.patch.object(nano.subprocess, "run") as run:
            self.assertEqual(nano.read_prepared(self.prepared), self.contents)
        run.assert_not_called()
        self.assertEqual(len(self.contents), 9)

    def test_manifest_lock_and_source_tampering_each_fail(self):
        for name in ("package.json", "package-lock.json", "tools/mirror_facts.mjs"):
            path = self.prepared / "browser-test-kit" / name
            original = path.read_bytes()
            path.write_bytes(original + b"tamper")
            with self.subTest(name=name), self.assertRaisesRegex(nano.Error, "mismatch"):
                nano.read_prepared(self.prepared)
            path.write_bytes(original)

    def test_wrong_receipt_source_or_feature_is_rejected(self):
        for receipt in ({"feature": "wrong", "source_commit": nano.SOURCE_COMMIT},
                        {"feature": nano.FEATURE, "source_commit": "0" * 40}):
            (self.prepared / "receipt.json").write_text(json.dumps(receipt))
            with self.subTest(receipt=receipt), self.assertRaises(nano.Error):
                nano.read_prepared(self.prepared)

    def test_matching_bytes_through_symlink_are_rejected(self):
        name = "package-lock.json"
        target = self.root / "outside.json"
        target.write_bytes(self.contents[name])
        path = self.prepared / "browser-test-kit" / name
        path.unlink()
        path.symlink_to(target)
        with self.assertRaisesRegex(nano.Error, "symlink"):
            nano.read_prepared(self.prepared)


def runtime():
    return {"platform": "linux", "arch": "x64", "bun_version": "1.4.4", "bun_sha256": "a" * 64,
            "node_version": "v24.22.1", "node_abi": "137", "node_sha256": "b" * 64,
            "python_version": [3, 12, 14]}


class CacheTests(TemporaryTests):
    def test_key_is_stable_and_segregates_all_runtime_dimensions(self):
        base = runtime()
        self.assertEqual(nano.cache_key(base), nano.cache_key(dict(reversed(list(base.items())))))
        for name in ("platform", "arch", "bun_version", "bun_sha256", "node_version", "node_abi", "node_sha256"):
            changed = dict(base, **{name: str(base[name]) + "-different"})
            with self.subTest(name=name):
                self.assertNotEqual(nano.cache_key(base), nano.cache_key(changed))
        for name in ("LOCK_SHA256", "BUN_LOCK_SHA256"):
            expected = nano.cache_key(base)
            with mock.patch.object(nano, name, "c" * 64):
                self.assertNotEqual(expected, nano.cache_key(base))

    @unittest.skipUnless(os.name == "posix", "flock contract is Linux-only")
    def test_busy_key_fails_and_failed_key_is_quarantined(self):
        cache = self.root / "cache"
        key = nano.cache_key(runtime())
        with nano.cache_lock(cache, key) as packages:
            self.assertEqual(packages, cache / key / "packages")
            self.assertTrue((cache / key / "active.json").is_file())
            with self.assertRaisesRegex(nano.Error, "busy"):
                with nano.cache_lock(cache, key):
                    self.fail("second writer acquired shared cache")
        self.assertFalse((cache / key / "active.json").exists())
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            with nano.cache_lock(cache, key):
                raise RuntimeError("synthetic")
        self.assertTrue((cache / key / "active.json").is_file())
        # An inspection error (not busy) establishes both released OS lock and
        # preserved quarantine. No PID guessing or automatic marker deletion.
        with self.assertRaisesRegex(nano.Error, "inspection"):
            with nano.cache_lock(cache, key):
                self.fail("failed cache was reused")

    def test_stale_marker_from_force_killed_job_is_never_deleted(self):
        cache = self.root / "cache"
        key = nano.cache_key(runtime())
        with nano.cache_lock(cache, key):
            pass
        marker = cache / key / "active.json"
        marker.write_text('{"pid": 999999999, "started": 0}')
        before = marker.read_bytes()
        with self.assertRaisesRegex(nano.Error, "inspection"):
            with nano.cache_lock(cache, key):
                pass
        self.assertEqual(marker.read_bytes(), before)

    def test_world_writable_or_symlink_cache_is_rejected(self):
        cache = self.root / "unsafe"
        cache.mkdir(mode=0o777)
        cache.chmod(0o777)
        with self.assertRaises(nano.Error):
            with nano.cache_lock(cache, "a" * 64):
                pass
        linked = self.root / "linked"
        linked.symlink_to(cache, target_is_directory=True)
        with self.assertRaises(nano.Error):
            with nano.cache_lock(linked, "a" * 64):
                pass


class RuntimeTests(TemporaryTests):
    def setUp(self):
        super().setUp()
        self.node, self.bun, self.python = [self.executable(name) for name in ("node", "bun", "python")]
        self.node_info = {"release": "node", "bun": False, "version": "v24.22.1", "abi": "137",
                          "platform": "linux", "arch": "x64", "execPath": str(self.node)}

    def probe(self, info=None, python=None, bun_version=None):
        values = [bun_version or nano.BUN_VERSION, json.dumps(info or self.node_info), json.dumps(python or [3, 12, 14])]
        with mock.patch.object(nano.platform, "system", return_value="Linux"), \
             mock.patch.object(nano.platform, "machine", return_value="x86_64"), \
             mock.patch.object(nano, "BUN_SHA256", nano.sha256(self.bun.read_bytes())), \
             mock.patch.object(nano.subprocess, "run", side_effect=[SimpleNamespace(stdout=v) for v in values]) as command:
            result = nano.probe_runtime(self.node, self.bun, self.python, {"HOME": str(self.root)})
        self.assertTrue(all(call.kwargs["timeout"] == 15 for call in command.call_args_list))
        return result

    def test_explicit_node_and_abi_are_recorded(self):
        value = self.probe()
        self.assertEqual(value["node_version"], "v24.22.1")
        self.assertEqual(value["node_abi"], "137")

    def test_wrong_runtime_release_bun_launcher_or_old_node_is_rejected(self):
        for field, value in (("release", "bun"), ("bun", True), ("execPath", "/other/node"),
                             ("version", "v22.0.0"), ("abi", ""), ("platform", "darwin"), ("arch", "arm64")):
            with self.subTest(field=field), self.assertRaises(nano.Error):
                self.probe(dict(self.node_info, **{field: value}))

    def test_wrong_bun_version_or_old_python_is_rejected(self):
        with self.assertRaises(nano.Error):
            self.probe(bun_version="1.4.3")
        for version in ([3, 8, 20], [3, 9, 25], [3, 10, 19]):
            with self.subTest(version=version), self.assertRaisesRegex(nano.Error, "Python 3.11"):
                self.probe(python=version)
        self.assertEqual(self.probe(python=[3, 11, 0])["python_version"], [3, 11, 0])

    def test_wrong_bun_bytes_are_rejected_before_execution(self):
        with mock.patch.object(nano.platform, "system", return_value="Linux"), \
             mock.patch.object(nano.platform, "machine", return_value="x86_64"), \
             mock.patch.object(nano.subprocess, "run") as command:
            with self.assertRaisesRegex(nano.Error, "unreviewed Bun"):
                nano.probe_runtime(self.node, self.bun, self.python, {"HOME": str(self.root)})
        command.assert_not_called()


class FailureFlowTests(TemporaryTests):
    def test_offline_cache_miss_preserves_failure_and_never_falls_back(self):
        binaries = [self.executable(name) for name in ("node", "bun", "python")]
        args = SimpleNamespace(prepared=self.root / "prepared", work_root=self.root / "jobs",
                               cache_root=self.root / "cache", node=binaries[0], bun=binaries[1],
                               python=binaries[2], allow_network=False)
        snapshot = {"package.json": b"{}", "package-lock.json": b'{"packages":{}}'}
        lock = b"reviewed-lock"
        calls = []
        def execute(command, project, env, job, label):
            calls.append((label, command))
            if label == "migration":
                (project / "bun.lock").write_bytes(lock)
            elif label == "install":
                raise nano.Error("offline cache miss")
            else:
                self.fail("test runtime was launched after failed install")
        info = dict(runtime(), node_sha256=nano.sha256(binaries[0].read_bytes()))
        with mock.patch.object(nano.platform, "system", return_value="Linux"), \
             mock.patch.object(nano.platform, "machine", return_value="x86_64"), \
             mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(nano, "read_prepared", return_value=snapshot), \
             mock.patch.object(nano, "probe_runtime", return_value=info), \
             mock.patch.object(nano, "BUN_LOCK_SHA256", nano.sha256(lock)), \
             mock.patch.object(nano, "execute", side_effect=execute), \
             mock.patch.object(nano, "validate_inventory") as inventory:
            with self.assertRaisesRegex(nano.Error, "cache miss"):
                nano.run(args)
        self.assertEqual([label for label, _ in calls], ["migration", "install"])
        self.assertIn("--offline", calls[1][1])
        inventory.assert_not_called()
        jobs = list(args.work_root.iterdir())
        self.assertEqual(len(jobs), 1)
        self.assertIn("cache miss", (jobs[0] / "failure.txt").read_text())
        self.assertEqual((jobs[0] / "bunfig.toml").read_text(), "")
        self.assertFalse((jobs[0] / "project" / "node_modules").exists())

    def test_unreviewed_migrated_lock_stops_before_install(self):
        binaries = [self.executable(name) for name in ("node", "bun", "python")]
        args = SimpleNamespace(prepared=self.root / "prepared", work_root=self.root / "jobs",
                               cache_root=self.root / "cache", node=binaries[0], bun=binaries[1],
                               python=binaries[2], allow_network=False)
        labels = []
        def execute(command, project, env, job, label):
            labels.append(label)
            self.assertEqual(label, "migration")
            (project / "bun.lock").write_bytes(b"unreviewed lock")
        with mock.patch.object(nano.platform, "system", return_value="Linux"), \
             mock.patch.object(nano.platform, "machine", return_value="x86_64"), \
             mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(nano, "read_prepared", return_value={"package.json": b"{}"}), \
             mock.patch.object(nano, "probe_runtime", return_value=runtime()), \
             mock.patch.object(nano, "execute", side_effect=execute):
            with self.assertRaisesRegex(nano.Error, "Bun lock differs"):
                nano.run(args)
        self.assertEqual(labels, ["migration"])


class InventoryTests(TemporaryTests):
    def setUp(self):
        super().setUp()
        self.project = self.root / "project"
        self.lock = {"packages": {
            "": {"devDependencies": {"root-reader": "1.0.0"}},
            "node_modules/root-reader": {"version": "1.0.0", "dependencies": {"leaf": "1.0.0"}},
            "node_modules/leaf": {"version": "1.0.0"},
        }}
        self.write_package("root-reader", "1.0.0", {"leaf": "1.0.0"})
        self.write_package("leaf", "1.0.0")

    def write_package(self, name, version, dependencies=None):
        path = self.project / "node_modules" / name / "package.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"name": name, "version": version}
        if dependencies is not None:
            data["dependencies"] = dependencies
        path.write_text(json.dumps(data))

    def test_exact_identities_and_resolved_graph_are_accepted(self):
        self.assertEqual(nano.validate_inventory(self.project, self.lock),
                         {"package_copies": 2, "unique_identities": 2, "dependency_edges": 1})

    def test_extra_identity_or_upgraded_version_is_rejected(self):
        self.write_package("leaf", "2.0.0")
        with self.assertRaisesRegex(nano.Error, "identities"):
            nano.validate_inventory(self.project, self.lock)
        self.write_package("leaf", "1.0.0")
        self.write_package("canvas", "3.0.0")
        with self.assertRaisesRegex(nano.Error, "identities"):
            nano.validate_inventory(self.project, self.lock)

    def test_same_versions_with_changed_graph_are_rejected(self):
        self.write_package("root-reader", "1.0.0", {})
        with self.assertRaisesRegex(nano.Error, "graph"):
            nano.validate_inventory(self.project, self.lock)

    def test_hidden_package_store_is_not_ignored(self):
        (self.project / "node_modules" / ".bun").mkdir()
        with self.assertRaisesRegex(nano.Error, "hidden"):
            nano.validate_inventory(self.project, self.lock)

    def test_nested_content_in_bin_directory_is_rejected(self):
        (self.project / "node_modules" / ".bin" / "hidden-package").mkdir(parents=True)
        with self.assertRaisesRegex(nano.Error, "nested .bin"):
            nano.validate_inventory(self.project, self.lock)

    def test_link_to_shared_node_modules_is_rejected(self):
        linked_project = self.root / "linked-project"
        linked_project.mkdir()
        (linked_project / "node_modules").symlink_to(self.project / "node_modules", target_is_directory=True)
        with self.assertRaisesRegex(nano.Error, "private node_modules"):
            nano.validate_inventory(linked_project, self.lock)


class CancellationTests(TemporaryTests):
    def signal_state(self):
        handlers = {nano.signal.SIGINT: "original-int", nano.signal.SIGTERM: "original-term"}
        def install(sig, handler):
            previous = handlers[sig]
            handlers[sig] = handler
            return previous
        return handlers, install

    def test_sigterm_handler_is_temporary_and_restored_after_failure(self):
        handlers, install = self.signal_state()
        original = dict(handlers)
        with mock.patch.object(nano.signal, "signal", side_effect=install):
            with self.assertRaises(nano.Error):
                with nano.cancellation():
                    handlers[nano.signal.SIGTERM](nano.signal.SIGTERM, None)
        self.assertEqual(handlers, original)

    def test_interrupt_kills_and_reaps_child_group_before_releasing_cache(self):
        job = self.root / "job"
        job.mkdir()
        process = mock.Mock(pid=123456789)
        events = []
        def wait(timeout):
            events.append(("wait", timeout))
            if timeout == 120:
                raise KeyboardInterrupt()
            return -9
        process.wait.side_effect = wait
        handlers, install = self.signal_state()
        original_handlers = dict(handlers)
        original_close = os.close
        def close(fd):
            events.append(("close", fd))
            original_close(fd)
        cache = self.root / "cache"
        key = nano.cache_key(runtime())
        with mock.patch.object(nano.subprocess, "Popen", return_value=process) as popen, \
             mock.patch.object(nano.os, "killpg", side_effect=lambda pid, sig: events.append(("kill", sig))), \
             mock.patch.object(nano.os, "close", side_effect=close), \
             mock.patch.object(nano.signal, "signal", side_effect=install), \
             mock.patch.object(nano.time, "sleep"):
            with self.assertRaises(KeyboardInterrupt):
                with nano.cache_lock(cache, key):
                    nano.execute(["/fake/bun", "install"], job, {}, job, "install")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        kills = [event for event in events if event[0] == "kill"]
        self.assertEqual(kills, [("kill", nano.signal.SIGTERM), ("kill", nano.signal.SIGKILL)])
        self.assertLess(events.index(("kill", nano.signal.SIGKILL)), events.index(("wait", 10)))
        self.assertLess(events.index(("wait", 10)), next(i for i, event in enumerate(events) if event[0] == "close"))
        self.assertEqual(handlers, original_handlers)
        self.assertEqual(json.loads((job / "install.json").read_text())["returncode"], "cancelled")
        self.assertTrue((cache / key / "active.json").exists())


if __name__ == "__main__":
    unittest.main()
