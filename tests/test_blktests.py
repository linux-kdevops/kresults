# SPDX-License-Identifier: copyleft-next-0.3.1
"""Test blktests: a run's results become records validate accepts."""

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

import blktests
import record
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
VERSION = "v2026.10.01-3-g0123456789ab"
# The facts of the machine under test, as the run copies them.
MACHINE = {
    "arch": "x86_64\n",
    "cpuinfo": "".join(
        f"processor\t: {cpu}\nmodel name\t: Example CPU\nflags\t\t: fpu\n\n"
        for cpu in range(2)
    ),
    "meminfo": "MemTotal:        2048 kB\nMemFree:         1024 kB\n",
    "version": f"{VERSION}\n",
}
PASSED = (
    "runtime\t1.682s\ndescription\tscan loop device partitions\n"
    "exit_status\t0\ndate\t2026-11-17 18:02:52\nstatus\tpass\n"
)
FAILED = PASSED.replace("status\tpass", "status\tfail\nreason\toutput")
NOT_RUN = "description\tpartscan\ndate\t2026-11-17 19:03:01\nstatus\tnot run\n"
# What check prints, not on a terminal, of tests that did not run.
STDOUT = (
    "loop/004 (partscan)\n"
    "loop/004 (partscan (twice))                [not run]\n"
    "    systemd version is older than 259\n"
    "    and no udev runs\n"
    "loop/005 (partscan) [passed]\n"
    "    runtime    ...  0.130s\n"
    "block/004 => nvme0n1 (discard) [not run]\n"
    "    the device does not discard\n"
    "block/*** => nvme0n1 [not run]\n"
    "    the group needs a module\n"
)
QEMU = "QEMU emulator version 11.0.93 (v11.1.0-rc2-19-gc62733676086)"
INVOCATION = {
    "producer": {"name": "example-ci", "version": "1.0"},
    "origin": "lab-a",
    "run": "run-1",
    "started": "2026-11-17T18:02:52Z",
    "seconds": 2,
    "machine": {"arch": "x86_64", "cpu": "Example CPU", "cpus": 2, "memory": 2097152},
}


@unittest.skipUnless(shutil.which("git"), "blktests reads the tree with git")
class BlktestsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.home = self.root / "user"
        self.tree = self.home / "linux"
        self.records = self.root / "records"
        self.results = self.root / "results" / "7.3.0"
        gitconfig = self.root / "gitconfig"
        environ = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": str(self.root),
        }
        self.enterContext(mock.patch.dict(os.environ, environ, clear=True))
        identity = "[user]\n\tname = User\n\temail = user@example.org\n"
        gitconfig.write_text(f"{identity}[kresults]\n\torigin = lab-a\n")
        self.config = self.tree / "build" / ".config"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(CONFIG)
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
        for name, text in MACHINE.items():
            self.result(f"machine/{name}", text)

    def result(self, path: str, text: str) -> None:
        """Write a file of the results directory."""
        file = self.results / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)

    def file(self, *options: str) -> str:
        """Run blktests on the results, and return what it printed."""
        argv = [f"--results={self.results}", f"--records={self.records}", *options]
        argv += ["--producer=example-ci", "--producer-version=1.0", "--run=run-1"]
        argv += ["--config=build/.config", "--configuration=block-layer"]
        with (
            contextlib.chdir(self.tree),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(blktests.main(argv), 0)
        return stderr.getvalue()

    def read(self, path: str) -> Json:
        self.assertEqual(list(validate.check(self.records)), [])
        return json.loads((self.records / BUILD / path).read_text("utf-8"))

    def test_a_pass(self) -> None:
        self.result("loop/nodev/loop/001", PASSED)
        self.result("loop/nodev/loop/001.full", "a verbose log\n")
        # What a test left behind is no result.
        self.result("loop/tmpdir.loop.001.qIt/img", "")
        said = self.file()
        written = (f"{BUILD}/blktests/nodev/loop/001.json", f"{BUILD}/config")
        written += ("linux/v1.2/series.json",)
        expected = "".join(f"record: {self.records / path}\n" for path in written)
        self.assertEqual(said, expected)
        record = self.read("blktests/nodev/loop/001.json")
        build = {"configuration": ["block-layer"], "arch": "x86_64", "compiler": "gcc"}
        self.assertEqual(record["subject"]["build"], build)
        self.assertEqual(record["subject"]["test"], "loop/001")
        self.assertEqual(len(record["subject"]["commit"]), 40)
        entry: Json = {
            "tools": [{"name": "blktests", "version": "", "build": VERSION}],
            "command": "check loop/001",
            "env": {},
            "config": "sha256:" + hashlib.sha256(CONFIG.encode()).hexdigest(),
            "status": "pass",
            "exit": 0,
            "invocations": [INVOCATION],
        }
        self.assertEqual(record["entries"], [entry])
        self.assertEqual((self.records / BUILD / "config").read_text(), CONFIG)

    def test_a_fail_with_its_output_and_kernel_log(self) -> None:
        self.result("loop/nodev/loop/002", FAILED)
        self.result("loop/nodev/loop/002.out.bad", f"cannot read {self.home}/img\n")
        self.result("loop/nodev/loop/002.dmesg", "[  12.5] WARNING: at block/x.c\n")
        self.result("loop/nodev/loop/002.full", "a verbose log\n")
        self.file()
        (entry,) = self.read("blktests/nodev/loop/002.json")["entries"]
        self.assertEqual((entry["status"], entry["exit"]), ("fail", 1))
        output = "cannot read ~/img\n[  12.5] WARNING: at block/x.c\n"
        self.assertEqual(entry["output"], output)

    def test_a_fail_that_left_no_file(self) -> None:
        failed = FAILED.replace("exit_status\t0", "exit_status\t3")
        self.result("loop/nodev/loop/003", failed.replace("\toutput", "\texit"))
        self.file()
        (entry,) = self.read("blktests/nodev/loop/003.json")["entries"]
        self.assertEqual((entry["status"], entry["exit"]), ("fail", 3))
        self.assertEqual(entry["output"], "exited with status 3\n")

    def test_a_test_that_did_not_run_leaves_no_record(self) -> None:
        self.result("loop/nodev/loop/001", PASSED)
        self.result("loop/nodev/loop/004", NOT_RUN)
        said = self.file().splitlines()
        self.result("loop.stdout", STDOUT.replace("loop/004", "loop/006"))
        why = "1 of 2 results did not run, and nothing kept their reason"
        self.assertEqual(said[1], f"no record: {why}")
        self.read("blktests/nodev/loop/001.json")
        tests = self.records / BUILD / "blktests" / "nodev" / "loop"
        self.assertEqual([test.name for test in tests.iterdir()], ["001.json"])

    def test_a_test_that_did_not_run_is_a_skip_with_its_reason(self) -> None:
        self.result("loop/nodev/loop/004", NOT_RUN)
        self.result("block/nvme0n1/block/004", NOT_RUN)
        self.result("block/nvme0n1/block/005", NOT_RUN)
        self.result("block.stdout", STDOUT.replace("discard\n", "discard at all\n"))
        self.result("loop.stdout", STDOUT)
        said = self.file().splitlines()
        why = "1 of 4 results did not run, and nothing kept their reason"
        self.assertEqual(said[3], f"no record: {why}")
        reasons = {
            "nodev/loop/004": "systemd version is older than 259\nand no udev runs\n",
            "nvme0n1/block/004": "the device does not discard\n",
        }
        for test, reason in reasons.items():
            (entry,) = self.read(f"blktests/{test}.json")["entries"]
            verdict: Json = {"status": "skip", "exit": 0, "output": reason}
            self.assertEqual({name: entry[name] for name in verdict}, verdict)
            self.assertEqual(entry["invocations"][0]["seconds"], 0)
        tests = self.records / BUILD / "blktests" / "nvme0n1" / "block"
        self.assertEqual([test.name for test in tests.iterdir()], ["004.json"])

    def test_a_test_under_a_condition_lies_under_its_directory(self) -> None:
        self.result("nvme/nodev_tr_loop_bd_device/nvme/012", NOT_RUN)
        self.result("nvme/nodev_tr_loop_bd_file/nvme/012", NOT_RUN)
        self.result("nvme/nvme0n1_tr_loop/nvme/035", NOT_RUN)
        self.result("nvme/nvme0n1_tr_loop/nvme/034", PASSED)
        self.result(
            "nvme.stdout",
            "nvme/012 (tr=loop bd=device) (run mkfs (xfs)) [not run]\n"
            "    no xfs\n"
            "nvme/012 (tr=loop bd=file) (run mkfs (xfs))   [not run]\n"
            "    no xfs on a file\n"
            "nvme/035 => nvme0n1 (tr=loop) (run mkfs) [not run]\n"
            "    no passthru\n",
        )
        said = self.file().splitlines()
        self.assertEqual(len(said), 6)
        devices: Json = {"TEST_DEVS": "/dev/nvme0n1"}
        skips: dict[str, tuple[str, Json]] = {
            "nodev_tr_loop_bd_device/nvme/012": ("no xfs\n", {}),
            "nodev_tr_loop_bd_file/nvme/012": ("no xfs on a file\n", {}),
            "nvme0n1_tr_loop/nvme/035": ("no passthru\n", devices),
        }
        for test, (reason, env) in skips.items():
            record = self.read(f"blktests/{test}.json")
            self.assertEqual(record["subject"]["test"], test.partition("/")[2])
            (entry,) = record["entries"]
            self.assertEqual((entry["status"], entry["output"]), ("skip", reason))
            self.assertEqual(entry["command"], f"check {record['subject']['test']}")
            self.assertEqual(entry["env"], env)
        (entry,) = self.read("blktests/nvme0n1_tr_loop/nvme/034.json")["entries"]
        self.assertEqual(entry["env"], devices)

    def test_a_group_that_did_not_run_is_a_skip_of_the_group(self) -> None:
        self.result("loop/nodev/loop/001", PASSED)
        self.result(
            "block.stdout", STDOUT + f"nbd/***{' ' * 54} [not run]\n    no nbd\n"
        )
        os.utime(self.results / "block.stdout", (0, 1794938572))
        self.file()
        groups = {
            "nvme0n1/block": "the group needs a module\n",
            "nodev/nbd": "no nbd\n",
        }
        for path, reason in groups.items():
            device, _, group = path.partition("/")
            record = self.read(f"blktests/{path}.json")
            self.assertEqual(record["subject"]["test"], group)
            (entry,) = record["entries"]
            verdict: Json = {"command": f"check {group}", "status": "skip", "exit": 0}
            verdict["env"] = (
                {} if device == "nodev" else {"TEST_DEVS": f"/dev/{device}"}
            )
            verdict["output"] = reason
            self.assertEqual({name: entry[name] for name in verdict}, verdict)
            when = {"started": "2026-11-17T18:02:52Z", "seconds": 0}
            self.assertEqual(entry["invocations"], [INVOCATION | when])
        said = self.file()
        self.assertEqual(said, "no record: 3 of 3 results are recorded already\n")

    def test_the_qemu_of_the_machine_is_the_second_tool(self) -> None:
        fakes = self.root / "bin"
        fakes.mkdir()
        # The QEMU is of no package, as one built from its tree.
        scripts = {"qemu-system-x86_64": f"echo '{QEMU}'", "dpkg-query": "exit 1"}
        for name, script in scripts.items():
            (fakes / name).write_text(f"#!/bin/sh\n{script}\n")
            (fakes / name).chmod(0o755)
        release = self.root / "os-release"
        release.write_text("ID=debian\n")
        self.enterContext(mock.patch.object(record, "OS_RELEASE", release))
        path = f"{fakes}{os.pathsep}{os.environ['PATH']}"
        self.enterContext(mock.patch.dict(os.environ, {"PATH": path}))
        self.result("loop/nodev/loop/001", PASSED)
        self.file(f"--qemu={fakes / 'qemu-system-x86_64'}")
        (entry,) = self.read("blktests/nodev/loop/001.json")["entries"]
        qemu: Json = {"name": "qemu", "version": "11.0.93"}
        qemu["build"] = "v11.1.0-rc2-19-gc62733676086"
        blktests_: Json = {"name": "blktests", "version": "", "build": VERSION}
        self.assertEqual(entry["tools"], [blktests_, qemu])
        missing = self.root / "no-qemu"
        said = self.file(f"--qemu={missing}")
        self.assertEqual(said, f"no record: {missing} is no program\n")

    def test_the_source_of_blktests_is_its_url(self) -> None:
        source = "https://example.org/blktests/commit/0123456789ab"
        blktests_: Json = {"name": "blktests", "version": "", "build": VERSION}
        self.result("loop/nodev/loop/001", PASSED)
        # An empty file names no source.
        self.result("machine/url", "\n")
        self.file()
        (entry,) = self.read("blktests/nodev/loop/001.json")["entries"]
        self.assertEqual(entry["tools"], [blktests_])
        # Tools that differ are another entry.
        self.result("machine/url", f"{source}\n")
        self.file()
        entries = self.read("blktests/nodev/loop/001.json")["entries"]
        tools = [entry["tools"] for entry in entries]
        self.assertEqual(tools, [[blktests_], [blktests_ | {"url": source}]])

    def test_a_test_on_a_device_asked_for_alone(self) -> None:
        self.result("block/003/nvme0n1/block/003", PASSED.replace("1.682s", "0.282s"))
        self.file()
        record = self.read("blktests/nvme0n1/block/003.json")
        self.assertEqual(record["subject"]["test"], "block/003")
        (entry,) = record["entries"]
        self.assertEqual(entry["command"], "check block/003")
        self.assertEqual(entry["env"], {"TEST_DEVS": "/dev/nvme0n1"})
        self.assertEqual(entry["invocations"][0]["seconds"], 0)

    def test_a_test_run_alone_and_in_its_group_is_two_runs(self) -> None:
        self.result("loop/nodev/loop/001", PASSED)
        self.result("loop/001/nodev/loop/001", PASSED.replace("18:02:52", "18:09:00"))
        self.file()
        (entry,) = self.read("blktests/nodev/loop/001.json")["entries"]
        started = [invocation["started"] for invocation in entry["invocations"]]
        self.assertEqual(started, ["2026-11-17T18:09:00Z", "2026-11-17T18:02:52Z"])

    def test_the_same_results_twice_add_nothing(self) -> None:
        self.result("loop/nodev/loop/001", PASSED)
        self.result("loop/nodev/loop/002", FAILED)
        self.file()
        before = self.read("blktests/nodev/loop/002.json")
        said = self.file()
        self.assertEqual(said, "no record: 2 of 2 results are recorded already\n")
        self.assertEqual(self.read("blktests/nodev/loop/002.json"), before)
        (entry,) = self.read("blktests/nodev/loop/001.json")["entries"]
        self.assertEqual(entry["invocations"], [INVOCATION])

    def test_results_without_the_version_of_blktests_leave_no_record(self) -> None:
        self.result("loop/nodev/loop/001", PASSED)
        version = self.results / "machine" / "version"
        version.unlink()
        why = f"{version} does not exist: the run copies it"
        self.assertEqual(self.file(), f"no record: {why}\n")
        self.assertFalse(self.records.exists())


if __name__ == "__main__":
    unittest.main()
