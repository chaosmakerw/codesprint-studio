"""Audit regression tests use synthetic secrets and disposable Git repositories."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_public.py"
SPEC = importlib.util.spec_from_file_location("public_audit", MODULE_PATH)
audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(audit)


class PublicPathTests(unittest.TestCase):
    def test_known_public_code_is_allowed(self):
        self.assertIsNone(audit.path_issue("src/codesprint/server.py"))
        self.assertIsNone(audit.path_issue(".env.example"))
        self.assertIsNone(audit.path_issue("prompts/question-author.md"))

    def test_private_and_unlisted_root_files_are_rejected(self):
        self.assertEqual("outside-public-allowlist", audit.path_issue("notes.md"))
        self.assertIsNotNone(audit.path_issue("data/private.json"))

    def test_database_and_key_inside_public_directory_are_rejected(self):
        self.assertEqual("private-data-or-binary", audit.path_issue("docs/sample.sqlite3"))
        self.assertEqual("private-data-or-binary", audit.path_issue("examples/signing.p12"))

    def test_screenshots_have_a_narrow_allowlist(self):
        self.assertIsNone(audit.path_issue("docs/screenshots/studio.png"))
        self.assertIsNotNone(audit.path_issue("web/background.png"))
        self.assertIsNotNone(audit.path_issue("docs/screenshots/nested/private.png"))

    def test_traversal_and_private_environment_are_rejected(self):
        self.assertEqual("unsafe-path", audit.path_issue("docs/../../private.md"))
        self.assertEqual("private-environment-file", audit.path_issue("examples/.env.local"))

    def test_shijie_has_an_exact_app_and_android_allowlist(self):
        for name in (
            "apps/shijie/run.py", "apps/shijie/web/offline-store.js",
            "apps/shijie/.gitignore", "apps/shijie/CONTEXT.md",
            "apps/shijie/examples/mobile-seed.json",
            "apps/shijie/examples/java-demo/src/main/java/demo/study/OrderTools.java",
            "apps/shijie/tests/offline/test_offline.cjs",
            "apps/shijie/tests/offline/store-contract.test.cjs",
            "apps/shijie/android/src/io/shijie/learning/MainActivity.java",
        ):
            with self.subTest(name=name):
                self.assertIsNone(audit.path_issue(name))
        for name in (
            "apps/other/run.py", "apps/shijie/private.md", "apps/shijie/web/extra.json",
            "apps/shijie/examples/private-project/source.py",
            "apps/shijie/examples/java-demo/private-notes.json",
            "apps/shijie/android/local.properties",
            "apps/shijie/scripts/verify_upgrade_041.cjs",
        ):
            with self.subTest(name=name):
                self.assertEqual("outside-public-allowlist", audit.path_issue(name))

    def test_nested_shijie_runtime_data_and_signers_are_rejected(self):
        for name in (
            "apps/shijie/data/study.json", "apps/shijie/output/results.json",
            "apps/shijie/android/build/seed.json", "apps/shijie/android/private-signing/password.txt",
            "apps/shijie/.qa/local.json", "apps/shijie/android/dist/sample.apk",
        ):
            with self.subTest(name=name):
                self.assertEqual("private-or-generated-directory", audit.path_issue(name))
        self.assertEqual("private-data-or-binary", audit.path_issue("apps/shijie/android/sample.apk"))

    def test_nested_screenshot_is_limited_to_one_level(self):
        self.assertIsNone(audit.path_issue("apps/shijie/docs/screenshots/example.png"))
        self.assertIsNotNone(audit.path_issue("apps/shijie/docs/screenshots/private/example.png"))


class ContentTests(unittest.TestCase):
    def test_example_tokens_and_environment_variables_are_allowed(self):
        blob = b"AI_API_KEY=YOUR_KEY\nAuthorization: Bearer EXAMPLE_TOKEN\ncontact@example.com"
        self.assertEqual([], audit.scan_content(blob, ()))

    def test_synthetic_realistic_credentials_are_detected_without_echo(self):
        secret = "gh" + "p_" + "x" * 36
        report = audit.inspect_file("docs/example.md", secret.encode(), ())
        self.assertIn("github-token", {item["category"] for item in report})
        self.assertNotIn(secret, str(report))

    def test_private_home_paths_are_detected(self):
        blob = ("C:" + "/" + "Users" + "/" + "PrivateLearner" + "/project").encode()
        self.assertIn("personal-home-path", audit.scan_content(blob, ()))

    def test_private_email_is_detected_public_noreply_is_allowed(self):
        private = ("personal" + "@" + "private.invalid").encode()
        self.assertIn("private-email", audit.scan_content(private, ()))
        self.assertEqual([], audit.scan_content(b"learner@users.noreply.github.com", ()))

    def test_host_identity_is_detected_and_redacted_in_filename(self):
        result = audit.inspect_file("docs/SYNTHETIC_PERSON.md", b"SYNTHETIC_PERSON", ("SYNTHETIC_PERSON",))
        self.assertTrue(result)
        self.assertNotIn("SYNTHETIC_PERSON", str(result))

    def test_png_metadata_and_trailing_payload_are_rejected(self):
        import struct
        import zlib
        def chunk(kind, data=b""):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
        header = b"\x89PNG\r\n\x1a\n"
        image_header = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        pixels = chunk(b"IDAT", zlib.compress(b"\0\0\0\0"))
        image = header + image_header + pixels + chunk(b"IEND")
        self.assertEqual([], audit.scan_png(image))
        self.assertIn("screenshot-metadata-not-allowed", audit.scan_png(header + image_header + chunk(b"tEXt", b"private")))
        self.assertIn("trailing-screenshot-data", audit.scan_png(image + b"private"))


class GitAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="codesprint-audit-")
        self.repo = Path(self.temporary.name)
        self.command("init", "-b", "main")
        self.command("config", "user.name", "public-learner")
        self.command("config", "user.email", "public-learner@users.noreply.github.com")

    def tearDown(self):
        self.temporary.cleanup()

    def command(self, *args, input_data=None):
        result = subprocess.run(["git", "-C", str(self.repo), *args], input=input_data, capture_output=True, check=True)
        return result.stdout.decode().strip()

    def stage(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.command("add", "--", name)

    def inspect(self, mode):
        with patch.object(audit, "sensitive_values", return_value=()):
            return audit.audit(self.repo, mode, "public-learner")

    def test_staged_audit_reads_index_and_rejects_unlisted_file(self):
        self.stage("README.md", "safe staged version")
        (self.repo / "README.md").write_text("not staged", encoding="utf-8")
        self.assertEqual("PASS", self.inspect("staged")["status"])
        self.stage("private.json", "{}")
        self.assertEqual("FAIL", self.inspect("staged")["status"])

    def test_history_scans_deleted_secret_in_old_commit(self):
        self.stage("README.md", "sk" + "-" + "x" * 40)
        self.command("commit", "-m", "Initial fixture")
        self.stage("README.md", "safe now")
        self.command("commit", "-m", "Remove fixture")
        result = self.inspect("history")
        self.assertEqual(2, result["commits_checked"])
        self.assertIn("model-api-key", {item["category"] for item in result["findings"]})

    def test_history_requires_public_noreply_identity(self):
        self.command("config", "user.email", "private" + "@" + "private.invalid")
        self.stage("README.md", "safe")
        self.command("commit", "-m", "Fixture")
        result = self.inspect("history")
        self.assertIn("non-public-git-identity", {item["category"] for item in result["findings"]})

    def test_index_symlink_is_rejected_without_system_symlink_permissions(self):
        blob_id = self.command("hash-object", "-w", "--stdin", input_data=b"README.md")
        self.command("update-index", "--add", "--cacheinfo", "120000," + blob_id + ",docs/link.md")
        result = self.inspect("staged")
        self.assertIn("symlink-or-submodule-not-allowed", {item["category"] for item in result["findings"]})

    def test_parent_git_repository_is_never_audit_target(self):
        child = self.repo / "new-project"
        child.mkdir()
        with self.assertRaisesRegex(RuntimeError, "independent-repository-required"):
            audit.audit(child, "staged", "public-learner")

    def test_nested_app_staged_audit_rejects_added_runtime_backup(self):
        self.stage("apps/shijie/run.py", "print('original example')")
        self.assertEqual("PASS", self.inspect("staged")["status"])
        self.stage("apps/shijie/data/backup.json", "{}")
        result = self.inspect("staged")
        self.assertIn("private-or-generated-directory", {item["category"] for item in result["findings"]})

    def test_working_tree_scans_nested_app_but_skips_known_local_build_outputs(self):
        source = self.repo / "apps/shijie/web/app.js"
        source.parent.mkdir(parents=True)
        source.write_text("safe original source", encoding="utf-8")
        generated = self.repo / "apps/shijie/android/private-signing/signer.txt"
        generated.parent.mkdir(parents=True)
        generated.write_text("sk" + "-" + "x" * 40, encoding="utf-8")
        result = self.inspect("working-tree")
        self.assertEqual("PASS", result["status"])
        self.assertEqual(1, result["files_checked"])
        source.write_text("sk" + "-" + "x" * 40, encoding="utf-8")
        result = self.inspect("working-tree")
        self.assertIn("model-api-key", {item["category"] for item in result["findings"]})

    def test_nested_app_deleted_private_content_is_still_found_in_history(self):
        self.stage("apps/shijie/web/app.js", "sk" + "-" + "x" * 40)
        self.command("commit", "-m", "Synthetic nested fixture")
        self.stage("apps/shijie/web/app.js", "safe now")
        self.command("commit", "-m", "Replace synthetic fixture")
        result = self.inspect("history")
        self.assertIn("model-api-key", {item["category"] for item in result["findings"]})


if __name__ == "__main__":
    unittest.main()
