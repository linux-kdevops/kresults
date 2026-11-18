# SPDX-License-Identifier: copyleft-next-0.3.1
"""Test sample: a run made on a machine under test becomes a record."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "format"))

import sample
import validate

type Json = dict[str, Any]

CONFIG = (
    "#\n"
    "# Linux/x86 7.3.0 Kernel Configuration\n"
    "#\n"
    "CONFIG_CC_IS_GCC=y\n"
    "CONFIG_X86_64=y\n"
)
BUILD = "linux/v1.2/block-layer/x86_64/gcc"
RECORD = "fio/nvme0n1/atomic-merge.json"
VERSION = "fio-3.41-12-g0123456789ab"
JOB = "[merge]\nrw=write\nbs=4k\n"
# The facts of the machine under test, as the run copies them.
MACHINE = {
    "arch": "x86_64\n",
    "cpuinfo": "".join(
        f"processor\t: {cpu}\nmodel name\t: Example CPU\n\n" for cpu in range(2)
    ),
    "meminfo": "MemTotal:        2048 kB\n",
}
METRICS = "iops\t12000.5\t1/s\thigher\nwrite_merges\t3\t\thigher\n"
INVOCATION: Json = {
    "producer": {"name": "example-ci", "version": "1.0"},
    "origin": "lab-a",
    "run": "run-1",
    "started": "2026-11-18T12:00:00Z",
    "seconds": 60,
    "metrics": [
        {"name": "iops", "value": 12000.5, "unit": "1/s", "better": "higher"},
        {"name": "write_merges", "value": 3, "unit": "", "better": "higher"},
    ],
    "machine": {"arch": "x86_64", "cpu": "Example CPU", "cpus": 2, "memory": 2097152},
}


@unittest.skipUnless(shutil.which("git"), "sample reads the tree with git")
class SampleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.home = self.root / "user"
        self.tree = self.home / "linux"
        self.records = self.root / "records"
        self.facts = self.root / "machine"
        gitconfig = self.root / "gitconfig"
        environ = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": str(self.root),
            "TEST_DEVS": "/dev/nvme0n1",
        }
        self.enterContext(mock.patch.dict(os.environ, environ, clear=True))
        identity = "[user]\n\tname = User\n\temail = user@example.org\n"
        gitconfig.write_text(f"{identity}[kresults]\n\torigin = lab-a\n")
        files = {"linux/build/.config": CONFIG, "linux/atomic-merge.fio": JOB}
        files |= {f"../machine/{name}": text for name, text in MACHINE.items()}
        files |= {"../machine/version": f"{VERSION}\n", "metrics": METRICS}
        for path, text in files.items():
            file = self.home / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text)
        for command in (
            ("init", "--quiet"),
            ("commit", "--quiet", "--allow-empty", "--message", "init: example"),
            ("tag", "v1.2"),
        ):
            subprocess.run(  # noqa: S603
                ["git", "-C", str(self.tree), *command],  # noqa: S607
                check=True,
                capture_output=True,
            )

    def file(self, *options: str, started: str = INVOCATION["started"]) -> str:
        """File a run of fio, and return what sample printed."""
        argv = [f"--records={self.records}", "--command=fio atomic-merge.fio"]
        argv += [f"--output={RECORD}", "--exit=0", f"--started={started}"]
        argv += ["--seconds=60", f"--machine={self.facts}", f"--install={self.facts}"]
        argv += ["--producer=example-ci", "--producer-version=1.0", "--run=run-1"]
        argv += ["--config=build/.config", "--configuration=block-layer", *options]
        with (
            contextlib.chdir(self.tree),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(sample.main(argv), 0)
        return stderr.getvalue()

    def read(self) -> Json:
        self.assertEqual(list(validate.check(self.records)), [])
        return json.loads((self.records / BUILD / RECORD).read_text("utf-8"))

    def metrics(self, text: str) -> str:
        """Return why a metrics file leaves no record, less the file's name."""
        (self.home / "metrics").write_text(text)
        said = self.file(f"--metrics={self.home / 'metrics'}")
        self.assertFalse(self.records.exists())
        return said.removeprefix(f"no record: {self.home / 'metrics'}:")

    def test_a_run_with_its_numbers(self) -> None:
        options = (f"--metrics={self.home / 'metrics'}", "--job=atomic-merge.fio")
        said = self.file(*options, "--env=TEST_DEVS")
        written = (f"{BUILD}/{RECORD}", f"{BUILD}/config", "linux/v1.2/series.json")
        expected = "".join(f"record: {self.records / path}\n" for path in written)
        self.assertEqual(said, expected)
        (entry,) = self.read()["entries"]
        expected_entry: Json = {
            "tools": [{"name": "fio", "version": "", "build": VERSION}],
            "command": "fio atomic-merge.fio",
            "env": {"TEST_DEVS": "/dev/nvme0n1"},
            "config": "sha256:" + hashlib.sha256(CONFIG.encode()).hexdigest(),
            "job": "sha256:" + hashlib.sha256(JOB.encode()).hexdigest(),
            "status": "pass",
            "exit": 0,
            "invocations": [INVOCATION],
        }
        self.assertEqual(entry, expected_entry)

    def test_each_run_is_one_more_sample(self) -> None:
        options = (f"--metrics={self.home / 'metrics'}", "--job=atomic-merge.fio")
        self.file(*options)
        (self.home / "metrics").write_text("iops\t11000\t1/s\thigher\n")
        self.file(*options, started="2026-11-18T12:05:00Z")
        (entry,) = self.read()["entries"]
        values = [run["metrics"][0]["value"] for run in entry["invocations"]]
        self.assertEqual(values, [12000.5, 11000])
        # The same sample filed again adds nothing.
        said = self.file(*options, started="2026-11-18T12:05:00Z")
        held = f"{self.records / BUILD / RECORD} holds the run already"
        self.assertEqual(said, f"no record: {held}\n")
        self.assertEqual(len(self.read()["entries"][0]["invocations"]), 2)

    def test_another_job_is_another_entry(self) -> None:
        self.file("--job=atomic-merge.fio")
        (self.tree / "atomic-merge.fio").write_text(JOB + "bs=8k\n")
        self.file("--job=atomic-merge.fio", started="2026-11-18T12:05:00Z")
        jobs = {entry["job"] for entry in self.read()["entries"]}
        self.assertEqual(len(jobs), 2)

    def test_a_run_that_failed_keeps_its_errors(self) -> None:
        errors = self.home / "fio.stderr"
        errors.write_text(f"fio: {self.home}/img: No such file\n")
        self.file("--exit=1", f"--output-file={errors}")
        (entry,) = self.read()["entries"]
        self.assertEqual((entry["status"], entry["exit"]), ("fail", 1))
        self.assertEqual(entry["output"], "fio: ~/img: No such file\n")
        self.assertNotIn("metrics", entry["invocations"][0])

    def test_a_metrics_file_that_cannot_be_read_leaves_no_record(self) -> None:
        for text, why in (
            ("iops\t1\t1/s\n", "1 is not four fields between tabs\n"),
            ("\t1\t1/s\thigher\n", "1 names no metric\n"),
            ("iops\t1\t\tlower\niops\t2\t\tlower\n", "2 names iops again\n"),
            ("iops\tfast\t1/s\thigher\n", "1: 'fast' is no number\n"),
            ("iops\tnan\t1/s\thigher\n", "1: 'nan' is no number\n"),
            ("iops\t1e999\t1/s\thigher\n", "1: 1e999 is no number a record holds\n"),
            ("iops\t1\t1/s\tmore\n", "1: 'more' is neither higher nor lower\n"),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.metrics(text), why)

    def test_what_is_not_given_right_leaves_no_record(self) -> None:
        said = self.file(started="yesterday")
        self.assertIn("--started yesterday is no time in UTC", said)
        (self.facts / "meminfo").unlink()
        why = f"{self.facts / 'meminfo'} does not exist"
        self.assertEqual(self.file(), f"no record: {why}\n")
        self.assertEqual(
            self.file("--job=none.fio"), "no record: none.fio does not exist\n"
        )
        self.assertFalse(self.records.exists())


if __name__ == "__main__":
    unittest.main()
