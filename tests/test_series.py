# SPDX-License-Identifier: copyleft-next-0.3.1
"""Test series: a range becomes a series.json validate accepts, or leaves none."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "format"))

import series
import validate

type Json = dict[str, Any]

URL = "https://git.example.org/linux.git"
FILES = {
    "init/main.c": "int main;\n",
    "include/example.h": "",
    "Documentation/core/index.rst": "Core\n",
    "Documentation/core/example.rst": "Example\n",
    "Documentation/loose/example.rst": "Example\n",
    "Documentation/top.rst": "Top\n",
}


class SeriesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.tree = self.root / "linux"
        self.records = self.root / "records"
        gitconfig = self.root / "gitconfig"
        gitconfig.write_text(
            "[user]\n\tname = User\n\temail = user@example.org\n"
            "[init]\n\tdefaultBranch = main\n"
        )
        environ = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": str(self.root),
        }
        self.enterContext(mock.patch.dict(os.environ, environ, clear=True))
        self.tree.mkdir()
        self.records.mkdir()
        self.git("init", "--quiet")
        self.base = self.commit("init: example", {"old.c": "", "kept.c": ""})
        self.git("tag", "v1.2")
        # A public tree and a local one, each with a branch that holds the base.
        for remote, url in (("public", URL), ("local", str(self.root / "bare"))):
            self.git("remote", "add", remote, url)
            self.git("update-ref", f"refs/remotes/{remote}/main", self.base)
        self.git("switch", "--quiet", "--create", "example-fix")
        self.git("rm", "--quiet", "old.c")
        self.first = self.commit("init: add files", FILES)
        links = "Link: https://example.org/r/1\nLink: https://example.org/r/2"
        message = f"init: change a file\n\n{links}"
        self.second = self.commit(message, {"kept.c": "int;\n"})

    def git(self, *command: str) -> str:
        done = subprocess.run(  # noqa: S603
            ["git", "-C", str(self.tree), *command],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        return done.stdout.strip()

    def commit(self, message: str, files: dict[str, str]) -> str:
        """Commit files with a message, and return the commit."""
        for name, text in files.items():
            (self.tree / name).parent.mkdir(parents=True, exist_ok=True)
            (self.tree / name).write_text(text)
        self.git("add", "--all")
        self.git("commit", "--quiet", "--message", message)
        return self.git("rev-parse", "HEAD")

    def write(self, *options: str, status: int = 0) -> str:
        """Run series in the tree, and return what it printed."""
        with (
            contextlib.chdir(self.tree),
            contextlib.redirect_stderr(io.StringIO()) as said,
        ):
            given = [f"--records={self.records}", *options]
            self.assertEqual(series.main(given), status)
        return said.getvalue()

    def read(self, name: str) -> Json:
        self.assertEqual(list(validate.check(self.records)), [])
        return json.loads((self.records / "linux" / name / "series.json").read_bytes())

    def test_a_range_of_two_commits(self) -> None:
        self.git("switch", "--quiet", "--detach")
        said = self.write(f"--range=v1.2..{self.second}")
        name = f"v1.2-2-g{self.second[:12]}"
        file = self.records / "linux" / name / "series.json"
        self.assertEqual(said, f"series: {file}\n")
        author = "User <user@example.org>"
        first = {"commit": self.first, "subject": "init: add files", "link": ""}
        second = {"commit": self.second, "subject": "init: change a file"}
        second["link"] = "https://example.org/r/2"
        wanted = {
            "schema": 1,
            "commit": self.second,
            "message_id": "",
            "ref": self.second,
            "trees": [],
            "base": self.base,
            "base_name": "v1.2",
            "base_trees": [{"remote": "public", "url": URL}],
            "commits": [first | {"author": author}, second | {"author": author}],
            "sources": ["include/example.h", "init/main.c", "kept.c"],
            "docs": ["core"],
            "added": sorted(FILES),
            "lists": [],
        }
        self.assertEqual(self.read(name), wanted)

    def test_a_range_without_a_tip_ends_at_head(self) -> None:
        # A change not committed is no part of a series, nor of its name.
        (self.tree / "kept.c").write_text("not committed\n")
        self.git("update-ref", "refs/remotes/public/next", self.second)
        # A series.json that is there is replaced.
        name = f"v1.2-2-g{self.second[:12]}"
        (self.records / "linux" / name).mkdir(parents=True)
        (self.records / "linux" / name / "series.json").write_text("{}\n")
        before = self.records.stat().st_mtime_ns
        os.utime(self.records, ns=(before - 10**9, before - 10**9))
        self.write(f"--range={self.base}..")
        written = self.read(name)
        self.assertEqual(written["commit"], self.second)
        self.assertEqual(written["ref"], "example-fix")
        self.assertEqual(written["trees"], [{"remote": "public", "url": URL}])
        self.assertGreaterEqual(self.records.stat().st_mtime_ns, before)

    def empty(self, message: str) -> str:
        """Commit a message with no change, dated, and return the commit."""
        dated = ("commit", "--quiet", "--date=2026-09-29T12:19:18+02:00")
        self.git(*dated, "--allow-empty", "--cleanup=verbatim", "-m", message)
        return self.git("rev-parse", "HEAD")

    def test_a_cover_letter_of_b4_is_no_commit(self) -> None:
        self.git("switch", "--quiet", "--create", "b4/topic", "v1.2")
        tracking = {"revision": 2, "change-id": "20260929-topic-073f2ed13bdc"}
        block = json.dumps({"series": tracking | {"prefixes": ["RFC"]}}, indent=2)
        text = "First <line>.\n\n# A note of b4's template.\nTo: l@example.org"
        self.empty(
            f"A topic\n\n{text}\n\n# More of the template.\n\n"
            f"{series.TRACKING}\n# This section is used by b4 prep.\n{block}\n"
        )
        patch = self.commit("init: change a file", {"kept.c": "int;\n"})
        self.write("--range=v1.2..")
        written = self.read(f"v1.2-2-g{patch[:12]}")
        wanted = {
            "subject": "A topic",
            "author": "User <user@example.org>",
            "date": "2026-09-29T10:19:18Z",
            "change_id": "20260929-topic-073f2ed13bdc",
            "revision": 2,
            "text": "First <line>.\n\nTo: l@example.org",
        }
        self.assertEqual(written["cover"], wanted)
        self.assertEqual([one["commit"] for one in written["commits"]], [patch])
        self.assertEqual((written["base"], written["commit"]), (self.base, patch))

    def test_a_cover_letter_alone_is_no_series(self) -> None:
        self.git("switch", "--quiet", "--create", "b4/topic", "v1.2")
        # A tracking block that cannot be read names no change-id.
        self.empty(f"A topic\n\nText.\n\n{series.TRACKING}\nnot JSON\n")
        self.assertIn("holds no commit", self.write("--range=v1.2..", status=1))
        patch = self.commit("init: change a file", {"kept.c": "int;\n"})
        self.write("--range=v1.2..")
        cover = self.read(f"v1.2-2-g{patch[:12]}")["cover"]
        self.assertEqual((cover["change_id"], cover["revision"]), ("", 1))
        self.assertEqual(cover["text"], "Text.")

    def test_only_a_first_commit_of_b4_is_a_cover_letter(self) -> None:
        self.git("switch", "--quiet", "--create", "topic", "v1.2")
        # An empty commit without b4's line, and one with it that changes a file.
        first = self.empty("An empty commit\n\n# Kept.\n")
        self.git("commit", "--quiet", "--allow-empty", "-m", "init: nothing")
        self.write("--range=v1.2..")
        written = self.read(f"v1.2-2-g{self.git('rev-parse', 'HEAD')[:12]}")
        self.assertNotIn("cover", written)
        self.assertEqual(written["commits"][0]["commit"], first)
        self.git("switch", "--quiet", "--create", "other", "v1.2")
        marked = f"init: change a file\n\n{series.TRACKING}\n{{}}\n"
        patch = self.commit(marked, {"kept.c": "int;\n"})
        self.write("--range=v1.2..")
        written = self.read(f"v1.2-1-g{patch[:12]}")
        self.assertNotIn("cover", written)
        self.assertEqual(written["commits"][0]["commit"], patch)

    def test_a_range_that_starts_with_an_option_is_refused(self) -> None:
        for given in ("--all..", "v1.2..--all", "--output=/dev/null..HEAD"):
            said = self.write(f"--range={given}", status=1)
            self.assertEqual(said, f"no series: {given} names no commit\n")
        self.assertEqual(list(self.records.iterdir()), [])

    def test_a_base_that_is_no_ancestor_is_refused(self) -> None:
        said = self.write(f"--range={self.second}..v1.2", status=1)
        self.assertEqual(said, f"no series: {self.second} is no ancestor of v1.2\n")
        self.assertIn("holds no commit", self.write("--range=HEAD..", status=1))
        self.assertIn("is no BASE..TIP", self.write("--range=HEAD", status=1))
        self.assertEqual(list(self.records.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
