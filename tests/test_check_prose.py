# SPDX-License-Identifier: copyleft-next-0.3.1
"""Test scripts/check-prose.py through its command line."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import PIPE

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-prose.py"
# A hook's GIT_DIR aims git at the caller's repository, and the caller's
# configuration can sign or refuse a commit: the tests take neither.
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}
GIT = ("git", "-c", "user.name=A Tester", "-c", "user.email=tester@example.org")
Result = subprocess.CompletedProcess[str]


def prose(*args: str, cwd: Path | None = None, stdin: str | None = None) -> Result:
    """Run the script under test; return its status and its findings."""
    command = [sys.executable, str(SCRIPT), *args]
    return subprocess.run(
        command, cwd=cwd, env=ENV, input=stdin, check=False, stdout=PIPE, text=True
    )


class ProseTest(unittest.TestCase):
    """Each test gives the script a text and reads its verdict."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def message(self, body: str) -> Result:
        return prose("--message", "-", stdin=f"mm: free the page\n\n{body}\n")

    def file(self, name: str, text: str) -> Result:
        (self.tmp / name).write_text(text)
        return prose("--file", str(self.tmp / name))

    def assert_denied(self, result: Result, finding: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(finding, result.stdout)

    def assert_clean(self, result: Result) -> None:
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_ai_vocabulary(self) -> None:
        self.assert_denied(self.message("Free it to leverage the cache."), "AI vocab")

    def test_plays_a_role(self) -> None:
        self.assert_denied(self.message("The lock plays a role."), "plays a role")

    def test_filler_opener(self) -> None:
        self.assert_denied(self.message("Importantly, it is held."), "filler opener")

    def test_this_patch_opener(self) -> None:
        self.assert_denied(self.message("This patch adds a lock."), "'This patch'")

    def test_dash(self) -> None:
        self.assert_denied(self.message("The lock \u2014 a mutex."), "en or em dash")

    def test_headed_section(self) -> None:
        self.assert_denied(self.message("Root cause:\nNo lock."), "headed section")

    def test_list_in_the_body(self) -> None:
        result = self.message("- take the lock\n- free the page")
        self.assert_denied(result, "a list in the body")
        self.assert_clean(self.message("Take the lock\n- then free the page."))

    def test_history_in_a_comment(self) -> None:
        result = self.file("x.sh", "# Added for the second CPU.\ntrue\n")
        self.assert_denied(result, "history in a comment")

    def test_comment_restates_the_code(self) -> None:
        result = self.file("x.sh", "# Increment the counter.\ni=$((i + 1))\n")
        self.assert_denied(result, "restates the code")

    def test_warning_passes(self) -> None:
        result = self.message("Take the lock in order to free the page.")
        self.assertEqual(result.returncode, 0)
        self.assertIn("warning: commit message:3: in order to: Take", result.stdout)

    def test_message_from_a_file(self) -> None:
        (self.tmp / "MSG").write_text("mm: free it\n\nThis patch adds a lock.\n")
        result = prose("--message", str(self.tmp / "MSG"))
        self.assert_denied(result, "error: commit message:3: 'This patch' opener")

    def test_message_skips_trailers_comments_and_quotes(self) -> None:
        body = (
            "The log shows the leak:\n\n    we leverage the cache\n\n"
            "Link: https://example.org/leverage\nSigned-off-by: A <a@example.org>\n"
            "# This patch adds what git says here.\n"
            "# ------------------------ >8 ------------------------\n"
            "+This patch adds a line of the diff.\n"
        )
        self.assert_clean(self.message(body))

    def test_file_takes_several_paths(self) -> None:
        self.assert_clean(self.file("good.md", "A page.\n"))
        self.assert_denied(self.file("bad.md", "A vital page.\n"), "AI vocabulary")
        result = prose("--file", str(self.tmp / "good.md"), str(self.tmp / "bad.md"))
        self.assertEqual((result.returncode, result.stdout.count("\n")), (1, 1))

    def test_markup_skips_code_blocks(self) -> None:
        page = "A page.\n\n```sh\nleverage --all\n```\n\n~~~\nvital\n~~~\n"
        self.assert_clean(self.file("page.md", page))
        self.assert_denied(self.file("x.md", page + "A vital page.\n"), "x.md:10:")
        page = "Run::\n\n    leverage --all\n\n.. code-block:: sh\n\n    vital\n"
        self.assert_clean(self.file("page.rst", page))
        note = ".. note::\n\n    A vital page.\n"
        self.assert_denied(self.file("x.rst", page + note), "x.rst:10:")

    def test_python_comments_and_docstrings(self) -> None:
        source = '"""Hold a word."""\n\nWORD = "# we leverage a cache"\n'
        self.assert_clean(self.file("clean.py", source))
        result = self.file("bad.py", source + "\n\ndef f():\n    '''A vital one.'''\n")
        self.assert_denied(result, "bad.py:7: AI vocabulary: A vital one.")
        result = self.file("tail.py", source + "X = 1  # Fixed in the last release.\n")
        self.assert_denied(result, "tail.py:4: history in a comment")

    def test_shell_comments(self) -> None:
        script = "#!/bin/sh\n# Print the word.\necho leverage '# vital'\n"
        self.assert_clean(self.file("task", script))
        self.assert_clean(self.file("words", script.partition("\n")[2] + "# vital\n"))
        self.assert_denied(self.file("bad", script + "  # A vital one.\n"), "bad:4:")
        self.assert_denied(self.file("unit.service", "# A vital unit.\n"), "AI vocab")
        self.assert_denied(self.file("x.jsonc", "// A vital key.\n{}\n"), "AI vocab")

    def test_commit_checks_its_message_and_what_it_adds(self) -> None:
        def git(*args: str) -> None:
            subprocess.run([*GIT, *args], cwd=self.tmp, env=ENV, check=True)

        git("init", "--quiet")
        (self.tmp / "page.md").write_text("They foster nothing.\n")
        git("add", "page.md")
        git("commit", "--quiet", "--message", "docs: add a page")
        (self.tmp / "page.md").write_text("They foster nothing.\nA vital page.\n")
        git("commit", "--quiet", "--all", "--message", "docs: more\n\nThis patch adds.")
        result = prose("--commit", "HEAD", cwd=self.tmp)
        self.assert_denied(result, "error: page.md:2: AI vocabulary: A vital page.")
        self.assertRegex(result.stdout, r"error: \w{12} message:3: 'This patch' opener")
        self.assertNotIn("page.md:1:", result.stdout)
