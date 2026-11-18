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
SENT = "Tue, 17 Nov 2026 19:00:00 +0100"
POSTED: Json = {
    "schema": 1,
    "message_id": COVER,
    "subject": "[PATCH v3 0/2] nvme: a series",
    "author": "A Developer <dev@example.org>",
    "date": "2026-11-17T18:00:00Z",
    "change_id": "",
    "revision": 3,
    "text": COVER,
    "patches": 2,
}


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
        headers += "From: A Developer <dev@example.org>\n"
        self.put(f"{headers}Date: {SENT}\n\n{name}\n-- \n2.50\n", fetched)

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
        filed = self.records / "linux" / "series" / name / "b4" / "mbox.json"
        return json.loads(filed.read_text("utf-8"))

    def posted(self, name: str) -> Json:
        """Return the series.json of a series, what its first message says of it."""
        own = self.records / "linux" / "series" / name / "series.json"
        return json.loads(own.read_text("utf-8"))

    def test_a_series_and_what_it_was_posted_as(self) -> None:
        self.series()
        said = self.file()
        series = self.records / "linux" / "series"
        written = [
            f"{kind}: {series / name / file}\n"
            for name in (COVER, SINGLE)
            for kind, file in (("series", "series.json"), ("record", "b4/mbox.json"))
        ]
        self.assertEqual(said, "".join(written))
        record = self.read(COVER)
        self.assertEqual(record["subject"], {"project": "linux", "message_id": COVER})
        self.assertEqual(record["entries"], [ENTRY])
        self.assertEqual(self.posted(COVER), POSTED)
        # A patch alone is its series: it has no letter, and so no text.
        alone = {"message_id": SINGLE, "subject": "[RFC PATCH] nvme: alone"}
        alone |= {"revision": 1, "text": "", "patches": 1}
        self.assertEqual(self.posted(SINGLE), POSTED | alone)
        # No mail lies with the records.
        kept = {file.name for file in series.rglob("*") if file.is_file()}
        self.assertEqual(kept, {"series.json", "mbox.json"})

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
        filed = (self.records / "linux" / "series").iterdir()
        names = sorted(series.name for series in filed)
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

    def test_a_reply_that_arrives_adds_nothing(self) -> None:
        self.series()
        self.file()
        before = self.read(COVER)
        self.mail(
            "late@example.org", "Re: [PATCH v3 0/2] nvme: a series", COVER, SECOND
        )
        said = self.file()
        self.assertEqual(said, "no record: 2 of 2 threads are recorded already\n")
        self.assertEqual(self.read(COVER), before)

    def test_a_series_filed_without_its_series_json_gets_it(self) -> None:
        self.series()
        self.file()
        before = self.read(COVER)
        own = self.records / "linux" / "series" / COVER / "series.json"
        own.unlink()
        said = self.file("--run=run-2").splitlines()
        self.assertEqual(said[0], f"series: {own}")
        self.assertEqual(said[1], "no record: 1 of 2 threads are recorded already")
        self.assertEqual(self.posted(COVER), POSTED)
        # It was fetched once.
        self.assertEqual(self.read(COVER), before)

    def test_a_first_message_with_no_date_leaves_no_record(self) -> None:
        self.put(f"Message-ID: <{SINGLE}>\nSubject: [PATCH] x\nFrom: a@b.org\n\n")
        said = self.file()
        self.assertEqual(said, f"no record: 1 of 1 threads {lore.NO_SENDER}\n")
        self.assertFalse(self.records.exists())

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
