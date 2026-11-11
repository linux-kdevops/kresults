# SPDX-License-Identifier: copyleft-next-0.3.1
"""Test record: a run becomes a record validate accepts, or leaves none."""

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

import record
import validate

type Json = dict[str, Any]

UNIT = "0123456789abcdef0123456789abcdef"
STARTED = 1767323045
GCC = "#\n# Linux/x86 7.3.0 Kernel Configuration\n#\nCONFIG_CC_IS_GCC=y\n"
CLANG = GCC.replace("GCC", "CLANG")
BUILD = "linux/v1.2/tinyconfig/x86/gcc"
MAKE = f"{BUILD}/make/all.json"
CHECKPATCH = "linux/v1.2/checkpatch.pl.json"
# The programs record runs, but for git, as scripts at the front of PATH.
FAKES = {
    "journalctl": '[ "$1" = --sync ] && exit 1\nexec cat "$JOURNAL"\n',
    "make": "echo GNU Make 4.4.1\necho Built for x86_64-pc-linux-gnu\n",
    "checkpatch.pl": "echo Version: 0.32\n",
    "dpkg-query": (
        'case "$1" in\n'
        '--search) echo "example-tools:amd64: $2" ;;\n'
        "*) printf '%s' 'example-tools 1.0-2' ;;\n"
        "esac\n"
    ),
    "lscpu": "echo 'Architecture:  x86_64'\necho 'Model name:    Example CPU'\n",
}


def printed(message: object) -> Json:
    """Return the journal's entry of a line the command printed."""
    stamp = str((STARTED + 1) * 10**6)
    line = {"_SYSTEMD_INVOCATION_ID": UNIT, "MESSAGE": message}
    return {**line, "__REALTIME_TIMESTAMP": stamp}


# What systemd-cat --stderr-priority=warning files: a line of standard output,
# one of standard error, and one the command sent at a level of its own.
LEVELS = (
    printed("CC a.o") | {"PRIORITY": "6"},
    printed("a.c:1: warning") | {"PRIORITY": "4"},
    printed("b.c:2: error") | {"PRIORITY": "3"},
)


def manager(job: str) -> Json:
    """Return the journal's entry of the manager's message on a job of the unit."""
    stamp = str(STARTED * 10**6)
    line = {"USER_INVOCATION_ID": UNIT, "JOB_TYPE": job, "MESSAGE": "Started."}
    return {**line, "_SYSTEMD_INVOCATION_ID": 32 * "f", "__REALTIME_TIMESTAMP": stamp}


@unittest.skipUnless(shutil.which("git"), "record reads the tree with git")
class RecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.home = self.root / "alice"
        self.tree = self.home / "linux"
        self.records = self.root / "records"
        self.gitconfig = self.root / "gitconfig"
        fakes = self.root / "bin"
        for directory in (self.tree, fakes):
            directory.mkdir(parents=True)
        for name, script in FAKES.items():
            (fakes / name).write_text(f"#!/bin/sh\n{script}")
            (fakes / name).chmod(0o755)
        release = self.root / "os-release"
        release.write_text('NAME="Example"\nID=debian\n')
        self.enterContext(mock.patch.object(record, "OS_RELEASE", release))
        self.environ = {
            "PATH": f"{fakes}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.home),
            "GIT_CONFIG_GLOBAL": str(self.gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": str(self.root),
            "JOURNAL": str(self.root / "journal"),
            "INVOCATION_ID": UNIT,
            "KCFLAGS": "-Wextra",
            "CROSS_COMPILE": "",
        }
        self.enterContext(mock.patch.dict(os.environ, self.environ, clear=True))
        identity = "[user]\n\tname = Alice\n\temail = alice@example.org\n"
        self.gitconfig.write_text(f"{identity}[kresults]\n\torigin = lab-a\n")
        self.git("init", "--quiet")
        self.git("commit", "--quiet", "--allow-empty", "--message", "init: example")
        self.git("tag", "v1.2")
        self.commit = self.git("rev-parse", "HEAD")
        self.config = self.tree / "build" / ".config"
        self.config.parent.mkdir()
        self.config.write_text(GCC)

    def git(self, *command: str) -> str:
        done = subprocess.run(  # noqa: S603
            ["git", "-C", str(self.tree), *command],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        return done.stdout.strip()

    def file(
        self, *journal: Json, options: tuple[str, ...] = (), **environ: str
    ) -> str:
        """Run record after a unit that left a journal, and return what it printed."""
        entries = [manager("start"), *journal, manager("stop")]
        Path(self.environ["JOURNAL"]).write_text(
            "".join(json.dumps(entry) + "\n" for entry in entries)
        )
        argv = [f"--records={self.records}", "--producer=example-ci", "--run=run-1"]
        argv += ["--producer-version=1.0", "--env=KCFLAGS", "--env=CROSS_COMPILE"]
        status = {"EXIT_CODE": "exited", "EXIT_STATUS": "0", **environ}
        with (
            contextlib.chdir(self.tree),
            mock.patch.dict(os.environ, status),
            contextlib.redirect_stderr(io.StringIO()) as stdout,
        ):
            self.assertEqual(record.main([*argv, *options]), 0)
        return stdout.getvalue()

    def make(self, *journal: Json | str, **environ: str) -> str:
        """Run record after make in the build, and return what it printed.

        A string among the journal's entries is one more option to record.
        """
        build = ("--config=build/.config", "--configuration=tinyconfig")
        options = ("--output=make/all.json", "--command=make -s", "--tool=make")
        more = tuple(extra for extra in journal if isinstance(extra, str))
        entries = [entry for entry in journal if not isinstance(entry, str)]
        return self.file(*entries, options=(*build, *options, *more), **environ)

    def read(self, path: str) -> Json:
        return json.loads((self.records / path).read_text("utf-8"))

    def entries(self, path: str = MAKE) -> list[Json]:
        self.assertEqual(list(validate.check(self.records)), [])
        return self.read(path)["entries"]

    def assert_no_record(self, said: str, why: str) -> None:
        self.assertEqual(said, f"no record: {why}\n")
        self.assertFalse(self.records.exists())

    def test_a_pass_without_output(self) -> None:
        said = self.make()
        written = (MAKE, f"{BUILD}/config", "linux/v1.2/series.json")
        expected = "".join(f"record: {self.records / path}\n" for path in written)
        self.assertEqual(said, expected)
        build = {"configuration": ["tinyconfig"], "arch": "x86", "compiler": "gcc"}
        subject = {"project": "linux", "commit": self.commit, "build": build}
        self.assertEqual(self.read(MAKE)["subject"], subject)
        (entry,) = self.entries()
        (invocation,) = entry.pop("invocations")
        tool = {
            "name": "make",
            "version": "4.4.1",
            "build": "debian:example-tools 1.0-2",
        }
        ran = {
            "tools": [tool],
            "command": "make -s",
            "env": {"KCFLAGS": "-Wextra"},
            "config": "sha256:" + hashlib.sha256(GCC.encode()).hexdigest(),
            "status": "pass",
            "exit": 0,
        }
        self.assertEqual(entry, ran)
        machine = invocation.pop("machine")
        self.assertGreaterEqual(invocation.pop("seconds"), 0)
        who = {"producer": {"name": "example-ci", "version": "1.0"}, "origin": "lab-a"}
        when = {"run": "run-1", "started": "2026-01-02T03:04:05Z"}
        self.assertEqual(invocation, who | when)
        self.assertEqual(machine["cpu"], "Example CPU")
        self.assertEqual(machine["cpus"], len(os.sched_getaffinity(0)))
        self.assertEqual((self.records / BUILD / "config").read_text(), GCC)

    def test_series_of_a_commit_from_no_message(self) -> None:
        self.make()
        series = self.read("linux/v1.2/series.json")
        self.assertEqual(series["commit"], self.commit)
        self.assertEqual(series["base"], self.commit)
        self.assertEqual(series["base_name"], "v1.2")
        self.assertEqual(series["commits"], [])

    def test_a_fail_with_output(self) -> None:
        said = self.make(
            printed(f"{self.tree}/init/main.c:15:2: error: example"),
            printed(f"In file included from {self.home}/include/example.h:3:"),
            EXIT_STATUS="2",
        )
        self.assertIn(f"record: {self.records / MAKE}\n", said)
        (entry,) = self.entries()
        self.assertEqual((entry["status"], entry["exit"]), ("fail", 2))
        output = "init/main.c:15:2: error: example\n"
        output += "In file included from ~/include/example.h:3:\n"
        self.assertEqual(entry["output"], output)

    def test_an_equal_run_adds_an_invocation(self) -> None:
        first = [printed("a.c:1: warning: at 0x0123456789"), printed("b.c:2: warning")]
        second = [printed("b.c:2: warning"), printed("a.c:1: warning: at 0xfedcba9876")]
        self.make(*first, EXIT_STATUS="2")
        said = self.make(*second, EXIT_STATUS="2")
        self.assertNotIn("series.json", said)
        (entry,) = self.entries()
        self.assertEqual(len(entry["invocations"]), 2)
        self.assertEqual(
            entry["output"], "a.c:1: warning: at 0x0123456789\nb.c:2: warning\n"
        )

    def test_a_run_touches_the_top_of_the_tree(self) -> None:
        self.make()
        os.utime(self.records, ns=(0, 0))
        self.make()
        self.assertGreater(self.records.stat().st_mtime_ns, 0)

    def test_another_output_adds_an_entry(self) -> None:
        self.make(printed("a.c:1: warning"))
        self.make(printed("a.c:2: warning"))
        self.make()
        outputs = [entry.get("output") for entry in self.entries()]
        self.assertEqual(outputs, ["a.c:1: warning\n", "a.c:2: warning\n", None])

    def test_a_record_under_the_commit(self) -> None:
        command = f"scripts/checkpatch.pl --git {self.commit}..{self.commit}"
        names = ("--output=checkpatch.pl.json", "--tool=checkpatch.pl")
        said = self.file(options=(f"--command={command}", *names))
        self.assertIn(f"record: {self.records / CHECKPATCH}\n", said)
        (entry,) = self.entries(CHECKPATCH)
        self.assertNotIn("config", entry)
        self.assertEqual(entry["tools"][0]["version"], "0.32")
        subject = {"project": "linux", "commit": self.commit}
        self.assertEqual(self.read(CHECKPATCH)["subject"], subject)
        self.assertFalse((self.records / BUILD).exists())

    def test_a_line_that_is_not_utf8(self) -> None:
        self.make(printed(list(b"caf\xe9 au lait")))
        (entry,) = self.entries()
        self.assertEqual(entry["output"], "caf� au lait\n")

    def test_a_priority_keeps_the_lines_at_least_that_severe(self) -> None:
        self.make(*LEVELS, "--priority=warning")
        (entry,) = self.entries()
        self.assertEqual(entry["output"], "a.c:1: warning\nb.c:2: error\n")

    def test_all_lines_are_kept_without_a_priority(self) -> None:
        self.make(*LEVELS)
        (entry,) = self.entries()
        self.assertEqual(entry["output"], "CC a.o\na.c:1: warning\nb.c:2: error\n")

    def test_a_line_of_no_priority_is_at_info(self) -> None:
        self.make(printed("CC a.o"), LEVELS[2], "--priority=warning")
        (entry,) = self.entries()
        self.assertEqual(entry["output"], "b.c:2: error\n")

    def test_a_build_by_clang_for_the_arch_the_run_set(self) -> None:
        self.config.write_text(CLANG)
        self.make(ARCH="arm64", LLVM="1")
        entries = self.entries("linux/v1.2/tinyconfig/arm64/clang/make/all.json")
        self.assertEqual(len(entries), 1)

    def test_a_tree_with_changes_is_filed_apart(self) -> None:
        self.git("commit", "--quiet", "--allow-empty", "--message", "init: another")
        (self.tree / "Makefile").write_text("all:\n")
        self.git("add", "Makefile")
        self.make()
        name = f"v1.2-1-g{self.git('rev-parse', 'HEAD')[:12]}-dirty"
        self.assertEqual(
            len(self.entries(f"linux/{name}/tinyconfig/x86/gcc/make/all.json")), 1
        )

    def test_started_without_a_start_message(self) -> None:
        with mock.patch.object(sys.modules[__name__], "manager", printed):
            self.make()
        (entry,) = self.entries()
        self.assertEqual(entry["invocations"][0]["started"], "2026-01-02T03:04:06Z")

    def test_a_store_path_is_a_build(self) -> None:
        store = "/nix/store/" + 32 * "a" + "-example-1.0"
        self.assertEqual(record.build_of(f"{store}/bin/example"), store)

    def test_no_record_without_a_lab(self) -> None:
        self.gitconfig.write_text("")
        why = "git config --global kresults.origin names no lab"
        self.assert_no_record(self.make(), why)

    def test_no_record_outside_a_work_tree(self) -> None:
        shutil.rmtree(self.tree / ".git")
        self.assert_no_record(self.make(), "the working directory is no Git work tree")

    def test_no_record_without_the_config(self) -> None:
        self.config.unlink()
        self.assert_no_record(self.make(), "build/.config does not exist")

    def test_no_record_of_a_stopped_run(self) -> None:
        for code in ("killed", "dumped"):
            said = self.make(printed("Terminated"), EXIT_CODE=code, EXIT_STATUS="TERM")
            self.assert_no_record(said, "the command was stopped")

    def test_no_record_with_a_tool_of_no_package(self) -> None:
        (self.root / "bin" / "dpkg-query").write_text("#!/bin/sh\nexit 1\n")
        said = self.make()
        self.assertRegex(said, r"^no record: no package of debian holds .*/bin/make\n$")
        self.assertFalse(self.records.exists())


if __name__ == "__main__":
    unittest.main()
