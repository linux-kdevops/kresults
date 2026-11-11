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
NAME = "example\\x2dbuild@x.service"
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
    "gcc": "echo 'gcc (Debian 14.2.0-19) 14.2.0'\n",
    "clang": "echo 'Debian clang version 19.1.7 (3+b1)'\n",
    "checkpatch.pl": "echo Version: 0.32\n",
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


def printed(message: object) -> Json:
    """Return the journal's entry of a line the command printed."""
    stamp = str((STARTED + 1) * 10**6)
    line = {"_SYSTEMD_INVOCATION_ID": UNIT, "MESSAGE": message}
    line |= {"SYSLOG_IDENTIFIER": NAME}
    return {**line, "__REALTIME_TIMESTAMP": stamp}


# What systemd-cat --stderr-priority=warning files: a line of standard output,
# one of standard error, and one the command sent at a level of its own.
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
            "JOURNAL": str(self.root / "journal"),
            "INVOCATION_ID": UNIT,
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
            given = [*argv, *options]
            kept = [one for one in given if one.partition("=")[0] not in self.without]
            self.assertEqual(record.main(kept), 0)
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
        build = {"configuration": ["tinyconfig"], "arch": "x86", "compiler": "gcc"}
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

    def test_the_identifier_finds_a_line_journald_names_no_unit_on(self) -> None:
        lost = printed("ERROR: example")
        del lost["_SYSTEMD_INVOCATION_ID"]
        before = {"__REALTIME_TIMESTAMP": str(STARTED * 10**6 - 1)}
        earlier = lost | before | {"MESSAGE": "record: of the run before"}
        other = printed("of another run") | {"_SYSTEMD_INVOCATION_ID": 32 * "e"}
        self.make(earlier, lost, other)
        (entry,) = self.entries()
        self.assertEqual(entry["output"], "ERROR: example\n")

    def test_a_build_by_clang_for_the_arch_the_run_set(self) -> None:
        self.config.write_text(CLANG)
        self.make(ARCH="arm64", LLVM="1")
        entries = self.entries("linux/v1.2/tinyconfig/arm64/clang/make/all.json")
        self.assertEqual(len(entries), 1)
        tools = [tool("make", "4.4.1"), tool("clang", "19.1.7")]
        self.assertEqual(entries[0]["tools"], tools)

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
            len(self.entries(f"linux/{name}/tinyconfig/x86/gcc/make/all.json")), 1
        )

    def test_started_without_a_start_message(self) -> None:
        with mock.patch.object(sys.modules[__name__], "manager", printed):
            self.make()
        (entry,) = self.entries()
        self.assertEqual(entry["invocations"][0]["started"], "2026-01-02T03:04:06Z")

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

    def test_no_record_with_a_tool_of_no_package(self) -> None:
        (self.root / "bin" / "dpkg-query").write_text("#!/bin/sh\nexit 1\n")
        said = self.make()
        self.assertRegex(said, r"^no record: no package of debian holds .*/bin/make\n$")
        self.assertFalse(self.records.exists())

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
        self.without = ("--env",)
        set_ = {"LLVM": "1", "KCFLAGS": "-O1", "EXAMPLE": "y", "ARCH": ""}
        self.make(**set_)
        self.without = ()
        self.make("--env=EXAMPLE", "--env=LLVM", **set_)
        first, second = self.entries()
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

    def test_no_configuration_leaves_the_text_as_it_is(self) -> None:
        names = ("--output={configuration}.json", "--command=make {configuration}")
        self.file(options=(*names, "--tool=make"))
        (entry,) = self.read("linux/v1.2/{configuration}.json")["entries"]
        self.assertEqual(entry["command"], "make {configuration}")

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
