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
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "format"))

import series
import validate

type Json = dict[str, Any]

URL = "https://git.example.org/linux.git"
POSTED = "20260825-topic-v1-0-afc38ac713ef@example.org"
# A cover letter as b4 sends one.
TEXT = "A topic.\n\n    $ make\n\nSigned-off-by: A Developer <dev@example.org>"
STAT = "A Developer (2):\n      init: add files\n\n init/main.c | 1 +"
TRAILERS = "base-commit: 7a34f7b8794b\nchange-id: 20260824-topic-3af8616d6e37"
BODY = f"{TEXT}\n---\n{STAT}\n---\n{TRAILERS}\n\nBest regards,\n-- \nA Developer\n"
# The subjects of the range's commits, and the header of a reply.
PATCHES = ("init: add files", "init: change a file")
REPLY = "In-Reply-To"
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
        self.sent = 0
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
        tracking = {"revision": 2, "change-id": "20260101-topic-0123456789ab"}
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
            "change_id": "20260101-topic-0123456789ab",
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

    def message(self, subject: str, body: str = BODY, **headers: str) -> EmailMessage:
        """Return a message, which is the root of its thread."""
        message = EmailMessage()
        message["From"] = "Dév Eloper <dev@example.org>"
        message["Subject"] = subject
        message["Date"] = "Tue, 25 Aug 2026 22:08:00 +0200"
        message["Message-ID"] = f"<{POSTED}>"
        for name, value in headers.items():
            del message[name]
            message[name] = value
        message.set_content(body)
        return message

    def reply(self, subject: str) -> EmailMessage:
        """Return a message that answers the root: a patch, or a review."""
        self.sent += 1
        headers = {"Message-ID": f"<{self.sent}@example.org>"}
        return self.message(subject, "A patch.\n", **headers, **{REPLY: f"<{POSTED}>"})

    def box(self, *thread: EmailMessage | bytes) -> str:
        """Write an mbox of a thread, and return the option that names it."""
        start = b"From mboxrd@z Thu Jan  1 00:00:00 1970\n"
        file = self.root / "cover.mbx"
        file.write_bytes(b"".join(start + bytes(message) for message in thread))
        return f"--mbox={file}"

    def mail(
        self,
        subject: str,
        body: str = BODY,
        *,
        patches: tuple[str, ...] = PATCHES,
        **headers: str,
    ) -> str:
        """Write the mbox of a series: its root, then the patches a letter counts."""
        root = self.message(subject, body, **headers)
        letter = series.LETTER.search(subject)
        prefixes = subject[: subject.find("]", letter.end()) + 1] if letter else ""
        counted = enumerate(patches, 1)
        replies = (f"{prefixes.replace('0/', f'{n}/')} {patch}" for n, patch in counted)
        return self.box(root, *map(self.reply, replies))

    def posted(self, *mail: str, **headers: str) -> Json:
        """Return the series.json of the range, posted as a mail."""
        self.write("--range=v1.2..", self.mail(*mail, **headers))
        return self.read(f"v1.2-2-g{self.second[:12]}")

    def test_a_cover_letter_of_an_mbox(self) -> None:
        written = self.posted("[PATCH 0/2] A [topic]")
        wanted = {
            "subject": "A [topic]",
            "author": "Dév Eloper <dev@example.org>",
            "date": "2026-08-25T20:08:00Z",
            "change_id": "20260824-topic-3af8616d6e37",
            "revision": 1,
            "text": f"{TEXT}\n---\n{STAT}",
        }
        self.assertEqual(written["message_id"], POSTED)
        self.assertEqual(written["cover"], wanted)
        self.assertEqual(len(written["commits"]), 2)

    def test_an_mbox_that_is_not_there_is_no_message(self) -> None:
        self.write("--range=v1.2..", f"--mbox={self.root / 'none.mbx'}")
        written = self.read(f"v1.2-2-g{self.second[:12]}")
        self.assertEqual(written["message_id"], "")
        self.assertNotIn("cover", written)

    def test_a_patch_of_an_mbox_is_no_cover_letter(self) -> None:
        first, second = (
            f"[PATCH v2 {n}/2] {patch}" for n, patch in enumerate(PATCHES, 1)
        )
        self.write("--range=v1.2..", self.box(self.message(first), self.reply(second)))
        written = self.read(f"v1.2-2-g{self.second[:12]}")
        self.assertEqual(written["message_id"], POSTED)
        self.assertNotIn("cover", written)

    def test_a_patch_alone_is_the_series(self) -> None:
        self.git("switch", "--quiet", "--create", "one", "v1.2")
        patch = self.commit("init: change a file", {"kept.c": "int;\n"})
        for subject in ("[PATCH v3] init: change a file", "init: change a file"):
            self.write("--range=v1.2..", self.mail(subject))
            written = self.read(f"v1.2-1-g{patch[:12]}")
            self.assertEqual(written["message_id"], POSTED)
            self.assertNotIn("cover", written)
        # A range may hold fewer commits than the series has patches.
        self.write("--range=v1.2..", self.mail("[PATCH 0/2] A topic"))
        written = self.read(f"v1.2-1-g{patch[:12]}")
        self.assertEqual(written["message_id"], POSTED)
        self.assertEqual(written["cover"]["subject"], "A topic")

    def test_a_reply_is_no_patch(self) -> None:
        root = self.message("[PATCH 0/2] A topic")
        first = self.reply("[PATCH 1/2] init: add files")
        review = self.reply("Re: [PATCH 1/2] init: add files")
        thanks = self.reply("Re: [PATCH 0/2] A topic")
        second = self.reply("[PATCH 2/2] init: change a file")
        self.write("--range=v1.2..", self.box(root, first, review, thanks, second))
        written = self.read(f"v1.2-2-g{self.second[:12]}")
        self.assertEqual(written["message_id"], POSTED)
        self.assertEqual(written["cover"]["subject"], "A topic")
        # A review of the second patch, whose own mail the thread lacks.
        review = self.reply("Re: [PATCH 2/2] init: change a file")
        said = self.write("--range=v1.2..", self.box(root, first, review))
        self.assertIn("is of another series", said)
        self.assertEqual(self.read(f"v1.2-2-g{self.second[:12]}")["message_id"], "")

    def test_an_mbox_of_another_series_is_not_used(self) -> None:
        name = f"v1.2-2-g{self.second[:12]}"
        file = self.records / "linux" / name / "series.json"
        stale = f"no mbox: {self.root / 'cover.mbx'} is of another series, <{POSTED}>\n"
        mails = (
            self.mail("[PATCH 0/2] A topic", patches=("init: other", PATCHES[1])),
            self.mail("[PATCH 0/2] A topic", patches=()),
            self.mail("[PATCH] init: add files"),
        )
        for mail in mails:
            said = self.write("--range=v1.2..", mail)
            self.assertEqual(said, f"{stale}series: {file}\n")
            written = self.read(name)
            self.assertEqual(written["message_id"], "")
            self.assertNotIn("cover", written)

    def test_a_patch_of_another_revision_is_no_patch(self) -> None:
        root = self.message("[PATCH v2 0/2] A topic")
        old = [
            self.reply(f"[PATCH {n}/2] {patch}") for n, patch in enumerate(PATCHES, 1)
        ]
        new = [
            self.reply(f"[PATCH v2 {n}/2] {patch}")
            for n, patch in enumerate(PATCHES, 1)
        ]
        renamed = self.reply("[PATCH v2 2/2] init: rename a file")
        said = self.write("--range=v1.2..", self.box(root, *old, new[0], renamed))
        self.assertIn("is of another series", said)
        self.assertEqual(self.read(f"v1.2-2-g{self.second[:12]}")["message_id"], "")
        renamed = self.reply("[PATCH 2/2] init: rename a file")
        self.write("--range=v1.2..", self.box(root, old[0], renamed, *new))
        written = self.read(f"v1.2-2-g{self.second[:12]}")
        self.assertEqual(written["message_id"], POSTED)
        self.assertEqual(written["cover"]["revision"], 2)

    def test_a_folded_subject_is_one_line(self) -> None:
        root = self.message("[PATCH 0/2] A topic")
        first = self.reply("[PATCH 1/2] init: add files")
        second = bytes(self.reply("[PATCH 2/2] init: change a file"))
        folded = second.replace(b"init: change a", b"init:\n\t change   a")
        self.assertNotEqual(folded, second)
        self.write("--range=v1.2..", self.box(root, first, folded))
        written = self.read(f"v1.2-2-g{self.second[:12]}")
        self.assertEqual(written["message_id"], POSTED)

    def test_the_root_of_a_thread_need_not_come_first(self) -> None:
        root = self.message("[PATCH 0/2] A topic")
        patches = [
            self.reply(f"[PATCH {n}/2] {patch}") for n, patch in enumerate(PATCHES, 1)
        ]
        self.write("--range=v1.2..", self.box(*patches, root))
        written = self.read(f"v1.2-2-g{self.second[:12]}")
        self.assertEqual(written["message_id"], POSTED)
        self.assertEqual(written["cover"]["subject"], "A topic")
        # A thread saved without its root starts at its first message.
        self.write("--range=v1.2..", self.box(*patches))
        self.assertEqual(
            self.read(f"v1.2-2-g{self.second[:12]}")["message_id"], "1@example.org"
        )

    def test_a_prefix_names_the_revision(self) -> None:
        for prefix, revision in (("PATCH v3 0/2", 3), ("PATCH RFC v12 00/15", 12)):
            cover = self.posted(f"[{prefix}] A topic")["cover"]
            self.assertEqual(
                (cover["subject"], cover["revision"]), ("A topic", revision)
            )
        cover = self.posted("[RFC] [PATCH 0/2] A topic v2")["cover"]
        self.assertEqual((cover["subject"], cover["revision"]), ("A topic v2", 1))

    def test_a_date_is_set_in_utc(self) -> None:
        dates = {
            "Mon, 31 Aug 2026 23:30:00 -0700": "2026-09-01T06:30:00Z",
            "Tue, 01 Sep 2026 00:15:00 +0530": "2026-08-31T18:45:00Z",
            "Tue, 01 Sep 2026 06:30:00 -0000": "2026-09-01T06:30:00Z",
        }
        for sent, wanted in dates.items():
            cover = self.posted("[PATCH 0/2] A topic", Date=sent)["cover"]
            self.assertEqual(cover["date"], wanted)

    def test_a_body_without_trailers_ends_at_its_signature(self) -> None:
        body = f"\nA topic.\n---\nv2: more\n\n{STAT}\n\n-- \n2.51.0\n"
        cover = self.posted("[PATCH 0/2] A topic", body)["cover"]
        self.assertEqual(cover["text"], f"A topic.\n---\nv2: more\n\n{STAT}")
        self.assertEqual(cover["change_id"], "")
        cover = self.posted("[PATCH 0/2] A topic", "A topic.\n\n--\nmore\n")["cover"]
        self.assertEqual(cover["text"], "A topic.\n\n--\nmore")

    def test_a_cover_letter_of_b4_comes_before_an_mbox(self) -> None:
        self.git("switch", "--quiet", "--create", "b4/topic", "v1.2")
        self.empty(f"The tree's own\n\nText.\n\n{series.TRACKING}\n{{}}\n")
        patch = self.commit("init: change a file", {"kept.c": "int;\n"})
        self.write(
            "--range=v1.2..", self.mail("[PATCH 0/1] A topic", patches=PATCHES[1:])
        )
        written = self.read(f"v1.2-2-g{patch[:12]}")
        self.assertEqual(written["message_id"], POSTED)
        self.assertEqual(written["cover"]["subject"], "The tree's own")

    def test_an_mbox_that_names_no_series_is_refused(self) -> None:
        refused = {"Message-ID": "<a/b@example.org>"}
        named = self.mail("[PATCH 0/2] A topic", patches=PATCHES, **refused)
        said = self.write("--range=v1.2..", named, status=1)
        self.assertEqual(said, "no series: <a/b@example.org> is no Message-ID\n")
        for date in ("", "soon"):
            named = self.mail("[PATCH 0/2] A topic", Date=date)
            said = self.write("--range=v1.2..", named, status=1)
            self.assertEqual(said, "no series: its cover letter has no date\n")
        (self.root / "cover.mbx").write_bytes(b"")
        said = self.write("--range=v1.2..", named, status=1)
        self.assertEqual(
            said, f"no series: {self.root / 'cover.mbx'} holds no message\n"
        )
        said = self.write("--range=v1.2..", f"--mbox={self.root}", status=1)
        self.assertIn(f"no series: {self.root} cannot be read: ", said)
        self.assertEqual(list(self.records.iterdir()), [])

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
