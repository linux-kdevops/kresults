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

NAME = "example\\x2dbuild@x.service"
STARTED = 1767323045
GCC = (
    "#\n"
    "# Linux/x86 7.3.0 Kernel Configuration\n"
    "#\n"
    "CONFIG_CC_IS_GCC=y\n"
    "CONFIG_X86_64=y\n"
)
CLANG = GCC.replace("GCC", "CLANG")
BUILD = "linux/v1.2/tinyconfig/x86_64/gcc"
MAKE = f"{BUILD}/make/all.json"
CHECKPATCH = "linux/v1.2/checkpatch.pl.json"
# The programs record runs, but for git, as scripts at the front of PATH.
FAKES = {
    # The manager's answer for the unit, the last word asked of it.
    "systemctl": f"for unit; do :; done\n[ \"$unit\" = '{NAME}' ] || exit 1\n"
    f"echo @{STARTED}\n",
    "make": "echo GNU Make 4.4.1\necho Built for x86_64-pc-linux-gnu\n",
    "gcc": "echo 'gcc (Debian 14.2.0-19) 14.2.0'\n",
    "clang": "echo 'Debian clang version 19.1.7 (3+b1)'\n",
    "checkpatch.pl": "echo Version: 0.32\n",
    "check-uapi.sh": "exit 1\n",
    "kernel-doc": "exit 1\n",
    "b4": "echo 'This is b4 0.14.1'\n",
    "dpkg-query": (
        'case "$1" in\n'
        '--search) echo "example-tools:amd64: $2" ;;\n'
        "*) printf '%s' 'example-tools 1.0-2' ;;\n"
        "esac\n"
    ),
    "lscpu": "echo 'Architecture:  x86_64'\necho 'Model name:    Example CPU'\n",
}


def tool(name: str, version: str) -> Json:
    """Return a tool as an entry names it, of the package that holds the fakes."""
    return {"name": name, "version": version, "build": "debian:example-tools 1.0-2"}


def printed(line: str | bytes) -> bytes:
    """Return a line the command printed, as the unit's file holds it."""
    return (line.encode() if isinstance(line, str) else line) + b"\n"


# The files of a unit's cgroup, as the kernel writes them.
PRESSURE = "some avg10=0.00 avg60=0.00 avg300=0.00 total={}\nfull avg10=0.00 total=9\n"
CGROUP = {
    "cpu.stat": "usage_usec 157346\nuser_usec 136234\nsystem_usec 21111\n",
    "cpu.pressure": PRESSURE.format(7),
    "memory.peak": "3174400\n",
    "memory.swap.peak": "4096\n",
    "memory.pressure": PRESSURE.format(1500),
    "memory.events": "low 0\nhigh 2\nmax 1\noom 0\noom_kill 3\noom_group_kill 0\n",
    "memory.stat": "anon 4096\npgfault 90\npgmajfault 12\n",
    "io.pressure": PRESSURE.format(2500000),
    "io.stat": (
        "259:0 rbytes=1 wbytes=2 rios=3 wios=4 dbytes=0 dios=0\n"
        "8:16 rbytes=10 wbytes=20 rios=30 wios=40 dbytes=0 dios=0\n"
    ),
    "pids.peak": "17\n",
}
RESOURCES = {
    "cpu": {
        "seconds": 0.157,
        "user_seconds": 0.136,
        "system_seconds": 0.021,
        "stalled_seconds": 0.0,
    },
    "memory": {
        "peak": 3174400,
        "swap_peak": 4096,
        "stalled_seconds": 0.002,
        "high_events": 2,
        "max_events": 1,
        "oom_kills": 3,
        "major_faults": 12,
    },
    "io": {
        "stalled_seconds": 2.5,
        "read_bytes": 11,
        "write_bytes": 22,
        "read_operations": 33,
        "write_operations": 44,
    },
    "tasks": {"peak": 17},
}


@unittest.skipUnless(shutil.which("git"), "record reads the tree with git")
class RecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.home = self.root / "user"
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
        # The unit's cgroup, which no test has until it writes one.
        self.cgroups = self.root / "cgroup"
        own = self.root / "self-cgroup"
        own.write_text(f"0::/user.slice/{NAME}\n")
        self.enterContext(mock.patch.object(record, "SELF_CGROUP", own))
        self.enterContext(mock.patch.object(record, "CGROUPS", self.cgroups))
        self.environ = {
            "PATH": f"{fakes}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.home),
            "GIT_CONFIG_GLOBAL": str(self.gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": str(self.root),
            "KCFLAGS": "-Wextra",
            "CROSS_COMPILE": "",
        }
        self.enterContext(mock.patch.dict(os.environ, self.environ, clear=True))
        identity = "[user]\n\tname = User\n\temail = user@example.org\n"
        self.gitconfig.write_text(f"{identity}[kresults]\n\torigin = lab-a\n")
        self.git("init", "--quiet")
        self.git("commit", "--quiet", "--allow-empty", "--message", "init: example")
        self.git("tag", "v1.2")
        self.commit = self.git("rev-parse", "HEAD")
        self.config = self.tree / "build" / ".config"
        self.config.parent.mkdir()
        self.config.write_text(GCC)
        # The file the unit's standard error appends to, which no run that
        # printed nothing has.
        self.printed = self.root / "stderr"
        # The options a test has record run without: each is given otherwise.
        self.without: tuple[str, ...] = ()

    def git(self, *command: str, tree: Path | None = None) -> str:
        done = subprocess.run(  # noqa: S603
            ["git", "-C", str(tree or self.tree), *command],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        return done.stdout.strip()

    def file(self, *lines: bytes, options: tuple[str, ...] = (), **environ: str) -> str:
        """Run record after a unit that printed lines, and return what it printed."""
        self.printed.unlink(missing_ok=True)
        if lines:
            self.printed.write_bytes(b"".join(lines))
        argv = [f"--records={self.records}", "--producer=example-ci", "--run=run-1"]
        argv += [f"--output-file={self.printed}"]
        argv += ["--producer-version=1.0", "--env=KCFLAGS", "--env=CROSS_COMPILE"]
        status = {"EXIT_CODE": "exited", "EXIT_STATUS": "0", **environ}
        with (
            contextlib.chdir(self.tree),
            mock.patch.dict(os.environ, status),
            contextlib.redirect_stderr(io.StringIO()) as stdout,
        ):
            given = [*argv, *options]
            kept = [one for one in given if one.partition("=")[0] not in self.without]
            self.assertEqual(record.main(kept), 0)
        return stdout.getvalue()

    def make(self, *printed: bytes | str, **environ: str) -> str:
        """Run record after make in the build, and return what it printed.

        A string among the lines make printed is one more option to record.
        """
        build = ("--config=build/.config", "--configuration=tinyconfig")
        options = ("--output=make/all.json", "--command=make -s", "--tool=make")
        more = tuple(extra for extra in printed if isinstance(extra, str))
        lines = [line for line in printed if not isinstance(line, str)]
        return self.file(*lines, options=(*build, *options, *more), **environ)

    def checkpatch(self, range_: str) -> tuple[str, ...]:
        """Return the options of record after checkpatch.pl read a range."""
        command = "--command=scripts/checkpatch.pl --git {range}"
        return (command, f"--range={range_}", "--output=checkpatch.pl.json")

    def cgroup(self, *but: str) -> None:
        """Write the files of the unit's cgroup, but for some."""
        directory = self.cgroups / "user.slice" / NAME
        directory.mkdir(parents=True)
        for name, text in CGROUP.items():
            if name not in but:
                (directory / name).write_text(text)

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
        build = {"configuration": ["tinyconfig"], "arch": "x86_64", "compiler": "gcc"}
        subject = {"project": "linux", "commit": self.commit, "build": build}
        self.assertEqual(self.read(MAKE)["subject"], subject)
        (entry,) = self.entries()
        (invocation,) = entry.pop("invocations")
        ran = {
            "tools": [tool("make", "4.4.1"), tool("gcc", "14.2.0")],
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
        self.assertEqual(series["lists"], [])

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

    def test_a_range_names_the_commits_checked(self) -> None:
        self.git("commit", "--quiet", "--allow-empty", "--message", "init: another")
        base, tip = self.git("rev-parse", "HEAD~1"), self.git("rev-parse", "HEAD")
        (self.tree / "build" / ".range").write_text("HEAD~1..\n")
        options = (
            "--command=scripts/checkpatch.pl --strict --git {range}",
            "--range-file=build/.range",
            "--output=checkpatch.pl.json",
        )
        self.file(printed("ERROR: trailing whitespace"), options=options)
        name = f"v1.2-1-g{tip[:12]}"
        path = f"linux/{name}/checkpatch.pl.json"
        (entry,) = self.entries(path)
        command = f"scripts/checkpatch.pl --strict --git {base}..{tip}"
        self.assertEqual(entry["command"], command)
        self.assertEqual(entry["output"], "ERROR: trailing whitespace\n")
        subject = {"project": "linux", "commit": tip, "range": f"{base}..{tip}"}
        self.assertEqual(self.read(path)["subject"], subject)

    def test_one_commit_in_a_range_file_is_its_patch(self) -> None:
        self.git("commit", "--quiet", "--allow-empty", "--message", "init: another")
        base, tip = self.git("rev-parse", "HEAD~1"), self.git("rev-parse", "HEAD")
        (self.tree / "build" / ".range").write_text("HEAD\n")
        options = (
            "--command=scripts/checkpatch.pl --branch {range}",
            "--range-file=build/.range",
            "--output=checkpatch.pl.json",
            "--project=qemu",
        )
        self.file(options=options)
        path = f"qemu/v1.2-1-g{tip[:12]}/checkpatch.pl.json"
        self.entries(path)
        subject = {"project": "qemu", "commit": tip, "range": f"{base}..{tip}"}
        self.assertEqual(self.read(path)["subject"], subject)

    def test_a_range_is_filed_under_its_last_commit_not_the_dirty_tree(self) -> None:
        for message in ("init: another", "init: one more"):
            self.git("commit", "--quiet", "--allow-empty", "--message", message)
        (self.tree / "Makefile").write_text("all:\n")
        self.git("add", "Makefile")
        (self.tree / "build" / ".range").write_text("HEAD~2..HEAD~1\n")
        options = (
            "--command=scripts/checkpatch.pl --git {range}",
            "--range-file=build/.range",
            "--output=checkpatch.pl.json",
        )
        self.file(options=options)
        tip = self.git("rev-parse", "HEAD~1")
        path = f"linux/v1.2-1-g{tip[:12]}/checkpatch.pl.json"
        self.entries(path)
        subject = {"project": "linux", "commit": tip}
        subject |= {"range": f"{self.commit}..{tip}"}
        self.assertEqual(self.read(path)["subject"], subject)

    def test_a_range_of_one_commit_twice_leaves_no_record(self) -> None:
        (self.tree / "build" / ".range").write_text("HEAD..HEAD\n")
        options = (
            "--command=scripts/checkpatch.pl --git {range}",
            "--range-file=build/.range",
            "--output=checkpatch.pl.json",
        )
        said = self.file(options=options)
        self.assert_no_record(said, "HEAD..HEAD checks no commit")

    def test_a_range_given_as_an_option(self) -> None:
        self.git("commit", "--quiet", "--allow-empty", "--message", "init: another")
        base, tip = self.git("rev-parse", "HEAD~1"), self.git("rev-parse", "HEAD")
        self.file(options=self.checkpatch("HEAD~1.."))
        path = f"linux/v1.2-1-g{tip[:12]}/checkpatch.pl.json"
        (entry,) = self.entries(path)
        self.assertEqual(entry["command"], f"scripts/checkpatch.pl --git {base}..{tip}")
        self.assertEqual(self.read(path)["subject"]["range"], f"{base}..{tip}")
        with self.assertRaises(SystemExit):
            self.file(options=(*self.checkpatch("HEAD"), "--range-file=build/.range"))

    def test_a_range_that_starts_with_an_option_leaves_no_record(self) -> None:
        said = self.file(options=self.checkpatch("--all.."))
        self.assert_no_record(said, "--all.. names no commit")

    def test_another_range_to_the_same_tip_replaces_the_record(self) -> None:
        for message in ("init: another", "init: one more"):
            self.git("commit", "--quiet", "--allow-empty", "--message", message)
        middle, tip = self.git("rev-parse", "HEAD~1"), self.git("rev-parse", "HEAD")
        path = f"linux/v1.2-2-g{tip[:12]}/checkpatch.pl.json"
        for base in ("HEAD~2", "HEAD~1", "HEAD~1"):
            self.file(options=self.checkpatch(f"{base}.."))
        (entry,) = self.entries(path)
        checked = f"{middle}..{tip}"
        self.assertEqual(entry["command"], f"scripts/checkpatch.pl --git {checked}")
        self.assertEqual(len(entry["invocations"]), 2)
        self.assertEqual(self.read(path)["subject"]["range"], checked)

    def test_a_command_names_one_end_of_the_range_whole(self) -> None:
        self.git("commit", "--quiet", "--allow-empty", "--message", "init: another")
        base, tip = self.git("rev-parse", "HEAD~1"), self.git("rev-parse", "HEAD")
        (self.tree / "build" / ".range").write_text("HEAD~1..\n")
        options = (
            "--command=scripts/check-uapi.sh -b HEAD -p {base}",
            "--range-file=build/.range",
            "--output=check-uapi.sh.json",
        )
        self.file(options=options)
        path = f"linux/v1.2-1-g{tip[:12]}/check-uapi.sh.json"
        (entry,) = self.entries(path)
        self.assertEqual(entry["command"], f"scripts/check-uapi.sh -b HEAD -p {base}")
        subject = {"project": "linux", "commit": tip, "range": f"{base}..{tip}"}
        self.assertEqual(self.read(path)["subject"], subject)

    def test_a_series_is_filed_under_its_message_id(self) -> None:
        posted = "20260102-topic-v1-0-0123456789ab@example.org"
        (self.tree / "build" / ".message-id").write_text(f"{posted}\n")
        options = (
            f"--command=b4 --no-stdin mbox --no-cache --single-message {posted}",
            "--message-id-file=build/.message-id",
            "--output=b4/mbox.json",
        )
        self.file(printed(f"Looking up {posted} in lore"), options=options)
        series = f"linux/series/{posted}"
        # The message the series was posted as, which the format names beside
        # its record.
        (self.records / series / "b4" / "mbox.mbx").write_text("From: A Developer\n\n")
        path = f"{series}/b4/mbox.json"
        (entry,) = self.entries(path)
        self.assertEqual(
            entry["command"], f"b4 --no-stdin mbox --no-cache --single-message {posted}"
        )
        subject = {"project": "linux", "message_id": posted}
        self.assertEqual(self.read(path)["subject"], subject)
        self.assertFalse((self.records / series / "series.json").exists())

    def test_no_record_with_a_message_id_that_is_none(self) -> None:
        options = (
            "--command=b4 --no-stdin mbox",
            "--message-id-file=build/.message-id",
            "--output=b4/mbox.json",
        )
        for text, why in (
            ("\n", "build/.message-id names no Message-ID"),
            ("2026/0102@example.org\n", "2026/0102@example.org is no Message-ID"),
        ):
            (self.tree / "build" / ".message-id").write_text(text)
            self.assert_no_record(self.file(options=options), why)

    def test_a_series_apart_from_a_build_or_a_range_is_an_error(self) -> None:
        with self.assertRaises(SystemExit):
            self.make("--message-id-file=build/.message-id")
        options = (
            "--command=scripts/checkpatch.pl --git {range}",
            "--range-file=build/.range",
            "--message-id-file=build/.message-id",
            "--output=checkpatch.pl.json",
        )
        with self.assertRaises(SystemExit):
            self.file(options=options)
        self.assertFalse(self.records.exists())

    def test_a_file_names_what_the_command_worked_on(self) -> None:
        said = self.make(
            "--command=make C=2 CF=-Wsparse-error {file}",
            "--file=init/main.o",
            "--output=sparse/{file}.json",
        )
        path = f"{BUILD}/sparse/init/main.o.json"
        self.assertIn(f"record: {self.records / path}\n", said)
        (entry,) = self.entries(path)
        self.assertEqual(entry["command"], "make C=2 CF=-Wsparse-error init/main.o")
        build = {"configuration": ["tinyconfig"], "arch": "x86_64", "compiler": "gcc"}
        subject = {"project": "linux", "commit": self.commit, "build": build}
        subject |= {"file": "init/main.o"}
        self.assertEqual(self.read(path)["subject"], subject)

    def test_a_record_named_for_the_builds_compiler(self) -> None:
        for text, compiler in ((CLANG, "clang"), (GCC, "gcc")):
            self.config.write_text(text)
            self.make(
                "--command=make W=1e {file}",
                "--file=init/main.o",
                "--output={compiler}/{file}.json",
            )
            build = f"linux/v1.2/tinyconfig/x86_64/{compiler}"
            path = f"{build}/{compiler}/init/main.o.json"
            self.assertEqual(len(self.entries(path)), 1)

    def test_a_file_without_a_build(self) -> None:
        options = (
            "--command=scripts/kernel-doc --none --wall --werror {file}",
            "--file=init/main.c",
            "--output=kernel-doc/{file}.json",
        )
        self.file(options=options)
        path = "linux/v1.2/kernel-doc/init/main.c.json"
        (entry,) = self.entries(path)
        command = "scripts/kernel-doc --none --wall --werror init/main.c"
        self.assertEqual(entry["command"], command)
        subject = {"project": "linux", "commit": self.commit, "file": "init/main.c"}
        self.assertEqual(self.read(path)["subject"], subject)

    def test_a_line_that_is_not_utf8(self) -> None:
        self.make(printed(b"caf\xe9 au lait"))
        (entry,) = self.entries()
        self.assertEqual(entry["output"], "caf\ufffd au lait\n")

    def test_the_files_given_in_their_order(self) -> None:
        # A path that is not absolute is from the working directory, and a
        # last line without its newline is a line.
        (self.tree / "build" / "stdout").write_bytes(b"CC a.o\nLD vmlinux")
        self.make(
            printed("a.c:1: warning"),
            "--output-file=build/stdout",
            f"--output-file={self.printed}",
        )
        (entry,) = self.entries()
        output = "a.c:1: warning\nCC a.o\nLD vmlinux\na.c:1: warning\n"
        self.assertEqual(entry["output"], output)

    def test_a_file_that_is_missing_holds_no_line(self) -> None:
        self.make("--output-file=build/none")
        self.assertFalse(self.printed.exists())
        self.assertNotIn("output", self.entries()[0])
        self.make(printed("a.c:1: warning"), "--output-file=build/none")
        self.assertEqual(self.entries()[1]["output"], "a.c:1: warning\n")

    def test_an_empty_line_between_two_is_kept(self) -> None:
        self.make(printed("a.c:1: warning"), printed(""), printed("b.c:2: error"))
        (entry,) = self.entries()
        self.assertEqual(entry["output"], "a.c:1: warning\n\nb.c:2: error\n")

    def test_no_output_of_empty_lines_alone(self) -> None:
        self.make(printed(""), printed(""))
        self.assertNotIn("output", self.entries()[0])

    def test_no_output_without_a_file_named(self) -> None:
        self.without = ("--output-file",)
        self.make(printed("a.c:1: warning"))
        self.assertNotIn("output", self.entries()[0])

    def test_the_config_names_the_machine_not_the_run(self) -> None:
        self.config.write_text(CLANG)
        self.make(ARCH="arm64", LLVM="1")
        (entry,) = self.entries("linux/v1.2/tinyconfig/x86_64/clang/make/all.json")
        tools = [tool("make", "4.4.1"), tool("clang", "19.1.7")]
        self.assertEqual(entry["tools"], tools)
        self.assertEqual(
            entry["env"], {"ARCH": "arm64", "KCFLAGS": "-Wextra", "LLVM": "1"}
        )

    def test_a_config_of_the_other_word_size(self) -> None:
        self.config.write_text(GCC.replace("CONFIG_X86_64=y", "CONFIG_X86_32=y"))
        self.make()
        i386 = "linux/v1.2/tinyconfig/i386/gcc/make/all.json"
        self.assertEqual(len(self.entries(i386)), 1)

    def test_a_compiler_named_as_a_tool_is_there_once(self) -> None:
        self.make("--tool=gcc")
        (entry,) = self.entries()
        tools = [tool("make", "4.4.1"), tool("gcc", "14.2.0")]
        self.assertEqual(entry["tools"], tools)

    def test_no_compiler_among_the_tools_without_a_config(self) -> None:
        options = ("--output=checkpatch.pl.json", "--command=checkpatch.pl")
        self.file(options=(*options, "--tool=checkpatch.pl"))
        (entry,) = self.entries(CHECKPATCH)
        self.assertEqual(entry["tools"], [tool("checkpatch.pl", "0.32")])

    def test_a_tree_with_changes_is_filed_apart(self) -> None:
        self.git("commit", "--quiet", "--allow-empty", "--message", "init: another")
        (self.tree / "Makefile").write_text("all:\n")
        self.git("add", "Makefile")
        self.make()
        name = f"v1.2-1-g{self.git('rev-parse', 'HEAD')[:12]}-dirty"
        self.assertEqual(
            len(self.entries(f"linux/{name}/tinyconfig/x86_64/gcc/make/all.json")), 1
        )

    def test_started_is_now_when_the_manager_names_no_time(self) -> None:
        (self.root / "bin" / "systemctl").write_text("#!/bin/sh\nexit 1\n")
        with mock.patch.object(record.time, "time", return_value=STARTED + 60):
            self.make()
        (entry,) = self.entries()
        self.assertEqual(entry["invocations"][0]["started"], "2026-01-02T03:05:05Z")

    def test_what_the_kernel_counted_of_the_cgroup(self) -> None:
        self.cgroup()
        self.make()
        (invocation,) = self.entries()[0]["invocations"]
        self.assertEqual(invocation["resources"], RESOURCES)

    def test_a_cgroup_without_the_io_controller(self) -> None:
        self.cgroup("io.stat")
        self.make()
        (invocation,) = self.entries()[0]["invocations"]
        only = {"io": {"stalled_seconds": 2.5}}
        self.assertEqual(invocation["resources"], RESOURCES | only)

    def test_no_resources_without_a_cgroup(self) -> None:
        self.make()
        (invocation,) = self.entries()[0]["invocations"]
        self.assertNotIn("resources", invocation)

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

    def test_no_record_with_a_file_that_names_an_absolute_path(self) -> None:
        said = self.make("--file=/a/b.c")
        self.assert_no_record(said, "/a/b.c is no path from the top of the tree")

    def test_no_record_with_a_file_that_climbs_out_of_the_tree(self) -> None:
        for path in ("../a/b.c", "a/./b.c", "a/b/..", "a//b.c"):
            said = self.make(f"--file={path}")
            self.assert_no_record(said, f"{path} is no path from the top of the tree")

    def test_no_record_with_a_file_that_names_none(self) -> None:
        said = self.make("--file=")
        self.assert_no_record(said, "--file names no file")

    def test_no_record_with_a_file_that_names_a_directory(self) -> None:
        said = self.make("--file=drivers/nvme/", "--output=sparse/{file}.json")
        self.assert_no_record(said, "drivers/nvme/ names no file")

    def test_no_record_with_a_placeholder_left_unfilled(self) -> None:
        for option, name in (
            ("--output=sparse/{file}.json", "file"),
            ("--command=scripts/checkpatch.pl --git {range}", "range"),
            ("--command=git diff {base}", "base"),
            ("--command=make CC={compiler}", "compiler"),
        ):
            text = option.partition("=")[2]
            why = f"{text} holds {{{name}}}, which nothing given fills"
            self.assert_no_record(self.make(option), why)
        said = self.file(options=("--command=true", "--output={configuration}.json"))
        why = "{configuration}.json holds {configuration}, which nothing given fills"
        self.assert_no_record(said, why)

    def outside(self, version: str) -> Path:
        """Write a tool no package holds, which prints a version, and return it."""
        smatch = self.root / "bin" / "smatch"
        # Only the first line it prints is its version.
        smatch.write_text(f"#!/bin/sh\necho {version}\necho v9.9-1-g0123456789ab\n")
        smatch.chmod(0o755)
        packages = self.root / "bin" / "dpkg-query"
        held = packages.read_text().partition("\n")[2]
        packages.write_text(f'#!/bin/sh\n[ "$2" = {smatch} ] && exit 1\n{held}')
        return smatch

    def test_a_tool_of_no_package_is_the_build_it_describes(self) -> None:
        self.outside("v0.5.0-9187-g5189e3fb")
        self.make("--tool=smatch")
        smatch = {"name": "smatch", "version": "0.5.0"}
        smatch["build"] = "v0.5.0-9187-g5189e3fb"
        self.assertEqual(self.entries()[0]["tools"][1], smatch)

    def test_a_describe_name_is_the_whole_word(self) -> None:
        dirty = "v0.6.4-12-g0123456789ab-dirty"
        self.assertEqual(record.DESCRIBE.findall(f"sparse {dirty} (x)"), [dirty])
        for word in ("v1-2-g5189e3", "v1-2-g5189e3fbx", "v1-2-g5189e3fb-dirt"):
            self.assertIsNone(record.DESCRIBE.search(word), word)

    def test_no_record_with_a_tool_of_no_package_that_describes_none(self) -> None:
        smatch = self.outside("1.2.3")
        said = self.make("--tool=smatch")
        why = f"{smatch} is in no package of debian and prints no git describe"
        self.assert_no_record(said, why)

    def test_the_records_go_to_the_state_directory_by_default(self) -> None:
        self.without = ("--records",)
        for state, home in (("", self.home / ".local" / "state"), ("state", None)):
            given = str(self.root / state) if state else ""
            self.records = Path(home or given, "krecords")
            said = self.make(XDG_STATE_HOME=given)
            self.assertIn(f"record: {self.records / MAKE}\n", said)
            self.assertEqual(len(self.entries()), 1)

    def test_a_run_of_no_name_is_manual(self) -> None:
        self.without = ("--run",)
        self.make()
        self.assertEqual(self.entries()[0]["invocations"][0]["run"], "manual")

    def producer(self) -> Path:
        """Return a symlink into a checkout of the producer, apart from the tree."""
        checkout = self.root / "producer"
        (checkout / "steps").mkdir(parents=True)
        self.git("init", "--quiet", tree=checkout)
        message = ("--allow-empty", "--message", "init: producer")
        self.git("commit", "--quiet", *message, tree=checkout)
        link = self.home / "steps"
        link.symlink_to(checkout / "steps")
        return link

    def test_a_producer_tree_names_the_version_of_its_checkout(self) -> None:
        self.without = ("--producer-version",)
        link = self.producer()
        self.make(f"--producer-tree={link}")
        version = self.git("describe", "--always", "--dirty", tree=link.resolve())
        self.assertNotEqual(version, self.git("describe", "--always", "--dirty"))
        (invocation,) = self.entries()[0]["invocations"]
        self.assertEqual(invocation["producer"]["version"], version)

    def test_no_record_with_a_producer_tree_that_is_not_one(self) -> None:
        self.without = ("--producer-version",)
        missing, bare = self.root / "none", self.root / "bin"
        said = self.make(f"--producer-tree={missing}")
        self.assert_no_record(said, f"{missing} does not exist")
        said = self.make(f"--producer-tree={bare}")
        self.assert_no_record(said, f"{bare} is in no Git work tree")

    def test_a_configuration_file_names_the_configuration(self) -> None:
        self.without = ("--configuration",)
        (self.tree / "build" / "name").write_text(" tinyconfig \n# Not a name.\n")
        names = ("--output=make/{configuration}.json", "--command=make {configuration}")
        self.make("--configuration-file=build/name", *names)
        (entry,) = self.entries(f"{BUILD}/make/tinyconfig.json")
        self.assertEqual(entry["command"], "make tinyconfig")

    def test_a_configuration_of_profiles_is_a_directory_each_sorted(self) -> None:
        self.without = ("--configuration",)
        (self.tree / "build" / "name").write_text("b+a\n")
        names = ("--output=make/all.json", "--command=make {configuration}")
        self.make("--configuration-file=build/name", *names)
        build = "linux/v1.2/a/b/x86_64/gcc"
        (entry,) = self.entries(f"{build}/make/all.json")
        self.assertEqual(entry["command"], "make b+a")
        subject = self.read(f"{build}/make/all.json")["subject"]
        self.assertEqual(subject["build"]["configuration"], ["a", "b"])
        self.assertTrue((self.records / build / "config").is_file())

    def test_an_arguments_file_ends_the_command(self) -> None:
        fragments = "build/core/64bit.config\n\nbuild/my fragments/x.config\n"
        (self.tree / "build" / ".config.fragments").write_text(fragments)
        tools = self.tree / "scripts" / "kconfig"
        tools.mkdir(parents=True)
        (tools / "merge_config.sh").touch()
        options = (
            "--command=scripts/kconfig/merge_config.sh -n -O build",
            "--arguments-file=build/.config.fragments",
            "--output=merge_config.sh.json",
        )
        self.make(*options)
        (entry,) = self.entries(f"{BUILD}/merge_config.sh.json")
        command = (
            "scripts/kconfig/merge_config.sh -n -O build"
            " build/core/64bit.config 'build/my fragments/x.config'"
        )
        self.assertEqual(entry["command"], command)
        self.assertEqual(entry["tools"][0]["build"], "tree")
        (self.tree / "build" / ".config.fragments").write_text("")
        self.make("--arguments-file=build/.config.fragments")
        self.assertEqual([one["command"] for one in self.entries()], ["make -s"])

    def test_no_record_with_an_arguments_file_that_is_missing(self) -> None:
        said = self.make("--arguments-file=build/.config.fragments")
        self.assert_no_record(said, "build/.config.fragments does not exist")

    def test_a_command_names_its_record_and_its_first_tool(self) -> None:
        self.without = ("--output", "--tool")
        said = self.make("--command=make")
        self.assertIn(f"record: {self.records / MAKE}\n", said)
        (entry,) = self.entries()
        self.assertEqual(entry["command"], "make")
        tools = [tool("make", "4.4.1"), tool("gcc", "14.2.0")]
        self.assertEqual(entry["tools"], tools)

    def test_a_command_names_the_record_of_its_configuration(self) -> None:
        self.without = ("--configuration", "--output", "--tool")
        (self.tree / "build" / "name").write_text("tinyconfig\n")
        self.make("--configuration-file=build/name", "--command=make {configuration}")
        (entry,) = self.entries(f"{BUILD}/make/tinyconfig.json")
        self.assertEqual(entry["command"], "make tinyconfig")

    def test_no_record_of_a_command_the_list_names_no_record_for(self) -> None:
        self.without = ("--output",)
        why = "commands.json names {} for 'make {}'; pass --output"
        for target in ("foo", "tiny/config", "tinyconfig all"):
            said = self.make(f"--command=make {target}")
            self.assert_no_record(said, why.format("no record", target))
        twice = [{"argv": "make *", "record": "*.json"}] * 2
        listed: Json = {"projects": {"linux": {"env": {}, "commands": twice}}}
        with mock.patch.object(record, "known", return_value=listed):
            said = self.make("--command=make foo")
        self.assert_no_record(said, why.format("more than one record", "foo"))

    def test_an_output_given_is_the_record_of_any_command(self) -> None:
        self.make("--command=make foo", "--tool=make", "--tool=gcc")
        (entry,) = self.entries()
        self.assertEqual(entry["command"], "make foo")
        tools = [tool("make", "4.4.1"), tool("gcc", "14.2.0")]
        self.assertEqual(entry["tools"], tools)

    def test_a_project_whose_records_lie_under_the_commit(self) -> None:
        self.without = ("--env",)
        # The tree's own configure, which is on no PATH and is not run.
        (self.tree / "configure").write_text("#!/bin/sh\nexit 1\n")
        command = "--command=configure --target-list=x86_64-softmmu"
        said = self.file(options=("--project=qemu", command), CC="clang", LLVM="1")
        configure = "qemu/v1.2/configure.json"
        self.assertIn(f"record: {self.records / configure}\n", said)
        self.file(options=("--project=qemu", "--command=make"), LLVM="1")
        (entry,) = self.entries(configure)
        tools = [{"name": "configure", "version": "", "build": "tree"}]
        self.assertEqual(entry["tools"], tools)
        self.assertEqual(entry["env"], {"CC": "clang"})
        subject = {"project": "qemu", "commit": self.commit}
        self.assertEqual(self.read(configure)["subject"], subject)
        (entry,) = self.entries("qemu/v1.2/make/all.json")
        self.assertEqual(entry["env"], {})
        # The kernel's make reads a build, and its record under the commit
        # is refused.
        self.file(options=("--command=make",))
        problem = "lies under the commit, and commands.json has it under a build"
        problems = [("linux/v1.2/make/all.json", problem)]
        self.assertEqual(list(validate.check(self.records)), problems)

    def test_no_record_of_a_project_the_list_lacks(self) -> None:
        said = self.make("--project=xen")
        self.assert_no_record(said, "commands.json lists no project 'xen'")

    def test_a_tool_given_follows_the_first(self) -> None:
        self.without = ("--config", "--configuration")
        self.make("--tool=gcc", "--tool=make", "--tool=clang", "--tool=gcc")
        (entry,) = self.read("linux/v1.2/make/all.json")["entries"]
        tools = [tool("make", "4.4.1"), tool("gcc", "14.2.0"), tool("clang", "19.1.7")]
        self.assertEqual(entry["tools"], tools)

    def test_the_variables_of_the_first_tool_and_those_given(self) -> None:
        # A run of LLVM=1 builds with clang: the config names the compiler.
        self.config.write_text(CLANG)
        self.without = ("--env",)
        set_ = {"LLVM": "1", "KCFLAGS": "-O1", "EXAMPLE": "y", "ARCH": ""}
        self.make(**set_)
        self.without = ()
        self.make("--env=EXAMPLE", "--env=LLVM", **set_)
        first, second = self.entries("linux/v1.2/tinyconfig/x86_64/clang/make/all.json")
        listed = {"LLVM": "1", "KCFLAGS": "-O1"}
        self.assertEqual(list(first["env"].items()), list(listed.items()))
        self.assertEqual(second["env"], first["env"] | {"EXAMPLE": "y"})
        self.assertEqual(list(second["env"])[-1], "EXAMPLE")

    def test_no_variables_of_a_tool_the_list_has_none_for(self) -> None:
        options = ("--output=checkpatch.pl.json", "--command=scripts/checkpatch.pl")
        self.file(options=options, LLVM="1")
        (entry,) = self.entries(CHECKPATCH)
        self.assertEqual(entry["tools"], [tool("checkpatch.pl", "0.32")])
        self.assertEqual(entry["env"], {"KCFLAGS": "-Wextra"})

    def test_no_record_with_a_configuration_file_that_names_none(self) -> None:
        self.without = ("--configuration",)
        said = self.make("--configuration-file=build/name")
        self.assert_no_record(said, "build/name does not exist")
        (self.tree / "build" / "name").write_text("\n")
        said = self.make("--configuration-file=build/name")
        self.assert_no_record(said, "build/name names no configuration")

    def test_both_or_neither_of_two_options_is_an_error(self) -> None:
        both = ("--producer-tree=.", "--configuration-file=build/name")
        for option in both:
            with self.assertRaises(SystemExit):
                self.make(option)
        for option in ("--producer-version", "--configuration"):
            self.without = (option,)
            with self.assertRaises(SystemExit):
                self.make()
        self.assertFalse(self.records.exists())


if __name__ == "__main__":
    unittest.main()
