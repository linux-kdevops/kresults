# SPDX-License-Identifier: copyleft-next-0.3.1
"""Test lore: a maildir's series become records validate accepts."""

import contextlib
import io
import json
import mailbox
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "format"))

import lore
import validate

type Json = dict[str, Any]

VERSION = "v0.3-4-g0123456789ab"
QUERY = "dfn:drivers/nvme AND s:PATCH AND NOT s:Re"
MACHINE = {"arch": "x86_64", "cpu": "Example CPU", "cpus": 2, "memory": 2097152}
# When lorefetch wrote the mails of its first run, and of its second.
FIRST, SECOND = 1794938572, 1794942172
INVOCATION = {
    "producer": {"name": "example-ci", "version": "1.0"},
    "origin": "lab-a",
    "run": "run-1",
    "started": "2026-11-17T18:02:52Z",
    "seconds": 0,
    "machine": MACHINE,
}
ENTRY: Json = {
    "tools": [{"name": "lorefetch", "version": "", "build": VERSION}],
    "command": f"lorefetch --query '{QUERY}'",
    "env": {},
    "status": "pass",
    "exit": 0,
    "invocations": [INVOCATION],
}
COVER, SINGLE = "cover@example.org", "single@example.org"


@unittest.skipUnless(shutil.which("git"), "lore reads the lab's name with git")
class LoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.records = self.root / "records"
        self.maildir = self.root / "maildir"
        self.box = mailbox.Maildir(self.maildir, create=True)
        self.addCleanup(self.box.close)
        self.install = self.root / "lorefetch"
        self.install.mkdir()
        (self.install / "version").write_text(f"{VERSION}\n")
        gitconfig = self.root / "gitconfig"
        gitconfig.write_text("[kresults]\n\torigin = lab-a\n")
        environ = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        self.enterContext(mock.patch.dict(os.environ, environ, clear=True))
        self.enterContext(mock.patch.object(lore, "machine", return_value=MACHINE))

    def mail(
        self, name: str, subject: str, answers: str = "", fetched: int = FIRST
    ) -> None:
        """Add a mail to the maildir, as lorefetch wrote it at a time."""
        headers = f"Message-ID: <{name}>\nSubject: {subject}\n"
        if answers:
            headers += f"In-Reply-To: <{answers}>\nReferences: <{answers}>\n"
        self.put(f"{headers}From: A Developer <dev@example.org>\n\n{name}\n", fetched)

    def put(self, text: str, fetched: int = FIRST) -> None:
        file = self.maildir / "new" / self.box.add(text.encode())
        os.utime(file, (fetched, fetched))

    def series(self) -> None:
        """Add a cover letter with two patches and a reply, and a patch alone."""
        self.mail("p2@example.org", "[PATCH v3 2/2] nvme: second", COVER)
        self.mail("re@example.org", "Re: [PATCH v3 1/2] nvme: first", "p1@example.org")
        self.mail("p1@example.org", "[PATCH v3 1/2] nvme: first", COVER)
        self.mail(COVER, "[PATCH v3 0/2] nvme: a series")
        self.mail(SINGLE, "[RFC PATCH] nvme: alone")

    def file(self, *options: str) -> str:
        """Run lore on the maildir, and return what it printed."""
        argv = [f"--maildir={self.maildir}", f"--records={self.records}", *options]
        argv += ["--producer=example-ci", "--producer-version=1.0", "--run=run-1"]
        argv += [f"--query={QUERY}", f"--lorefetch={self.install}"]
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(lore.main(argv), 0)
        return stderr.getvalue()

    def read(self, name: str) -> Json:
        self.assertEqual(list(validate.check(self.records)), [])
        filed = self.records / "linux" / name / "b4" / "mbox.json"
        return json.loads(filed.read_text("utf-8"))

    def thread(self, name: str) -> list[str]:
        """Return the Message-IDs of the thread saved of a series, in its order."""
        saved = mailbox.mbox(self.records / "linux" / name / "b4" / "mbox.mbx")
        self.addCleanup(saved.close)
        return [str(message["Message-ID"]) for message in saved]

    def test_a_series_and_its_thread(self) -> None:
        self.series()
        said = self.file()
        written = [
            self.records / "linux" / name / "b4" / file
            for name in (COVER, SINGLE)
            for file in ("mbox.mbx", "mbox.json")
        ]
        self.assertEqual(said, "".join(f"record: {file}\n" for file in written))
        record = self.read(COVER)
        self.assertEqual(record["subject"], {"project": "linux", "message_id": COVER})
        self.assertEqual(record["entries"], [ENTRY])
        thread = [COVER, "p1@example.org", "p2@example.org", "re@example.org"]
        self.assertEqual(self.thread(COVER), [f"<{name}>" for name in thread])
        self.assertEqual(self.thread(SINGLE), [f"<{SINGLE}>"])
        mbox = (self.records / "linux" / COVER / "b4" / "mbox.mbx").read_bytes()
        self.assertTrue(mbox.startswith(lore.FROM + b"Message-ID: <cover@"))

    def test_what_is_no_series_leaves_no_record(self) -> None:
        self.series()
        self.mail("failed@example.org", 'FAILED: patch "[PATCH] x" failed to apply')
        self.mail("lost@example.org", "Re: [PATCH] nvme: lost", "gone@example.org")
        self.mail("stable@example.org", "[PATCH 6.12 000/670] 6.12.118-rc1 review")
        self.mail("s1@example.org", "[PATCH 6.12 001/670] x", "stable@example.org")
        self.mail("y@example.org", "[PATCH 6.6.y] nvme: a backport")
        self.mail("a/b@example.org", "[PATCH] nvme: a slash")
        self.mail("../../b@example.org", "[PATCH] nvme: a way out")
        self.mail("a\x1b[31m@example.org", "[PATCH] nvme: an escape")
        self.mail("b" * 256 + "@example.org", "[PATCH] nvme: a long name")
        self.put("Subject: [PATCH] nvme: no Message-ID\n\n")
        self.put("\xff\xfe no headers\n")
        said = self.file().splitlines()
        self.assertEqual(
            said[4:],
            [
                "no record: 2 of 9 threads start with no patch",
                "no record: 2 of 9 threads are for a stable tree",
                f"no record: 3 of 9 threads {lore.NO_NAME}",
                "no record: 3 of 16 mails name no Message-ID",
            ],
        )
        # The headers' parser may cut a Message-ID short: the name is checked too.
        escape = lore.Mail(Path(), "a\x1b[31m@example.org", "[PATCH] x", ())
        self.assertEqual(lore.left_out(escape), lore.NO_NAME)
        self.read(COVER)
        names = sorted(series.name for series in (self.records / "linux").iterdir())
        self.assertEqual(names, [COVER, SINGLE])
        # Nothing is written outside the records.
        kept = sorted(path.name for path in self.root.iterdir())
        self.assertEqual(kept, ["gitconfig", "lorefetch", "maildir", "records"])

    def test_the_same_maildir_twice_adds_nothing(self) -> None:
        self.series()
        self.file()
        before = self.read(COVER)
        said = self.file("--run=run-2")
        self.assertEqual(said, "no record: 2 of 2 threads are recorded already\n")
        self.assertEqual(self.read(COVER), before)

    def test_a_reply_that_arrives_makes_the_thread_fuller(self) -> None:
        self.series()
        self.file()
        self.mail(
            "late@example.org", "Re: [PATCH v3 0/2] nvme: a series", COVER, SECOND
        )
        said = self.file().splitlines()
        self.assertEqual(said[2], "no record: 1 of 2 threads are recorded already")
        self.assertEqual(len(self.thread(COVER)), 5)
        (entry,) = self.read(COVER)["entries"]
        started = [invocation["started"] for invocation in entry["invocations"]]
        self.assertEqual(started, ["2026-11-17T18:02:52Z", "2026-11-17T19:02:52Z"])

    def test_a_line_that_would_split_the_mbox_is_quoted(self) -> None:
        self.put(f"Message-ID: <{SINGLE}>\nSubject: [PATCH] x\n\nFrom here on\n")
        self.file()
        self.assertEqual(self.thread(SINGLE), [f"<{SINGLE}>"])
        self.assertEqual(
            self.file(), "no record: 1 of 1 threads are recorded already\n"
        )

    def test_mails_that_answer_each_other_end(self) -> None:
        self.mail("a@example.org", "[PATCH] a", "b@example.org")
        self.mail("b@example.org", "[PATCH] b", "a@example.org")
        self.assertIn("record: ", self.file())
        self.assertEqual(list(validate.check(self.records)), [])

    def test_where_the_source_of_lorefetch_can_be_read(self) -> None:
        source = "https://git.example.org/lorefetch/commit/?id=0123456789ab"
        (self.install / "url").write_text(f"{source}\n")
        self.series()
        self.file()
        (entry,) = self.read(SINGLE)["entries"]
        self.assertEqual(entry["tools"], [ENTRY["tools"][0] | {"url": source}])

    def test_a_maildir_without_the_version_of_lorefetch_leaves_no_record(self) -> None:
        self.series()
        version = self.install / "version"
        version.unlink()
        self.assertEqual(self.file(), f"no record: {version} does not exist\n")
        self.assertFalse(self.records.exists())

    def test_no_maildir(self) -> None:
        shutil.rmtree(self.maildir / "cur")
        why = f"{self.maildir} is no maildir: it has no cur"
        self.assertEqual(self.file(), f"no record: {why}\n")


if __name__ == "__main__":
    unittest.main()
