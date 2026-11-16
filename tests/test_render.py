# SPDX-License-Identifier: copyleft-next-0.3.1
"""Tests of render: matching, comparison, loading, escaping and writing."""

import dataclasses
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from itertools import takewhile
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "format"))

import render
import test_validate
import validate
from render import Change

FIXTURES = Path(__file__).resolve().parent
LINK = "https://patch.msgid.link/20260929-nvme-mam-v1-1-48dcbe79cece@samsung.com"
SHA = "0123456789abcdef" * 2 + "01234567"
CHECK = f'<span class="pass" title="{render.CLEAN}">\u2713</span>'
SERIES = render.Series("b" * 40, "v1", (render.Commit("c" * 40, "s", "A B <a@x>"),), ())
# A build as the format nests it: its configuration, ARCH and compiler; and
# the build's record of make, under it.
B = "b/x86_64/clang"
MAKE_ALL = ("make", "all.json")
TIP = "c" * 40
RANGE = f"{'b' * 40}..{TIP}"
CLANG = {"name": "clang", "version": "21.1.8", "build": "/nix/store/x-clang-21.1.8"}
KBUILD = {"ARCH": "x86_64", "LLVM": "1"}
MACHINE = {"arch": "x86_64", "cpu": "AMD Ryzen 9", "cpus": 32, "memory": 2**37}
PRODUCER = {"name": "kdevops-ng", "version": "30335b9"}
STARTED = "2026-11-10T09:12:03Z"
# The fields record() puts in the invocation, and in the subject.
INVOKED = frozenset(
    {"producer", "origin", "run", "started", "seconds", "machine", "resources"}
)
SUBJECT = frozenset({"file", "test", "range", "commit", "build"})
# B as a subject or an objects.json names it.
BUILT = {"configuration": ["b"], "arch": "x86_64", "compiler": "clang"}
# Where the site links a revision of the producer in PRODUCER.
COMMITS = "https://github.com/linux-kdevops/kdevops-ng/commit/"


def kdevops_ng(version: str) -> dict[str, str]:
    return {"name": "kdevops-ng", "version": version}


def invocation(**extra: object) -> dict[str, object]:
    return {
        "producer": PRODUCER,
        "origin": "lab-a",
        "run": "Gn7rQ4G1Nsn",
        "started": STARTED,
        "seconds": 1,
        "machine": MACHINE,
        **extra,
    }


def entry(
    status: str = "pass", output: object = "", **extra: object
) -> dict[str, object]:
    """An entry of make -s; extra sets its fields, or its invocation's."""
    found: dict[str, object] = {
        "tools": [CLANG],
        "command": "make -s",
        "env": KBUILD,
        "status": status,
        "exit": 0 if status in {"pass", "skip"} else 1,
    }
    if output != "":
        found["output"] = output
    found.update((k, v) for k, v in extra.items() if k not in INVOKED)
    invoked = {k: v for k, v in extra.items() if k in INVOKED}
    return {**found, "invocations": [invocation(**invoked)]}


def record(
    status: str = "pass", output: object = "", **extra: object
) -> dict[str, object]:
    """A record of one entry.

    extra sets its schema, its subject's fields, or its entry's as entry()
    does.
    """
    subject: dict[str, object] = {"project": "linux", "commit": TIP}
    subject.update((k, v) for k, v in extra.items() if k in SUBJECT)
    rest = {k: v for k, v in extra.items() if k not in SUBJECT | {"schema"}}
    return {
        "schema": extra.get("schema", 1),
        "subject": subject,
        "entries": [entry(status, output, **rest)],
    }


def parsed(status: str = "pass", output: str = "") -> render.Record:
    return render.parse_record(record(status, output))


def tree(records: dict[str, render.Entry], commit: str = "c") -> render.Tree:
    return render.Tree("p", commit, "b", records, None, None, None)


def commit(subject: str = "s", author: str = "A <a@x>", **extra: str) -> dict[str, str]:
    return {"commit": TIP, "subject": subject, "author": author, "link": "", **extra}


def series_json(*commits: Mapping[str, str], **extra: object) -> dict[str, object]:
    """A series.json of a series of commits on v1; extra sets its fields."""
    return {
        "schema": 1,
        "commit": TIP,
        "message_id": "",
        "ref": "",
        "trees": [],
        "base": "b" * 40,
        "base_name": "v1",
        "base_trees": [],
        "commits": list(commits),
        "sources": [],
        "docs": [],
        "added": [],
        "lists": [],
        **extra,
    }


COVER = re.compile(
    r'<a class="row-cover" href="[^"]*" tabindex="-1" aria-hidden="true"></a>'
)


def uncovered(html: str) -> str:
    """A page without its row covers, which every cell of a linked row has."""
    return COVER.sub("", html)


def put(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data)
    path.write_text(text, encoding="utf-8")


class Positions(unittest.TestCase):
    def test_dropped(self) -> None:
        for line, key in (
            ("fs/a.c:12: warning: x", "fs/a.c: warning: x"),
            ("fs/a.c:12:5: warning: x", "fs/a.c: warning: x"),
            ("./fs/a.c:12:3-5: WARNING x", "./fs/a.c: WARNING x"),
            (
                "[../scripts/Makefile.build:291: a.o] Error 1",
                "[../scripts/Makefile.build: a.o] Error 1",
            ),
            ("  162 | int f(void)", "| int f(void)"),
        ):
            with self.subTest(line=line):
                self.assertEqual(render.unplaced(line), key)

    def test_addresses_and_store_hashes(self) -> None:
        base = "0xffffffff8171a6f0 mas_wr_store_entry [vmlinux]:\t3760"
        series = "0xffffffff8171a830 mas_wr_store_entry [vmlinux]:\t3760"
        self.assertEqual(render.unplaced(base), render.unplaced(series))
        fragment = "-kfragments/kernel/configs/storage/block-test-devices.config:"
        old = f"by fragment /nix/store/8bixzqcfb7xjrh5crs6r5dnnnsn8npzl{fragment}"
        new = f"by fragment /nix/store/2hb0wngbqg7mzjhmpllsf2snxd5dsnz0{fragment}"
        self.assertEqual(render.unplaced(old), render.unplaced(new))
        self.assertIn("/nix/store/-kfragments/kernel", render.unplaced(old))
        self.assertEqual(
            render.diff_html(render.diff(f"{base}\n{old}\n", f"{series}\n{new}\n")), ""
        )
        self.assertEqual(render.unplaced("mask 0x1234"), "mask 0x1234")

    def test_test_runs(self) -> None:
        base = "[ 6405.509403] XFS (dm-0): log I/O error -5"
        series = "[   12.000001] XFS (dm-0): log I/O error -5"
        self.assertEqual(render.unplaced(base), render.unplaced(series))
        then = "--- tests/generic/794.out\t2026-11-08 14:48:56.194558869 +0000"
        now = "--- tests/generic/794.out\t2026-11-09 05:37:00.864037270 +0000"
        self.assertEqual(render.unplaced(now), "--- tests/generic/794.out")
        self.assertEqual(render.unplaced(then), render.unplaced(now))

    def test_kept(self) -> None:
        for line in ("9d7e163330d8:7: WARNING:X", "      |     ^", "Makefile:12: x"):
            with self.subTest(line=line):
                self.assertEqual(render.unplaced(line), line)


class Added(unittest.TestCase):
    def test_counted(self) -> None:
        self.assertEqual(render.added("A\nA\n", "A\n"), {1})

    def test_positions_aside(self) -> None:
        self.assertEqual(render.added("a.c:5:1: w\n", "a.c:9:3: w\n"), set())

    def test_gutter_aside(self) -> None:
        then = "a.c:162:5: warning: X\n  162 | int f(void)\n      |     ^\n"
        now = "a.c:163:5: warning: X\n  163 | int f(void)\n      |     ^\n"
        self.assertEqual(render.added(now, then), set())

    def test_line_in_prose_aside(self) -> None:
        then = "a.c:4059:3: note: Control jumps to line 4067\n"
        now = "a.c:4082:3: note: Control jumps to line 4090\n"
        self.assertEqual(render.added(now, then), set())

    def test_marks_the_new_occurrence(self) -> None:
        then = "In function f:\nwarning A\n"
        now = then + "In function g:\nwarning A\n"
        self.assertEqual(render.added(now, then), {2, 3})
        self.assertEqual(
            render.diff_html(render.diff(then, now)),
            '<pre><span class="same">In function f:</span>\n'
            '<span class="same">warning A</span>\n'
            '<span class="added">In function g:</span>\n'
            '<span class="added">warning A</span></pre>',
        )

    def test_control_pictures(self) -> None:
        output = "a\rb\x0cc\x7fd\te\x00f\x1b[K\n"
        html = render.output_html(parsed("fail", output), "k.txt")
        self.assertIn("a\u240db\u240cc\u2421d\te\u2400f\u241b[K", html)
        self.assertFalse(set(html) & {chr(c) for c in render.PICTURES})
        self.assertEqual(render.lines(output), [output.removesuffix("\n")])
        self.assertEqual(render.added("a\rb\n", "a\rb\n"), set())
        self.assertEqual(render.added("a\rb\n", "a\u240db\n"), {0})

    def test_split_at_newline_only(self) -> None:
        self.assertEqual(render.lines("50%\r100%\x0cx\n"), ["50%\r100%\x0cx"])
        self.assertEqual(render.lines(""), [])
        self.assertEqual(render.added("a\n", "a"), set())


class Colours(unittest.TestCase):
    def test_b4_marks(self) -> None:
        for sgr, mark, kind in (
            ("32", "\u2713", "pass"),
            ("31", "\u2717", "fail"),
            ("33", "?", "warn"),
            ("90", "\u25cf", "none"),
            ("32", "\u25cf", "pass"),
            ("31", "\u25cf", "fail"),
            ("31;5", "\u25cf", "fail"),
        ):
            with self.subTest(sgr=sgr, mark=mark):
                line = f"  \x1b[{sgr}m{mark}\x1b[0m Signed: DKIM/kernel.org"
                self.assertEqual(
                    render.pictured(line),
                    f'  <span class="{kind}">{mark}</span> Signed: DKIM/kernel.org',
                )

    def test_reset(self) -> None:
        for reset in ("\x1b[0m", "\x1b[m", "\x1b[31;0m", "\x1b[;m"):
            with self.subTest(reset=reset):
                self.assertEqual(
                    render.pictured(f"\x1b[31ma{reset}b"),
                    '<span class="fail">a</span>b',
                )
        self.assertEqual(render.pictured("\x1b[;32ma"), '<span class="pass">a</span>')

    def test_other_codes(self) -> None:
        for line, html in (
            ("\x1b[1ma\x1b[22m", "a"),
            ("\x1b[01;33ma", '<span class="warn">a</span>'),
            ("\x1b[31m\x1b[5ma", '<span class="fail">a</span>'),
            ("\x1b[31ma\x1b[34mb\x1b[39mc", '<span class="fail">a</span>bc'),
            ("\x1b[32ma\x1b[38;5;31mb", '<span class="pass">a</span>b'),
            ("\x1b[48;5;31ma", "a"),
            ("\x1b[32m\x1b[48;5;31ma", '<span class="pass">a</span>'),
        ):
            with self.subTest(line=line):
                self.assertEqual(render.pictured(line), html)

    def test_not_sgr(self) -> None:
        for line, html in (
            ("\x1b[Ka", "\u241b[Ka"),
            ("\x1b]0;t\x07a", "\u241b]0;t\u2407a"),
            ("\x1b[31", "\u241b[31"),
            ("\x1b[3\r1ma", "\u241b[3\u240d1ma"),
            ("\x1b\x1b[31ma", '\u241b<span class="fail">a</span>'),
        ):
            with self.subTest(line=line):
                self.assertEqual(render.pictured(line), html)

    def test_ends_with_its_line(self) -> None:
        html = render.output_html(parsed("fail", "\x1b[31ma\nb\n"), "k.txt")
        self.assertEqual(html, '<pre><span class="fail">a</span>\nb</pre>')
        rows = render.diff("", "\x1b[31ma\nb\n")
        self.assertEqual(
            render.diff_html(rows),
            '<pre><span class="added"><span class="fail">a</span></span>\n'
            '<span class="added">b</span></pre>',
        )

    def test_escaped(self) -> None:
        self.assertEqual(
            render.pictured('<\x1b[31m<a & "b"\r>\x1b[0m&'),
            '&lt;<span class="fail">&lt;a &amp; &quot;b&quot;\u240d&gt;</span>&amp;',
        )

    def test_compared_as_stored(self) -> None:
        green, red = "\x1b[32m\u2713\x1b[0m x\n", "\x1b[31m\u2713\x1b[0m x\n"
        self.assertEqual(render.added(green, red), {0})
        self.assertEqual(render.added("\u2713 x\n", green), {0})
        self.assertEqual(render.added(green, green), set())
        self.assertEqual(
            render.diff(red, green),
            (
                render.Row(render.Mark.GONE, red.removesuffix("\n")),
                render.Row(render.Mark.ADDED, green.removesuffix("\n")),
            ),
        )


class Compare(unittest.TestCase):
    def check(
        self, now: render.Entry | None, then: render.Entry | None, change: Change
    ) -> None:
        self.assertEqual(render.compare_entry(now, then).change, change)

    def test_changes(self) -> None:
        bad = render.Unreadable("p/c/b/x.json", "no exit")
        self.check(parsed("pass"), parsed("pass"), Change.PASSES_ON_BOTH)
        self.check(parsed("pass"), parsed("fail"), Change.FIXED)
        self.check(parsed("fail"), parsed("pass"), Change.INTRODUCED)
        self.check(parsed("fail", "a\nb\n"), parsed("fail", "a\n"), Change.NEW_LINES)
        self.check(parsed("fail", "a\n"), parsed("fail", "a\nb\n"), Change.NOTHING_NEW)
        self.check(parsed("fail"), None, Change.NO_BASE)
        self.check(None, parsed("pass"), Change.NO_SERIES)
        self.check(bad, parsed("pass"), Change.UNREADABLE)
        self.check(parsed("pass"), bad, Change.UNREADABLE_BASE)
        self.check(parsed("skip", "a"), parsed("skip", "b"), Change.SKIPS_ON_BOTH)
        self.check(parsed("skip"), parsed("fail"), Change.SKIPS)
        self.check(parsed("pass"), parsed("skip"), Change.PASSES_BASE_SKIPS)
        self.check(parsed("fail"), parsed("skip"), Change.FAILS_BASE_SKIPS)
        own = render.parse_record(record("fail", "e\nf\n", range=RANGE))
        alone = render.compare_entry(own, None)
        self.assertEqual(
            (alone.change, alone.own, alone.new, alone.gone),
            (Change.INTRODUCED, render.Own.SERIES_ONLY, 0, 0),
        )
        report = render.parse_record(record("pass", "m\n", range=RANGE))
        quiet = render.compare_entry(report, None)
        self.assertEqual(
            (quiet.change, quiet.new, quiet.label),
            (Change.PASSES_ON_BOTH, 0, "series only"),
        )
        added = render.compare_entry(parsed("pass", "w\n"), None, new_file=True)
        self.assertEqual((added.change, added.new), (Change.PASSES_ON_BOTH, 1))
        based = render.compare_entry(own, parsed("fail", "e\nf\n"))
        self.assertEqual((based.change, based.own), (Change.NOTHING_NEW, None))

    def test_error(self) -> None:
        self.check(parsed("error", "oom\n"), parsed("pass"), Change.ERRORS)
        self.check(parsed("error"), parsed("fail", "e\n"), Change.ERRORS)
        self.check(parsed("error"), parsed("skip"), Change.ERRORS)
        self.check(parsed("fail", "e\n"), parsed("error"), Change.BASE_ERRORS)
        self.check(parsed("skip"), parsed("error"), Change.BASE_ERRORS)
        erred = render.compare_entry(parsed("error"), parsed("pass"))
        self.assertEqual(
            (erred.label, erred.kind, erred.new, erred.gone),
            ("error, no verdict", "error", 0, 0),
        )
        self.assertNotIn(Change.ERRORS, render.INTRODUCING)
        self.assertNotIn(Change.BASE_ERRORS, render.INTRODUCING)
        own = render.parse_record(record("error", range=RANGE))
        alone = render.compare_entry(own, None)
        self.assertEqual(
            (alone.change, alone.label, alone.kind),
            (Change.ERRORS, "series only, error, no verdict", "error"),
        )
        added = render.compare_entry(parsed("error"), None, new_file=True)
        self.assertEqual(
            (added.change, added.own), (Change.ERRORS, render.Own.NEW_IN_SERIES)
        )
        self.assertEqual(
            render.verdict(parsed("error")),
            '<span class="badge error">error, exit 1</span>',
        )
        self.assertIn(".error { color: var(--status-error); }", render.STYLE)
        with self.assertRaises(render.RecordError) as raised:
            render.parse_record(record("flaky"))
        self.assertEqual(
            str(raised.exception), "entry 1: status is not pass, fail, skip or error"
        )

    def test_union_of_paths(self) -> None:
        series = tree({"make/all": parsed("pass")})
        base = tree({"make/all": parsed("pass"), "sparse/x": parsed("fail")}, "base")
        changes = {k: c.change for k, c in render.compare(series, base).items()}
        self.assertEqual(
            changes, {"make/all": Change.PASSES_ON_BOTH, "sparse/x": Change.NO_SERIES}
        )
        lone = render.compare(series, None)
        self.assertEqual(lone["make/all"].change, Change.NO_BASE)

    def test_label(self) -> None:
        c = render.Comparison
        self.assertEqual(c(Change.NEW_LINES, 1, 0).label, "fails on both, +1 \u22120")
        self.assertEqual(
            c(Change.NOTHING_NEW, 0, 2).label, "fails on both, nothing new, \u22122"
        )
        self.assertEqual(c(Change.FIXED, 0, 1).label, "fixed, +0 \u22121")
        self.assertEqual(c(Change.FIXED).label, "fixed")
        self.assertEqual(c(Change.NO_BASE).label, "no base record")
        own, added = render.Own.SERIES_ONLY, render.Own.NEW_IN_SERIES
        for comparison, label, kind in (
            (c(Change.PASSES_ON_BOTH, own=own), "series only", "none"),
            (c(Change.PASSES_ON_BOTH, 3, own=added), "new in the series, +3", "none"),
            (c(Change.INTRODUCED, own=own), "series only, introduced", "fail"),
            (
                c(Change.INTRODUCED, 2, own=added),
                "new in the series, introduced, +2",
                "fail",
            ),
        ):
            with self.subTest(label=label):
                self.assertEqual((comparison.label, comparison.kind), (label, kind))


def ran(*entries: dict[str, object]) -> tuple[render.Record, ...]:
    """The entries of a record file, as render reads them."""
    data = {**record(), "entries": list(entries)}
    return render.parse_entries(data)


class BaseMatching(unittest.TestCase):
    """A series' entry is set against the base's that ran what it ran."""

    def compare(
        self, now: render.Record, runs: tuple[render.Record, ...]
    ) -> render.Comparison:
        base = dataclasses.replace(
            tree({"sparse/x": render.representative(runs)}, "base"),
            runs={"sparse/x": runs},
        )
        return render.compare(tree({"sparse/x": now}), base)["sparse/x"]

    def test_same_run_chosen(self) -> None:
        older = {**CLANG, "version": "20.1.0"}
        runs = ran(
            entry("fail", "w\n", started="2026-11-09T00:00:00Z"),
            entry("pass", tools=[older], started="2026-11-10T00:00:00Z"),
        )
        self.assertEqual(render.representative(runs).tools[0].version, "20.1.0")
        now = render.parse_record(record("fail", "w\nnew\n"))
        found = self.compare(now, runs)
        self.assertEqual(
            (found.change, found.label, found.notes),
            (Change.NEW_LINES, "fails on both, +1 \u22120", []),
        )

    def test_same_config_first(self) -> None:
        a, b = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        runs = ran(
            entry("pass", config=a, started="2026-11-08T00:00:00Z"),
            entry("fail", "w\n", config=b, started="2026-11-09T00:00:00Z"),
        )
        now = render.parse_record(record("fail", "w\n", config=a))
        found = self.compare(now, runs)
        self.assertEqual((found.change, found.reconfigured), (Change.INTRODUCED, False))

    def test_ran_differently(self) -> None:
        for name, changed in (
            ("tools", {"tools": [{**CLANG, "build": "llvmorg-21.1.8"}]}),
            ("command", {"command": "make -s W=1"}),
            ("env", {"env": {**KBUILD, "KCFLAGS": "-Werror"}}),
        ):
            with self.subTest(differs=name):
                runs = ran(entry("pass", **changed))
                found = self.compare(parsed("fail", "w\n"), runs)
                self.assertEqual(
                    (found.change, found.notes, found.ran_differently),
                    (Change.INTRODUCED, ["ran differently"], True),
                )
        same = ran(entry("pass", env={"LLVM": "1", "ARCH": "x86_64"}))
        self.assertEqual(self.compare(parsed("fail"), same).notes, [])

    def test_shown(self) -> None:
        runs = ran(entry("pass", command="make -s W=1"))
        page = render.Page(
            tree({"sparse/x": parsed("fail", "w\n")}),
            SERIES,
            None,
            {"sparse/x": self.compare(parsed("fail", "w\n"), runs)},
        )
        self.assertIn(
            '<span class="badge fail">introduced, +1 \u22120</span>'
            '<span class="badge warn">ran differently</span>',
            render.entry_head(
                "sparse/x", parsed("fail", "w\n"), page.comparisons["sparse/x"]
            ),
        )
        reconfigured = dataclasses.replace(
            page.comparisons["sparse/x"], reconfigured=True
        )
        self.assertEqual(
            reconfigured.notes, ["configured differently", "ran differently"]
        )


class Representative(unittest.TestCase):
    def test_latest_invocation(self) -> None:
        late = invocation(started="2026-11-10T12:00:00Z", seconds=9)
        runs = ran(
            {**entry("fail", "w\n"), "invocations": [invocation(), late]},
            entry("pass", started="2026-11-10T10:00:00Z", seconds=5),
        )
        shown = render.representative(runs)
        self.assertEqual(
            (shown.status, shown.seconds, shown.started),
            (render.Status.FAILED, 9, "2026-11-10T12:00:00Z"),
        )
        self.assertEqual(
            render.parse_record({**record(), "entries": runs_data()}).seconds, 2
        )

    def test_tie_goes_to_the_later(self) -> None:
        runs = ran(entry("pass", seconds=1), entry("fail", "w\n", seconds=2))
        self.assertIs(render.representative(runs).status, render.Status.FAILED)

    def test_fields(self) -> None:
        shown = render.parse_record(
            record(
                "fail",
                "w\n",
                file="init/main.o",
                command="make -s C=2 init/main.o",
                env={**KBUILD, "KCFLAGS": "-Werror -O2"},
                producer={"name": "other-ci", "version": "4.2"},
            )
        )
        self.assertEqual(
            shown.command_line,
            "ARCH=x86_64 LLVM=1 KCFLAGS='-Werror -O2' make -s C=2 init/main.o",
        )
        self.assertEqual(shown.file, "init/main.o")
        self.assertFalse(shown.series_check)
        self.assertEqual(shown.producer, render.Producer("other-ci", "4.2"))
        self.assertEqual(shown.host, render.Host("x86_64", "AMD Ryzen 9", 32, 2**37))
        self.assertTrue(render.parse_record(record(range=RANGE)).series_check)


def runs_data() -> list[dict[str, object]]:
    """Two entries whose invocations are out of order."""
    return [
        {
            **entry(),
            "invocations": [
                invocation(started="2026-11-10T08:00:00Z", seconds=2),
                invocation(started="2026-11-09T08:00:00Z", seconds=3),
            ],
        },
        entry(started="2026-11-10T07:00:00Z", seconds=4),
    ]


def analyzer(name: str) -> tuple[str, str]:
    """A clang_analyzer output of a base and its series, trimmed from records."""
    found = FIXTURES / "clang_analyzer"
    then = (found / f"{name}-base.txt").read_text(encoding="utf-8")
    return then, (found / f"{name}-series.txt").read_text(encoding="utf-8")


class CompilerFindings(unittest.TestCase):
    def test_heads(self) -> None:
        for line in (
            "../fs/xfs/xfs_rtalloc.c:336:9: error: Assigned value is uninitialized",
            "drivers/x.c:12: warning: no previous prototype for 'f'",
            "../a.c:3:10: fatal error: 'x.h' file not found",
            "../lib/radix-tree.c:267:36: error: incorrect type in assignment",
        ):
            with self.subTest(line=line):
                self.assertTrue(render.DIAGNOSTIC.match(line))
        for line in (
            "../kernel/time/hrtimer.c:2351:38: note: Loop condition is false.",
            "../net/ipv6/route.c:2407:39:    got restricted __be32",
            "./fs/a.c:12:3-5: WARNING x",
            "Warning: drivers/cxl/core/hdm.c:337 No description found",
            "make[4]: *** [../scripts/Makefile.build:290: fs/x.o] Error 1",
            " 2109 |                 restart = fn(timer);",
            "11 warnings generated.",
        ):
            with self.subTest(line=line):
                self.assertFalse(render.DIAGNOSTIC.match(line))

    def test_chunks(self) -> None:
        text = (
            "2 warnings generated.\na.c:1:2: error: x\n 1 | y\nb.c:3: warning: z\nend\n"
        )
        self.assertEqual(
            render.chunks(text),
            [
                render.Chunk(("2 warnings generated.",), finding=False),
                render.Chunk(("a.c:1:2: error: x", " 1 | y"), finding=True),
                render.Chunk(("b.c:3: warning: z", "end"), finding=True),
            ],
        )
        self.assertEqual(
            render.chunks("a\nb\n"),
            [render.Chunk(("a",), finding=False), render.Chunk(("b",), finding=False)],
        )

    def test_series_that_moves_code(self) -> None:
        then, now = analyzer("xfs_rtalloc")
        comparison = render.compare_records(parsed("fail", now), parsed("fail", then))
        self.assertEqual(
            (comparison.label, comparison.new, comparison.gone),
            ("fails on both, nothing new", 0, 0),
        )
        self.assertGreater(len(render.added(now, then)), 0)
        self.assertEqual(render.diff_html(comparison.rows), "")
        shown = [row.text for row in comparison.rows]
        drawn = [line for chunk in render.chunks(now) for line in chunk.drawn]
        self.assertEqual(shown, drawn[1:])
        self.assertEqual(drawn[0], "15 warnings generated.")

    def test_one_new_finding(self) -> None:
        then, now = analyzer("core")
        comparison = render.compare_records(parsed("fail", now), parsed("fail", then))
        self.assertEqual(
            (comparison.label, comparison.new, comparison.gone, comparison.kind),
            ("fails on both, +1 finding", 1, 0, "fail"),
        )
        marked = {
            mark: [row.text for row in comparison.rows if row.mark is mark]
            for mark in render.Mark
        }
        head = "../drivers/nvme/host/nvme.h:597:46: error: Access to field 'disk'"
        rows = render.lines(now)
        start = next(i for i, line in enumerate(rows) if line.startswith(head))
        # The head, its source and the note, not the source quoted under it.
        self.assertEqual(marked[render.Mark.ADDED], rows[start : start + 4])
        self.assertEqual(marked[render.Mark.GONE], [])
        html = render.diff_html(comparison.rows, comparison.context)
        self.assertNotIn('class="same"', html)
        self.assertTrue(html.startswith('<pre><span class="skip"></span>\n'))

    def test_introduced(self) -> None:
        then, now = analyzer("hrtimer")
        comparison = render.compare_records(parsed("fail", now), parsed("pass", then))
        self.assertEqual(
            (comparison.label, comparison.new, comparison.gone),
            ("introduced, +1 finding", 1, 0),
        )
        page = render.Page(tree({}), SERIES, None, {"check/x": comparison})
        self.assertTrue(render.prominent(page, "check/x"))

    def test_matched_shows_as_the_series_printed_it(self) -> None:
        then = "a.c:5:1: error: x\na.c:9:1: note: old path\nb.c:2:1: error: gone\n"
        now = "a.c:7:1: error: x\na.c:12:1: note: new path\n 12 | y\n"
        comparison = render.compare_records(parsed("pass", now), parsed("pass", then))
        self.assertEqual(
            render.diff_html(comparison.rows),
            '<pre><span class="same">a.c:7:1: error: x</span>\n'
            '<span class="same">a.c:12:1: note: new path</span>\n'
            '<span class="gone">b.c:2:1: error: gone</span></pre>',
        )
        self.assertEqual(
            render.diff_html(comparison.rows, comparison.context),
            '<pre><span class="skip"></span>\n'
            '<span class="gone">b.c:2:1: error: gone</span></pre>',
        )
        self.assertEqual(comparison.label, "passes on both, \u22121 finding")

    def test_drawn(self) -> None:
        finding = (
            "a.h:146:9: error: x",
            "  146 |         return per_cpu_ptr(q, cpu);",
            "      |                ^",
            "a.h:235:26: note: expanded from macro 'per_cpu_ptr'",
            "  235 |         SHIFT_PERCPU_PTR((ptr), per_cpu_offset((cpu)));",
            "a.c:760:6: note: Assuming the condition is true",
            "  760 |         if (blk_queue_rq_alloc_time(q))",
            "b.c:3:5: note: in expansion of macro 'Y'",
            "a.c:729:2: note: Assuming 'cpu' is >= 64",
            "2 warnings generated.",
        )
        self.assertEqual(
            render.Chunk(finding, finding=True).drawn,
            (*finding[:3], finding[5], finding[8]),
        )
        self.assertEqual(render.Chunk(finding, finding=False).drawn, finding)

    def test_line_by_line_without_a_head(self) -> None:
        then = "a.c:1:2: note: n\nWARNING: x\n"
        comparison = render.compare_records(
            parsed("fail", then + "y\n"), parsed("fail", then)
        )
        self.assertEqual(
            (comparison.unit, comparison.label),
            (render.Unit.LINE, "fails on both, +1 \u22120"),
        )

    def test_labels(self) -> None:
        def c(
            change: Change, new: int, gone: int, own: render.Own | None = None
        ) -> str:
            unit = render.Unit.FINDING
            return render.Comparison(change, new, gone, own=own, unit=unit).label

        added = render.Own.NEW_IN_SERIES
        for label, expected in (
            (c(Change.NEW_LINES, 2, 1), "fails on both, +2 \u22121 findings"),
            (
                c(Change.NOTHING_NEW, 0, 3),
                "fails on both, nothing new, \u22123 findings",
            ),
            (c(Change.NOTHING_NEW, 0, 0), "fails on both, nothing new"),
            (c(Change.FIXED, 0, 1), "fixed, \u22121 finding"),
            (c(Change.PASSES_ON_BOTH, 0, 0), "passes on both"),
            (
                c(Change.INTRODUCED, 2, 0, own=added),
                "new in the series, introduced, +2 findings",
            ),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(label, expected)

    def test_suite_sums_lines_and_findings(self) -> None:
        c, unit = render.Comparison, render.Unit.FINDING
        comparisons = {
            "check/a": c(Change.NEW_LINES, 3, 0),
            "check/b": c(Change.NEW_LINES, 1, 2, unit=unit),
            "check/c": c(Change.NOTHING_NEW, 0, 1, unit=unit),
        }
        page = render.Page(tree({}), SERIES, None, comparisons)
        self.assertEqual(
            render.suite_findings(page),
            '<span class="suite">check <b class="fail" title="2 introduced">2</b>'
            ' <span class="muted" title="3 lines and 1 finding new">+4</span>'
            ' <span class="muted" title="3 findings gone">\u22123</span></span>',
        )


# What a series checks with no counterpart: its own patches, files it adds, and
# a path the base was checked on for another series.
# What a series checks with no counterpart: its own patches, files it adds, and
# a path the base was checked on for another series.
OWN_SERIES = render.Series(
    "b" * 40,
    "v1",
    SERIES.commits,
    (),
    objects=("drivers/x/new.o", "block/a.o"),
    sources=("drivers/x/new.c", "include/linux/new.h", "block/a.c"),
    docs=("block",),
    added=("drivers/x/new.c", "include/linux/new.h"),
)


def checked(path: str, status: str = "pass", output: str = "") -> render.Record:
    """A record of a check of the file path."""
    return render.parse_record(record(status, output, file=path))


class Coverage(unittest.TestCase):
    def labels(self, series: render.Series) -> dict[str, str]:
        now: dict[str, render.Entry] = {
            "make/all": parsed(),
            "checkpatch.pl": render.parse_record(record(range=RANGE)),
            "clang/drivers/x/new.o": checked("drivers/x/new.o", "fail", "w\n"),
            "kernel-doc/include/linux/new.h": checked("include/linux/new.h"),
            "clang/block/a.o": checked("block/a.o"),
            "sparse/block/a.o": checked("block/a.o"),
        }
        then: dict[str, render.Entry] = {
            "make/all": parsed(),
            "clang/block/a.o": checked("block/a.o"),
            "make/checkstack": parsed(),
            "make/htmldocs/block": parsed(),
            "make/htmldocs/filesystems": parsed(),
            "clang/block/other.o": checked("block/other.o"),
            "kernel-doc/include/linux/other.h": checked("include/linux/other.h"),
            "fstests/xfs/generic/001": parsed(),
        }
        comparisons = render.compare(tree(now), tree(then, "base"), series)
        return {key: comparison.label for key, comparison in comparisons.items()}

    def test_own_and_others(self) -> None:
        self.assertEqual(
            self.labels(OWN_SERIES),
            {
                "checkpatch.pl": "series only",
                "clang/block/a.o": "passes on both",
                "clang/drivers/x/new.o": "new in the series, introduced, +1",
                "fstests/xfs/generic/001": "no series record",
                "kernel-doc/include/linux/new.h": "new in the series",
                "make/all": "passes on both",
                "make/checkstack": "no series record",
                "make/htmldocs/block": "no series record",
                "sparse/block/a.o": "no base record",
            },
        )

    def test_by_objects_json(self) -> None:
        """Without objects.json, a build's objects are not the series' own."""
        unbuilt = dataclasses.replace(OWN_SERIES, objects=None)
        labels = self.labels(unbuilt)
        self.assertEqual(labels["clang/block/a.o"], "passes on both")
        self.assertNotIn("clang/block/other.o", labels)
        nothing = dataclasses.replace(OWN_SERIES, objects=(), sources=(), docs=())
        self.assertNotIn("make/htmldocs/block", self.labels(nothing))

    def test_paths(self) -> None:
        self.assertEqual(render.checked_path("make/htmldocs/block", None), "block")
        self.assertEqual(
            render.checked_path("sparse/init/main.o", checked("init/main.o")),
            "init/main.o",
        )
        for key in ("make/all", "fstests/xfs/generic/001", "checkpatch.pl"):
            with self.subTest(key=key):
                self.assertIsNone(render.checked_path(key, parsed()))


class Diff(unittest.TestCase):
    def case(self, then: render.Record, now: render.Record) -> tuple[str, str]:
        comparison = render.compare_records(now, then)
        return comparison.label, render.diff_html(comparison.rows)

    def test_each_case(self) -> None:
        cases = (
            ("pass", "a.c:1: x\n", "pass", "a.c:2: x\n", "passes on both", ""),
            ("pass", "x\n", "pass", "x\ny\n", "passes on both, +1 \u22120", "added"),
            ("fail", "e\n", "pass", "", "fixed, +0 \u22121", "gone"),
            ("pass", "", "fail", "e\n", "introduced, +1 \u22120", "added"),
            ("fail", "e\n", "fail", "e\n", "fails on both, nothing new", ""),
            (
                "fail",
                "a\nb\n",
                "fail",
                "a\n",
                "fails on both, nothing new, \u22121",
                "gone",
            ),
            ("fail", "a\n", "fail", "a\nb\n", "fails on both, +1 \u22120", "added"),
        )
        for was, then, status, now, label, mark in cases:
            with self.subTest(label=label):
                shown, html = self.case(parsed(was, then), parsed(status, now))
                self.assertEqual(shown, label)
                if mark:
                    self.assertIn(f'<span class="{mark}">', html)
                else:
                    self.assertEqual(html, "")

    def test_reordered(self) -> None:
        rows = render.diff("a\nb\nc\nd\n", "c\nd\na\nb\n")
        self.assertEqual({row.mark for row in rows}, {render.Mark.SAME})
        self.assertEqual([row.text for row in rows], ["c", "d", "a", "b"])
        self.assertEqual(render.diff_html(rows), "")

    def test_positions_only(self) -> None:
        then = "f.c:10:2: warning: w\n   10 | int x;\nControl jumps to line 12\n"
        now = "f.c:14:2: warning: w\n   14 | int x;\nControl jumps to line 16\n"
        self.assertEqual(render.diff_html(render.diff(then, now)), "")

    def test_counts_agree_with_the_judge(self) -> None:
        # Seeded, to draw outputs, not secrets.
        chance = random.Random(7)  # noqa: S311
        for _ in range(300):
            then = "".join(f"{chance.choice('abcde')}\n" for _ in range(12))
            now = "".join(f"{chance.choice('abcdef')}\n" for _ in range(12))
            rows = render.diff(then, now)
            marks = [row.mark for row in rows]
            self.assertEqual(
                marks.count(render.Mark.ADDED), len(render.added(now, then))
            )
            self.assertEqual(
                marks.count(render.Mark.GONE), len(render.added(then, now))
            )
            shown = [row.text for row in rows if row.mark is not render.Mark.GONE]
            self.assertEqual(shown, render.lines(now))

    def test_hunks(self) -> None:
        same, added = render.Mark.SAME, render.Mark.ADDED
        rows = [render.Row(same, str(n)) for n in range(20)]
        for n in (2, 5, 15):
            rows[n] = render.Row(added, str(n))
        self.assertEqual(render.hunks(rows), [range(9), range(12, 19)])
        html = render.diff_html(rows)
        self.assertEqual(html.count('<span class="skip"></span>'), 2)
        self.assertTrue(html.startswith('<pre><span class="same">0</span>'))
        rows[5], rows[15] = render.Row(same, "5"), render.Row(same, "15")
        rows[9] = render.Row(added, "9")
        self.assertEqual(render.hunks(rows), [range(13)])

    def test_marks_outside_the_text(self) -> None:
        html = render.diff_html(render.diff("a\n", "b <c>\n"))
        gone, added = '<span class="gone">a</span>', '<span class="added">b &lt;c&gt;'
        self.assertEqual(html, f"<pre>{gone}\n{added}</span></pre>")
        self.assertEqual(re.sub(r"<[^>]+>", "", html), "a\nb &lt;c&gt;")
        self.assertIn('pre .added::before { content: "+ "; }', render.CSS)
        self.assertIn('pre .gone::before { content: "- "; }', render.CSS)

    def test_anchors(self) -> None:
        self.assertEqual(
            render.anchors(["check/a b", "check/a-b", "build"]),
            {
                "build": "r-build",
                "check/a b": "r-check-a-b",
                "check/a-b": "r-check-a-b-2",
            },
        )
        self.assertEqual(render.anchors(['x/"<y>']), {'x/"<y>': "r-x---y-"})


class Configuration(unittest.TestCase):
    def test_records(self) -> None:
        def run(config: str | None) -> render.Record:
            extra = {"config": config} if config else {}
            return render.parse_record(record("fail", "e\n", **extra))

        cases = (("sha256:a", "sha256:b", True), ("sha256:a", "sha256:a", False))
        cases += (("sha256:a", None, False), (None, None, False))
        for now, then, reconfigured in cases:
            with self.subTest(now=now, then=then):
                comparison = render.compare_records(run(now), run(then))
                self.assertEqual(comparison.reconfigured, reconfigured)

    def test_config_lines_match_exactly(self) -> None:
        then = render.config_text(b"CONFIG_PHYSICAL_START=0x1000000\n")
        now = render.config_text(b"CONFIG_PHYSICAL_START=0x2000000\n")
        marks = [row.mark for row in render.diff(then, now, key=str)]
        self.assertEqual(marks, [render.Mark.GONE, render.Mark.ADDED])


class ConfiguredSite(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.records = self.dir / "records"
        self.output = self.dir / "public"

    def make(
        self, base: str | None, series: str, *, record_configs: bool = False
    ) -> str:
        """A base v1 and a series on it, built as B with the given .config."""
        trees = (("v1", base, "sha256:a"), ("v1-1-gx", series, "sha256:b"))
        for name, config, digest in trees:
            tree_dir = self.records.joinpath("p", name, B)
            extra = {"config": digest} if record_configs else {}
            put(tree_dir.joinpath(*MAKE_ALL), record("pass", "", **extra))
            if config is not None:
                put(tree_dir / "config", config)
        put(self.records / "p" / "v1-1-gx" / "series.json", series_json(commit()))
        render.make_site(self.records, self.output)
        page = self.output.joinpath("p", "v1-1-gx", B, "index.html")
        return page.read_text(encoding="utf-8")

    def index(self) -> str:
        return uncovered((self.output / "index.html").read_text(encoding="utf-8"))

    def test_differ(self) -> None:
        page = self.make("A=y\nB=y\nC=0x1000000\n", "A=y\nB=m\nC=0x2000000\n")
        note = '<span class="badge warn">configured differently from its base</span>'
        self.assertIn(f'<a href="./config.txt">.config</a> {note}', page)
        self.assertIn("<h2>Configuration against the base</h2>", page)
        self.assertIn(f'href="../../../../../p/v1/{B}/config.txt"', page)
        self.assertIn(
            '<span class="same">A=y</span>\n<span class="gone">B=y</span>\n'
            '<span class="gone">C=0x1000000</span>\n<span class="added">B=m</span>\n'
            '<span class="added">C=0x2000000</span>',
            page,
        )
        index = self.index()
        self.assertIn(f'{note} <span class="suite">make</span>', index)
        self.assertNotIn(CHECK, index)

    def test_match(self) -> None:
        page = self.make("A=y\n", "A=y\n")
        self.assertNotIn("Configuration against the base", page)
        self.assertNotIn("configured differently", page)
        self.assertIn(f'<span class="suite">make {CHECK}</span>', self.index())

    def test_base_not_recorded(self) -> None:
        page = self.make(None, "A=y\n")
        self.assertIn(
            '.config</a> <span class="muted">base configuration not recorded</span>',
            page,
        )
        self.assertNotIn("Configuration against the base", page)

    def test_records_configured_differently(self) -> None:
        page = self.make("A=y\n", "A=y\n", record_configs=True)
        self.assertIn(
            '<span class="badge pass">passes on both</span>'
            '<span class="badge warn">configured differently</span>',
            page,
        )


class B4Log(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.records = self.dir / "records"

    def overview(self, log: bytes | None, *, applied: str = "m@x") -> str:
        """The overview of a series on v1 from the message applied.

        Its b4/shazam.json printed log, if any.
        """
        name = self.records / "p" / "v1-1-gx"
        put(name.joinpath(B, *MAKE_ALL), record())
        put(name / "series.json", series_json(commit(), message_id=applied))
        if log is not None:
            shazam = self.records / "p" / "m@x" / "b4" / "shazam.json"
            shazam.parent.mkdir(parents=True, exist_ok=True)
            try:
                put(shazam, record(output=log.decode()))
            except UnicodeDecodeError:
                shazam.write_bytes(b'{"output": "' + log + b'"}')
        output = self.dir / "public"
        render.make_site(self.records, output)
        return uncovered(
            (output / "p" / "v1-1-gx" / "index.html").read_text(encoding="utf-8")
        )

    def test_as_written(self) -> None:
        page = self.overview(b" Base: v7.4-rc1\nApplying: <x> & \x1b[K\n")
        self.assertIn(
            "<pre> Base: v7.4-rc1\nApplying: &lt;x&gt; &amp; \u241b[K</pre>", page
        )
        self.assertLess(page.index("<h2>Builds</h2>"), page.index("<h2>b4</h2>"))

    def test_coloured(self) -> None:
        log = "  \x1b[32m\u2713\x1b[0m [PATCH 1/2] <x>\n  \x1b[31m\u2717\x1b[0m y\n"
        page = self.overview(log.encode())
        self.assertIn(
            '<pre>  <span class="pass">\u2713</span> [PATCH 1/2] &lt;x&gt;\n'
            '  <span class="fail">\u2717</span> y</pre>',
            page,
        )

    def test_absent(self) -> None:
        self.assertNotIn('id="b4"', self.overview(None))
        self.assertNotIn('id="b4"', self.overview(b"x\n", applied=""))

    def test_empty(self) -> None:
        self.assertIn("b4 printed nothing.", self.overview(b""))

    def test_not_utf8(self) -> None:
        self.assertIn("p/m@x/b4/shazam.json: not UTF-8", self.overview(b"\xff\n"))


# A series b4 could not apply, as b4 shazam names it, and what b4 printed.
MESSAGE_ID = "20261103-mctp-v1-1-0123abcd+x=y@kernel.org"
NOT_APPLIED = (
    "Applying: mctp: accept MSG_CMSG_COMPAT in recvmsg\n"
    "Patch failed at 0001 mctp: accept MSG_CMSG_COMPAT in recvmsg\n"
    "\x1b[31merror\x1b[0m: patch failed: net/mctp/af_mctp.c:292\n"
    "error: net/mctp/af_mctp.c: patch does not apply\n"
)
# A series from a local mbox, which b4 could not apply either.
MBOX_ID = "20261104-xfs-v1-0-1@local"


def unapplied(
    records: Path, message_id: str, key: str, data: Mapping[str, object]
) -> None:
    """A series by its Message-ID whose record key is data, of that subject."""
    subject = {"project": "linux", "message_id": message_id}
    put(records / "linux" / message_id / f"{key}.json", {**data, "subject": subject})


class NotApplied(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.records = self.dir / "records"
        self.output = self.dir / "public"
        self.b4 = self.records / "linux" / MESSAGE_ID / "b4"
        command = f"b4 shazam {MESSAGE_ID}"
        shazam = record("fail", NOT_APPLIED, command=command, env={})
        unapplied(self.records, MESSAGE_ID, "b4/shazam", shazam)
        unapplied(self.records, MESSAGE_ID, "b4/mbox", record(env={}))
        shutil.copyfile(FIXTURES / "cover.mbx", self.b4 / "mbox.mbx")
        long = "".join(f"line {n}\n" for n in range(render.CUT + 1))
        local = record("fail", long, command="b4 shazam -m xfs.mbx", env={})
        unapplied(self.records, MBOX_ID, "b4/shazam", local)
        put(self.records.joinpath("linux", "v1", B, *MAKE_ALL), record())

    def page(self, *parts: str) -> str:
        render.make_site(self.records, self.output)
        return uncovered(
            self.output.joinpath(*parts, "index.html").read_text(encoding="utf-8")
        )

    def test_not_commits(self) -> None:
        put(self.records / "linux" / "lonely@x" / "b4" / "mbox.mbx", "Subject: s\n")
        unapplied(self.records, "fetched@x", "b4/mbox", record())
        unapplied(self.records, "lost@x", "b4/mbox", record("fail", "no message\n"))
        trees = render.load(self.records, {})
        self.assertEqual(
            [tree.parts for tree in trees], [("linux", "v1", *B.split("/"))]
        )
        found = render.load_unapplied(self.records, {}, trees)
        self.assertEqual(
            [(item.applier, item.name, item.key, item.parts) for item in found],
            [
                (
                    render.Applier.B4,
                    MESSAGE_ID,
                    "b4/shazam",
                    ("linux", MESSAGE_ID),
                ),
                (render.Applier.B4, MBOX_ID, "b4/shazam", ("linux", MBOX_ID)),
                (render.Applier.B4, "lost@x", "b4/mbox", ("linux", "lost@x")),
            ],
        )
        self.assertIsInstance(found[0].cover, render.Cover)
        self.assertIsNone(found[1].cover)

    def test_applied(self) -> None:
        unapplied(
            self.records, MESSAGE_ID, "b4/shazam", record("pass", " Base: v7.3\n")
        )
        found = render.load_unapplied(self.records, {})
        self.assertEqual([item.name for item in found], [MBOX_ID])
        self.assertNotIn(MESSAGE_ID, self.page())

    def test_named_by_a_commit(self) -> None:
        name = self.records / "linux" / "v1-1-gx"
        put(name.joinpath(B, *MAKE_ALL), record())
        put(name / "series.json", series_json(commit(), message_id=MESSAGE_ID))
        trees = render.load(self.records, {})
        found = render.load_unapplied(self.records, {}, trees)
        self.assertEqual([item.name for item in found], [MBOX_ID])
        overview = self.page("linux", "v1-1-gx")
        subject = "[PATCH RFC 0/2] Support for Multiple Atomicity Mode"
        self.assertIn(f"<h1>{subject}</h1>", overview)
        self.assertIn("<pre>Applying: mctp: accept MSG_CMSG_COMPAT", overview)

    def test_no_lists(self) -> None:
        none = '<span class="muted">none</span>'
        page = self.page("linux", MESSAGE_ID)
        self.assertIn(f"<dt>Mailing lists</dt><dd>{none}</dd>", page)
        subject = "[PATCH RFC 0/2] Support for Multiple Atomicity Mode"
        index = self.page()
        self.assertIn(f'{subject}</a><div class="lists">{none}</div>', index)
        self.assertNotIn('<option value="linux-', index)
        search = (self.output / "search.html").read_text(encoding="utf-8")
        self.assertIn(
            ' data-lists="" data-week="2026-W40" data-outcome="unapplied"', search
        )

    def test_b4_with_cover(self) -> None:
        page = self.page("linux", MESSAGE_ID)
        subject = "[PATCH RFC 0/2] Support for Multiple Atomicity Mode"
        self.assertIn(f"<h1>{subject}</h1>", page)
        self.assertIn("b4 could not apply the series", page)
        self.assertIn("<dd>Daniel Gomez &lt;da.gomez@kernel.org&gt;</dd>", page)
        thread = "20260929-nvme-mam-v1-0-48dcbe79cece@samsung.com"
        self.assertIn(f'<a href="https://lore.kernel.org/all/{thread}/"', page)
        self.assertIn(f"$ b4 shazam {MESSAGE_ID}", page)
        self.assertIn('<section><h2>b4</h2><details id="r-b4-shazam" open>', page)
        self.assertIn('<span class="badge fail">fail, exit 1</span>', page)
        self.assertIn(
            "<pre>Applying: mctp: accept MSG_CMSG_COMPAT in recvmsg\n"
            "Patch failed at 0001 mctp: accept MSG_CMSG_COMPAT in recvmsg\n"
            '<span class="fail">error</span>: patch failed: net/mctp/af_mctp.c:292\n'
            "error: net/mctp/af_mctp.c: patch does not apply</pre>",
            page,
        )
        self.assertIn('<dt>Producer</dt><dd><span class="mono">kdevops-ng</span>', page)

    def test_b4_without_cover(self) -> None:
        (self.b4 / "mbox.mbx").unlink()
        page = self.page("linux", MESSAGE_ID)
        self.assertIn(f"<h1>{MESSAGE_ID}</h1>", page)
        self.assertIn('<dt>Cover letter</dt><dd><span class="muted">not recorded', page)
        self.assertIn(f'<a href="https://lore.kernel.org/all/{MESSAGE_ID}/"', page)
        index = self.page()
        self.assertIn(
            '<td><span class="muted">not recorded</span></td>'
            '<td><span class="muted">not recorded</span></td>'
            '<td class="num"></td><td></td>'
            '<td><span class="badge fail">cannot apply</span></td>',
            index,
        )

    def test_back_links(self) -> None:
        for name in (MESSAGE_ID, MBOX_ID):
            with self.subTest(name=name):
                page = self.page("linux", name)
                self.assertIn('<p><a href="../../index.html">\u2190 Back</a></p>', page)

    def test_local_mbox(self) -> None:
        page = self.page("linux", MBOX_ID)
        self.assertIn(f"<h1>{MBOX_ID}</h1>", page)
        self.assertIn("b4 could not apply the series", page)
        self.assertIn('<section><h2>b4</h2><details id="r-b4-shazam" open>', page)
        self.assertIn("$ b4 shazam -m xfs.mbx", page)
        self.assertIn(f"The first {render.CUT} of {render.CUT + 1} lines", page)
        self.assertIn('href="./output/b4/shazam.txt"', page)
        whole = self.output / "linux" / MBOX_ID / "output" / "b4" / "shazam.txt"
        self.assertEqual(whole.read_text(), "".join(f"line {n}\n" for n in range(51)))

    def test_index_rows(self) -> None:
        index = self.page()
        href = "./linux/20261103-mctp-v1-1-0123abcd%2Bx%3Dy%40kernel.org/index.html"
        subject = "[PATCH RFC 0/2] Support for Multiple Atomicity Mode"
        self.assertIn(f'<td><a href="{href}" class="row-link">{subject}</a>', index)
        self.assertIn(
            '<td>Daniel Gomez<div class="text-muted-sm">da.gomez@kernel.org</div></td>'
            '<td>2026-09-29<div class="text-muted-sm">13:17 +0200</div></td>'
            '<td class="num"></td><td></td>'
            '<td><span class="badge fail">cannot apply</span></td></tr>',
            index,
        )
        self.assertIn(
            '<td><a href="./linux/20261104-xfs-v1-0-1%40local/index.html"'
            f' class="row-link">{MBOX_ID}</a>'
            '<div class="lists"><span class="text-muted-sm">no cover letter</span>',
            index,
        )
        self.assertNotIn("No series yet.", index)
        searched = re.findall(r'<tr data-search="([^"]*)"', index)
        self.assertIn(MBOX_ID.lower(), searched)
        b4 = next(text for text in searched if MESSAGE_ID.lower() in text)
        for word in (subject.lower(), "daniel gomez", "da.gomez@kernel.org"):
            self.assertIn(word, b4)
        self.assertNotIn("lkml", b4)

    def test_unreadable(self) -> None:
        put(self.b4 / "shazam.json", "{")
        page = self.page("linux", MESSAGE_ID)
        self.assertIn(f"linux/{MESSAGE_ID}/b4/shazam.json: not JSON", page)
        self.assertIn('<span class="badge warn">unreadable record</span>', self.page())

    def test_error(self) -> None:
        unapplied(self.records, MESSAGE_ID, "b4/shazam", record("error", "lore down\n"))
        index = self.page()
        self.assertIn(
            '<td><span class="badge error">error, exit 1</span></td></tr>', index
        )

    def test_every_link_resolves(self) -> None:
        self.page()
        for page in sorted(self.output.rglob("*.html")):
            for href in re.findall(r'href="([^"]+)"', page.read_text(encoding="utf-8")):
                if href.startswith("https://"):
                    continue
                path, _, fragment = unquote(href.replace("&amp;", "&")).partition("#")
                path = path.partition("?")[0]
                target = (page.parent / path).resolve()
                with self.subTest(page=page, href=href):
                    self.assertTrue(target.is_file())
                    if fragment:
                        self.assertIn(f'id="{fragment}"', target.read_text())


class Findings(unittest.TestCase):
    def test_each_tag(self) -> None:
        c = render.Comparison
        comparisons = {
            "make/all": c(Change.PASSES_ON_BOTH),
            "sparse/a": c(Change.INTRODUCED, 3, 0),
            "sparse/b": c(Change.NEW_LINES, 2, 1),
            "sparse/c": c(Change.NO_BASE),
            "sparse/d": c(Change.FIXED, 0, 4),
            "checkpatch.pl": c(Change.UNREADABLE),
            "make/dir-pkg": c(Change.PASSES_ON_BOTH, 0, 2),
            "fstests/z": c(Change.PASSES_ON_BOTH, 1, 0),
            "kernel-doc/e": c(Change.ERRORS),
            "zz/y": c(Change.NO_SERIES),
        }
        page = render.Page(tree({}), SERIES, None, comparisons)
        self.assertEqual(
            render.suite_findings(page),
            f'<span class="suite">make {CHECK}'
            ' <span class="muted" title="2 lines gone">\u22122</span></span>'
            ' <span class="suite">checkpatch.pl'
            ' <b class="warn" title="1 unreadable">1</b></span>'
            ' <span class="suite">fstests'
            ' <span class="muted" title="1 line new">+1</span></span>'
            ' <span class="suite">kernel-doc'
            ' <b class="error" title="1 with no verdict">1</b></span>'
            ' <span class="suite">sparse <b class="fail" title="2 introduced">2</b>'
            ' <b class="none" title="1 without a base record">1</b>'
            ' <b class="pass" title="1 fixed">1</b>'
            ' <span class="muted" title="5 lines new">+5</span>'
            ' <span class="muted" title="5 lines gone">\u22125</span></span>'
            ' <span class="suite">zz'
            ' <b class="none" title="1 without a series record">1</b></span>',
        )
        self.assertTrue(render.prominent(page, "kernel-doc/e"))

    def test_own_records(self) -> None:
        c, own, added = (
            render.Comparison,
            render.Own.SERIES_ONLY,
            render.Own.NEW_IN_SERIES,
        )
        silent = {
            "check/checkpatch": c(Change.PASSES_ON_BOTH, own=own),
            "check/w1/new.o": c(Change.PASSES_ON_BOTH, own=added),
        }
        page = render.Page(tree({}), SERIES, None, silent)
        self.assertEqual(
            render.suite_findings(page), f'<span class="suite">check {CHECK}</span>'
        )
        self.assertEqual([render.prominent(page, key) for key in silent], [False] * 2)
        found = {
            "check/checkpatch": c(Change.INTRODUCED, own=own),
            "check/w1/new.o": c(Change.INTRODUCED, 1, own=added),
            "check/w1/b.o": c(Change.PASSES_ON_BOTH, 2, own=added),
            "check/w1/a.o": c(Change.NO_BASE),
        }
        page = render.Page(tree({}), SERIES, None, found)
        self.assertEqual(
            render.suite_findings(page),
            '<span class="suite">check <b class="fail" title="2 introduced">2</b>'
            ' <b class="none" title="1 without a base record">1</b>'
            ' <span class="muted" title="3 lines new">+3</span></span>',
        )
        self.assertEqual([render.prominent(page, key) for key in found], [True] * 4)
        introduced = render.Page(
            tree({}), SERIES, None, {"make/all": c(Change.INTRODUCED)}
        )
        self.assertTrue(render.prominent(introduced, "make/all"))

    def test_not_a_series(self) -> None:
        page = render.Page(tree({}), None, None, {})
        self.assertIn("not set against a base", render.suite_findings(page))

    def test_index_columns(self) -> None:
        cover = render.parse_cover((FIXTURES / "cover.mbx").read_bytes())
        self.assertEqual(
            render.author_parts(cover, SERIES), ("Daniel Gomez", "da.gomez@kernel.org")
        )
        self.assertEqual(render.author_parts(None, SERIES), ("A B", "a@x"))
        three = render.Series("b", "v1", SERIES.commits * 3, ())
        self.assertEqual(render.index_title(None, three), "s and 2 more")
        self.assertEqual(render.index_title(cover, three), cover.subject)

    def test_date_and_time(self) -> None:
        cover = render.parse_cover((FIXTURES / "cover.mbx").read_bytes())
        self.assertEqual(
            render.date_html(cover),
            '2026-09-29<div class="text-muted-sm">13:17 +0200</div>',
        )
        unknown = render.Cover("", "", "Tue, 29 Sep 2026 13:17:10 -0000", "")
        self.assertEqual(
            render.date_html(unknown),
            '2026-09-29<div class="text-muted-sm">13:17</div>',
        )
        odd = render.Cover("", "", "<garbage>", "")
        self.assertEqual(render.date_html(odd), "&lt;garbage&gt;")
        self.assertIn("not recorded", render.date_html(None))


class Lists(unittest.TestCase):
    def test_short_names(self) -> None:
        self.assertEqual(render.list_name("linux-kernel@vger.kernel.org"), "LKML")
        self.assertEqual(render.list_name("Linux-Kernel@VGER.kernel.org"), "LKML")
        self.assertEqual(
            render.list_name("linux-nvme@lists.infradead.org"), "linux-nvme"
        )

    def test_tag(self) -> None:
        self.assertEqual(
            render.list_tag("linux-block@vger.kernel.org"),
            '<span class="tag" title="linux-block@vger.kernel.org">linux-block</span>',
        )
        self.assertEqual(
            render.list_tag('<x>"@y'),
            '<span class="tag" title="&lt;x&gt;&quot;@y">&lt;x&gt;&quot;</span>',
        )


def without(data: Mapping[str, object], name: str) -> dict[str, object]:
    return {k: v for k, v in data.items() if k != name}


class Parse(unittest.TestCase):
    def test_reasons(self) -> None:
        one = entry()
        seen = invocation()
        none: list[object] = []
        cases: tuple[tuple[object, str], ...] = (
            (5, "not a JSON object"),
            ("status unknown", "not a JSON object"),
            (["status"], "not a JSON object"),
            (record(schema=2), "schema is not 1"),
            (record(schema=True), "schema is not an integer"),
            (without(record(), "subject"), "no subject"),
            ({**record(), "subject": "s"}, "subject is not a JSON object"),
            ({**record(), "subject": {"commit": TIP}}, "no project"),
            ({**record(), "subject": {"project": "linux"}}, "no commit"),
            (record(file=1), "file is not a string"),
            (record(build="b"), "build is not a JSON object"),
            (
                record(build={**BUILT, "compiler": "tcc"}),
                "compiler is not clang or gcc",
            ),
            (record(build={**BUILT, "configuration": []}), "configuration is empty"),
            (
                {**record(), "subject": {"project": "linux", "message_id": "a/b@c"}},
                "message_id is not a Message-ID",
            ),
            ({**record(), "entries": none}, "entries is empty"),
            ({**record(), "entries": [5]}, "entry 1: an entry is not a JSON object"),
            (
                record(status="flaky"),
                "entry 1: status is not pass, fail, skip or error",
            ),
            (record(exit=True), "entry 1: exit is not an integer"),
            (record(output=None), "entry 1: output is not a string"),
            (record(command=["make"]), "entry 1: command is not a string"),
            (record(env={"A": 1}), "entry 1: env is not a JSON object of strings"),
            (record(tools=[]), "entry 1: tools is empty"),
            (record(tools=[{"name": "x"}]), "entry 1: no version"),
            (record(config=5), "entry 1: config is not a string"),
            (
                {**record(), "entries": [one, without(one, "command")]},
                "entry 2: no command",
            ),
            (
                {**record(), "entries": [{**one, "invocations": none}]},
                "entry 1: invocations is empty",
            ),
            (record(seconds="<x>"), "entry 1: seconds is not an integer"),
            (record(producer="me"), "entry 1: producer is not a JSON object"),
            (
                {
                    **record(),
                    "entries": [{**one, "invocations": [without(seen, "started")]}],
                },
                "entry 1: no started",
            ),
            (
                record(machine={**MACHINE, "cpus": "16"}),
                "entry 1: cpus is not an integer",
            ),
            (
                record(machine={**MACHINE, "memory": True}),
                "entry 1: memory is not an integer",
            ),
            (record(machine=without(MACHINE, "arch")), "entry 1: no arch"),
            (
                {
                    **record(),
                    "entries": [{**one, "invocations": [without(seen, "origin")]}],
                },
                "entry 1: no origin",
            ),
            (record(run=7), "entry 1: run is not a string"),
            (record(resources=[]), "entry 1: resources is not a JSON object"),
            (record(resources={"gpu": {}}), "entry 1: resources has no gpu"),
            (
                record(resources={"cpu": {"idle_seconds": 1}}),
                "entry 1: resources: cpu has no idle_seconds",
            ),
            (
                record(resources={"memory": {"peak": 1.5}}),
                "entry 1: resources: memory peak is not an integer",
            ),
            (
                record(resources={"cpu": {"seconds": "2"}}),
                "entry 1: resources: cpu seconds is not a number",
            ),
            (
                record(resources={"io": {"read_bytes": -1}}),
                "entry 1: resources: io read_bytes is negative",
            ),
        )
        for data, reason in cases:
            with self.subTest(data=data):
                with self.assertRaises(render.RecordError) as raised:
                    render.parse_record(data)
                self.assertEqual(str(raised.exception), reason)

    def test_optional(self) -> None:
        bare = render.parse_record(record())
        self.assertEqual((bare.output, bare.config, bare.file), ("", None, None))
        self.assertFalse(bare.series_check)
        commit_fields = commit()
        self.assertEqual(render.parse_commit(commit_fields).link, "")
        self.assertEqual(
            render.parse_commit({**commit_fields, "link": LINK}).link, LINK
        )
        for broken, reason in (
            ({**commit_fields, "link": None}, "link is not a string"),
            (without(commit_fields, "link"), "no link"),
        ):
            with self.subTest(reason=reason):
                with self.assertRaises(render.RecordError) as raised:
                    render.parse_commit(broken)
                self.assertEqual(str(raised.exception), reason)

    def test_series(self) -> None:
        series = render.parse_series(
            series_json(
                commit(),
                message_id="m@x",
                ref="xfs-fix",
                added=["a.c"],
                trees=[{"remote": "kdevops", "url": "https://github.com/x/linux"}],
            )
        )
        self.assertEqual(
            (series.message_id, series.ref, series.added, series.objects),
            ("m@x", "xfs-fix", ("a.c",), None),
        )
        self.assertEqual(
            series.trees, (render.BaseTree("kdevops", "https://github.com/x/linux"),)
        )
        lists = ["linux-block@vger.kernel.org", "linux-kernel@vger.kernel.org"]
        self.assertEqual(
            render.parse_series(series_json(lists=lists)).lists,
            ("linux-kernel@vger.kernel.org", "linux-block@vger.kernel.org"),
        )
        for data, reason in (
            (series_json(sources=[1]), "sources is not a list of strings"),
            (without(series_json(), "lists"), "no lists"),
            (series_json(lists=[1]), "lists is not a list of strings"),
            (without(series_json(), "ref"), "no ref"),
            (without(series_json(), "trees"), "no trees"),
            (series_json(message_id="../../x"), "message_id is not a Message-ID"),
            (series_json(message_id="a@b/c"), "message_id is not a Message-ID"),
            (series_json(schema=2), "schema is not 1"),
        ):
            with self.subTest(reason=reason):
                with self.assertRaises(render.RecordError) as raised:
                    render.parse_series(data)
                self.assertEqual(str(raised.exception), reason)

    def test_objects(self) -> None:
        objects: dict[str, object] = {"schema": 1, "commit": TIP, "build": BUILT}
        none: list[str] = []
        self.assertEqual(
            render.parse_objects({**objects, "objects": ["a.o"], "unbuilt": ["b.c"]}),
            (("a.o",), ("b.c",)),
        )
        for data, reason in (
            ({**objects, "objects": none}, "no unbuilt"),
            (without(objects, "commit"), "no commit"),
            ({**without(objects, "build"), "unbuilt": none}, "no build"),
        ):
            with self.subTest(reason=reason):
                with self.assertRaises(render.RecordError) as raised:
                    render.parse_objects(data)
                self.assertEqual(str(raised.exception), reason)


class CoverLetter(unittest.TestCase):
    def test_fixture(self) -> None:
        cover = render.parse_cover((FIXTURES / "cover.mbx").read_bytes())
        self.assertEqual(
            cover,
            render.Cover(
                subject="[PATCH RFC 0/2] Support for Multiple Atomicity Mode",
                author="Daniel Gomez <da.gomez@kernel.org>",
                date="Tue, 29 Sep 2026 13:17:10 +0200",
                message_id="<20260929-nvme-mam-v1-0-48dcbe79cece@samsung.com>",
                change_id="20260929-nvme-mam-073f2ed13bdc",
            ),
        )

    def test_absent_headers(self) -> None:
        self.assertEqual(render.parse_cover(b""), render.Cover("", "", "", ""))

    def test_unreadable_headers(self) -> None:
        with self.assertRaises(render.RecordError):
            render.parse_cover(b"From: ]t, :a?\x81)\n")

    def test_thread_url(self) -> None:
        bare = "20260929-nvme-mam-v1-0-48dcbe79cece@samsung.com"
        self.assertEqual(
            render.thread_url(f"<{bare}>"), f"https://lore.kernel.org/all/{bare}/"
        )
        self.assertEqual(
            render.thread_url(" <a/b c+d=e@f> "),
            "https://lore.kernel.org/all/a%2Fb%20c+d=e@f/",
        )
        self.assertIsNone(render.thread_url(""))
        self.assertIsNone(render.thread_url("<>"))

    def test_thread_from_series(self) -> None:
        bare = "20261104-cache-v8-0-eea5b90e4318@kernel.org"
        thread = f"https://lore.kernel.org/all/{bare}/"
        for cover in (render.parse_cover(b""), None):
            items = dict(render.cover_items(cover, bare))
            self.assertIn(thread, items["Thread"])
        self.assertNotIn("Thread", dict(render.cover_items(None)))


class Links(unittest.TestCase):
    def test_web_link(self) -> None:
        self.assertEqual(render.web_link(LINK), LINK)
        for value in (
            "",
            "javascript:alert(1)",
            f" {LINK}",
            "https://",
            "patch.msgid.link/x",
            "data:text/html,x",
        ):
            with self.subTest(value=value):
                self.assertIsNone(render.web_link(value))

    def test_revision(self) -> None:
        self.assertEqual(
            render.revision_html(f"{SHA}-dirty", COMMITS),
            f'<a href="https://github.com/linux-kdevops/kdevops-ng/commit/{SHA}"'
            f' class="mono">{SHA[:12]}-dirty</a>',
        )
        self.assertEqual(
            render.revision_html("unknown", COMMITS),
            '<span class="mono">unknown</span>',
        )
        self.assertEqual(
            render.revision_html(SHA[:12], COMMITS),
            f'<span class="mono">{SHA[:12]}</span>',
        )

    def test_producer(self) -> None:
        self.assertEqual(
            render.producer_html(render.Producer("kdevops-ng", SHA)),
            '<span class="mono">kdevops-ng</span> <a href="https://github.com/'
            f'linux-kdevops/kdevops-ng/commit/{SHA}" class="mono">{SHA[:12]}</a>',
        )
        self.assertEqual(
            render.producer_html(render.Producer("other-ci", SHA)),
            f'<span class="mono">other-ci</span> <span class="mono">{SHA}</span>',
        )
        self.assertEqual(
            render.producer_html(render.Producer("<x>", "4.2")),
            '<span class="mono">&lt;x&gt;</span> <span class="mono">4.2</span>',
        )


def linux(commit: str) -> render.Tree:
    return render.Tree("linux", commit, "b", {}, None, None, None)


class CodeLinks(unittest.TestCase):
    CODE = "https://github.com/linux-kdevops/linux/blob"
    SERIES = render.Series(
        "c8c53d669d4e45b3a804013251a34387d7e11bfb",
        "v7.3-13641-gc8c53d669d4e",
        (render.Commit("0bc757dd81463e77fdb63ce9456bfdeff44fffe8", "s", "A <a@x>"),),
        (),
    )

    def test_revision(self) -> None:
        head = linux("v7.3-13642-g0bc757dd8146")
        base = linux(self.SERIES.base_name)
        self.assertEqual(
            render.revision(head, self.SERIES), self.SERIES.commits[0].commit
        )
        self.assertEqual(render.revision(base, self.SERIES), self.SERIES.base)
        self.assertEqual(render.revision(base, None), "c8c53d669d4e")
        self.assertEqual(render.revision(linux("v7.3"), None), "v7.3")

    def test_linked(self) -> None:
        cited = render.cites(linux("v7.3-1-gabcdef012345"), None)
        self.assertIsNotNone(cited)
        code = f"{self.CODE}/abcdef012345"
        self.assertEqual(
            render.pictured("../block/blk-mq.h:146:9: error: x <y>", cited),
            f'<a href="{code}/block/blk-mq.h#L146">../block/blk-mq.h:146</a>'
            ":9: error: x &lt;y&gt;",
        )
        for line, href in (
            ("Warning: block/blk-mq.c:3281 No description", "block/blk-mq.c#L3281"),
            ("#12: FILE: fs/xfs/xfs_rtalloc.c:735:", "fs/xfs/xfs_rtalloc.c#L735"),
            ("./fs/a.c:12:3-5: WARNING x", "fs/a.c#L12"),
            (
                "make[4]: *** [../scripts/Makefile.build:290: x.o]",
                "scripts/Makefile.build#L290",
            ),
        ):
            with self.subTest(line=line):
                self.assertIn(f"{code}/{href}", render.pictured(line, cited))
        for line in (
            "/nix/store/0abc-glibc/include/stdio.h:3:1: note: here",
            "./include/generated/autoconf.h:5:1: warning: x",
            "Execution continues on line 734",
            "17:45:36",
        ):
            with self.subTest(line=line):
                self.assertNotIn("<a ", render.pictured(line, cited))
        self.assertIsNone(render.cites(tree({}), None))

    def test_gone_links_to_the_base(self) -> None:
        cited = render.cites(linux("v7.3-13642-g0bc757dd8146"), self.SERIES)
        base = render.cites(linux(self.SERIES.base_name), self.SERIES)
        rows = (
            render.Row(render.Mark.GONE, "a.c:1:1: error: x"),
            render.Row(render.Mark.ADDED, "a.c:2:1: error: y"),
        )
        html = render.diff_html(rows, 0, cited, base)
        self.assertIn(f"{self.CODE}/{self.SERIES.base}/a.c#L1", html)
        self.assertIn(f"{self.CODE}/{self.SERIES.commits[0].commit}/a.c#L2", html)


B1, B2 = "b1/x86_64/clang", "b2/x86_64/clang"
# The cover letter in FIXTURES, by its Message-ID.
THREAD = "20260929-nvme-mam-v1-0-48dcbe79cece@samsung.com"
# The lists its Cc names, as series.json holds them.
LISTS = [
    "linux-block@vger.kernel.org",
    "linux-kernel@vger.kernel.org",
    "linux-nvme@lists.infradead.org",
]


class SeriesPage(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.records = self.dir / "records"
        self.output = self.dir / "public"

    def make(
        self, *, cover: bool, link: str | None, revision: str | None, **fields: object
    ) -> None:
        """A base v1 built as B1, and a series on it built as B1 and B2.

        fields go into the series' series.json.
        """
        producer = kdevops_ng(revision) if revision else PRODUCER
        base = self.records.joinpath("linux", "v1", B1)
        put(base.joinpath(*MAKE_ALL), record(producer=producer))
        put(base / "sparse" / "x.json", record("fail", "w\n", producer=producer))
        patch = commit("nvme: x", link=link or "")
        series = self.records / "linux" / "v1-1-gabc"
        for build in (B1, B2):
            put(series.joinpath(build, *MAKE_ALL), record(producer=producer))
            put(series / build / "sparse" / "x.json", record("fail", "w\nnew\n"))
        message_id = THREAD if cover else ""
        if "cover_kept" in fields:
            fields["cover"] = fields.pop("cover_kept")
        put(series / "series.json", series_json(patch, message_id=message_id, **fields))
        if cover:
            mbox = self.records / "linux" / THREAD / "b4" / "mbox.mbx"
            mbox.parent.mkdir(parents=True)
            shutil.copyfile(FIXTURES / "cover.mbx", mbox)
        put(
            self.records / "linux" / "v2" / "series.json", series_json(commit("lonely"))
        )
        render.make_site(self.records, self.output)

    def page(self, *parts: str) -> str:
        return uncovered(
            self.output.joinpath(*parts, "index.html").read_text(encoding="utf-8")
        )

    def root(self, name: str) -> str:
        return uncovered((self.output / name).read_text(encoding="utf-8"))

    def test_one_row_per_series(self) -> None:
        self.make(cover=False, link=None, revision=None)
        index = self.page()
        self.assertEqual(index.count('href="./linux/v1-1-gabc/index.html"'), 1)
        self.assertIn(
            f'<div><a href="./linux/v1-1-gabc/{B1}/index.html" class="mono">{B1}</a>'
            f' <span class="suite">make {CHECK}</span>'
            ' <span class="suite">sparse <b class="fail" title="1 introduced">1</b>'
            ' <span class="muted" title="1 line new">+1</span></span></div>',
            index,
        )
        self.assertIn(
            f'<div><a href="./linux/v1-1-gabc/{B2}/index.html" class="mono">{B2}</a>'
            ' <span class="suite">make'
            ' <b class="none" title="1 without a base record">1</b></span>',
            index,
        )
        self.assertIn(
            '<a href="./linux/v1/index.html" class="mono row-link">v1</a></td><td>'
            f'<a href="./linux/v1/{B1}/index.html" class="mono">{B1}</a>'
            ' <span class="badge fail">1 failing</span></td>',
            self.root("bases.html"),
        )

    def test_back_links(self) -> None:
        self.make(cover=True, link=None, revision=None)
        up = "../../../../../linux"
        for parts, href in (
            (("linux", "v1-1-gabc"), "../../index.html"),
            (("linux", "v1"), "../../bases.html"),
            (("linux", "v1-1-gabc", *B1.split("/")), f"{up}/v1-1-gabc/index.html"),
            (("linux", "v1", *B1.split("/")), f"{up}/v1/index.html"),
        ):
            with self.subTest(page=parts):
                page = self.page(*parts)
                self.assertIn(
                    f'<p><a href="{href}">\u2190 Back</a></p>\n<section>', page
                )
        index = self.page()
        base = render.base_commit_html("linux", render.parse_series(series_json()))
        self.assertIn(f'class="mono">v1</a> {base}</td>', index)
        self.assertIn(f"{base}</dd>", self.page("linux", "v1-1-gabc"))
        self.assertIn(f"{base}</dd>", self.page("linux", "v1-1-gabc", *B1.split("/")))

    def test_row_links(self) -> None:
        self.make(cover=True, link=LINK, revision=SHA)
        raw = {
            name: self.output.joinpath(*name).read_text(encoding="utf-8")
            for name in (
                ("index.html",),
                ("bases.html",),
                ("search.html",),
                ("linux", "v1-1-gabc", "index.html"),
                ("linux", "v1", "index.html"),
            )
        }
        table = re.compile(
            r'<table class="(?:fixed )?rows(?: bases)?">.*?<tbody>(.*?)</tbody>',
            re.DOTALL,
        )
        main = re.compile(r'<a href="([^"]+)" class="(?:mono )?row-link">')
        cover = re.compile(
            r'<a class="row-cover" href="([^"]+)" tabindex="-1" aria-hidden="true">'
        )
        for name, page in raw.items():
            bodies = table.findall(page)
            self.assertTrue(bodies, name)
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", "".join(bodies)):
                cells = re.findall(r"<td[^>]*>(.*?)</td>", row)
                links = main.findall(cells[0])
                self.assertEqual(len(links), 1, row)
                self.assertNotIn("row-cover", cells[0])
                for cell in cells[1:]:
                    self.assertEqual(cover.findall(cell), links, cell)
                    self.assertNotIn("row-link", cell)
        index = uncovered(raw[("index.html",)])
        self.assertIn('<table class="fixed rows">', index)
        self.assertIn(
            f'<a href="./linux/v1-1-gabc/{B1}/index.html" class="mono">{B1}</a>', index
        )
        self.assertIn(
            f'<a href="../../linux/v1-1-gabc/{B1}/index.html" class="mono row-link">'
            f"{B1}</a>",
            raw[("linux", "v1-1-gabc", "index.html")],
        )
        for rule in (
            "table.rows tbody tr { cursor: pointer; }",
            "table.rows tbody td { position: relative; }",
            "a.row-link { color: inherit; text-decoration: none; }",
            'a.row-link::after { content: ""; position: absolute; inset: 0; }',
            "a.row-cover { position: absolute; inset: 0; }",
            "a.row-link:focus-visible::after { outline: 2px solid var(--link);",
            "table.rows tbody tr:focus-within { background: var(--hover); }",
            "table.rows a:not(.row-link, .row-cover), table.rows [title] {",
        ):
            self.assertIn(rule, render.STYLE)
        for rule in re.findall(r"([^{}]*)\{([^{}]*)\}", render.STYLE):
            if re.search(r"\btr\b", rule[0]):
                self.assertNotIn("position", rule[1], rule[0])
        for name in (".controls {", ".pagination {"):
            start = render.STYLE.index(name)
            rule = render.STYLE[start : render.STYLE.index("}", start)]
            self.assertIn("position: relative; z-index: 2;", rule)

    def test_diff_page(self) -> None:
        self.make(cover=False, link=None, revision=None)
        page = self.page("linux", "v1-1-gabc", *B1.split("/"))
        self.assertIn('<details id="r-sparse-x" open>', page)
        self.assertIn('<details id="r-make-all"><summary>', page)
        self.assertIn(
            '<input type="checkbox" class="toggle series" id="series-r-sparse-x">'
            '<input type="checkbox" class="toggle base" id="base-r-sparse-x">'
            '<p class="note toggles">'
            '<label for="series-r-sparse-x" class="series">series output</label>'
            '<label for="base-r-sparse-x" class="base">base output</label>'
            f'<a href="../../../../../linux/v1/{B1}/index.html#r-sparse-x">'
            "base page</a></p>"
            '<div class="out series"><p class="note text-muted-sm">series output</p>'
            "<pre>w\nnew</pre></div>"
            '<div class="out base"><p class="note text-muted-sm">base output</p>'
            "<pre>w</pre></div></div>",
            page,
        )
        self.assertIn('<pre><span class="same">w</span>\n<span class="added">new', page)
        self.assertIn("fails on both, +1 \u22120", page)
        self.assertIn(
            '<p class="step muted mono">$ ARCH=x86_64 LLVM=1 make -s</p>', page
        )
        self.assertFalse(
            self.output.joinpath("linux", "v1-1-gabc", B1, "output").exists()
        )
        base = self.page("linux", "v1", *B1.split("/"))
        self.assertIn('<details id="r-sparse-x"><summary>', base)
        self.assertIn("<pre>w</pre>", base)
        self.assertNotIn('class="same"', base)
        alone = self.page("linux", "v1-1-gabc", *B2.split("/"))
        self.assertIn('<details id="r-sparse-x" open>', alone)
        self.assertIn("<pre>w\nnew</pre>", alone)
        self.assertNotIn("series output", alone)

    def test_differing_first(self) -> None:
        base = self.records.joinpath("p", "v1", B)
        series = self.records.joinpath("p", "v1-1-gx", B)
        for tree_dir, extra in ((base, ""), (series, "z\n")):
            put(tree_dir / "sparse" / "a.json", record("pass", "same\n"))
            put(tree_dir / "sparse" / "b.json", record("fail", "y\n" + extra))
        put(self.records / "p" / "v1-1-gx" / "series.json", series_json(commit()))
        render.make_site(self.records, self.output)
        page = self.page("p", "v1-1-gx", *B.split("/"))
        self.assertLess(page.index('id="r-sparse-b"'), page.index('id="r-sparse-a"'))

    def test_own_records_and_others(self) -> None:
        base = self.records.joinpath("p", "v1", B)
        series = self.records.joinpath("p", "v1-1-gx", B)
        put(base / "clang" / "a.o.json", record(file="a.o"))
        put(base / "clang" / "other.o.json", record("fail", "other\n", file="other.o"))
        put(series / "clang" / "a.o.json", record(file="a.o"))
        put(series / "clang" / "new.o.json", record("fail", "w\n", file="new.o"))
        tip = series.parent.parent.parent
        put(tip / "checkpatch.pl.json", record("fail", "e\n", range=RANGE))
        put(tip / "get_maintainer.pl.json", record("pass", "m\n", range=RANGE))
        objects = {"schema": 1, "commit": TIP, "build": BUILT}
        objects |= {"objects": ["a.o", "new.o"], "unbuilt": ["x/y.c"]}
        put(series / "objects.json", objects)
        put(
            series.parent.parent.parent / "series.json",
            series_json(commit(), sources=["a.c", "new.c"], added=["new.c"]),
        )
        render.make_site(self.records, self.output)
        index = self.page()
        self.assertIn(
            'class="mono">no build</a> <span class="suite">checkpatch.pl'
            ' <b class="fail" title="1 introduced">1</b></span>'
            f' <span class="suite">get_maintainer.pl {CHECK}</span>',
            index,
        )
        self.assertIn(
            f'class="mono">{B}</a>'
            ' <span class="suite">clang <b class="fail" title="1 introduced">1</b>'
            ' <span class="muted" title="1 line new">+1</span></span>',
            index,
        )
        page = self.page("p", "v1-1-gx", render.NO_BUILD_DIR)
        self.assertIn('<dt>Build</dt><dd><span class="mono">no build</span></dd>', page)
        self.assertIn(
            '<details id="r-checkpatch.pl" open><summary>'
            '<span class="mono">checkpatch.pl</span>'
            '<span class="badge fail">fail, exit 1</span>'
            '<span class="badge fail">series only, introduced</span>',
            page,
        )
        self.assertIn(
            '<details id="r-get_maintainer.pl"><summary>'
            '<span class="mono">get_maintainer.pl</span>'
            '<span class="badge pass">pass, exit 0</span>'
            '<span class="badge none">series only</span>',
            page,
        )
        page = self.page("p", "v1-1-gx", *B.split("/"))
        self.assertNotIn("checkpatch.pl", page)
        self.assertIn(
            '<details id="r-clang-new.o" open><summary>'
            '<span class="mono">new.o</span>'
            '<span class="badge fail">fail, exit 1</span>'
            '<span class="badge fail">new in the series, introduced, +1</span>',
            page,
        )
        self.assertIn("<pre>w</pre>", page)
        self.assertNotIn("other", page)
        self.assertNotIn("objects", page)
        self.assertIn("<dt>Not built</dt><dd>x/y.c</dd>", page)

    def test_no_build_named_by_target_list(self) -> None:
        runs = (("softmmu", "pass", 2), ("softmm", "fail", 3), ("user", "fail", 1))
        entries = [
            entry(
                status,
                command=f"configure --target-list=x86_64-{targets}",
                started=f"2026-01-0{day}T00:00:00Z",
            )
            for targets, status, day in runs
        ]
        configure = {**record(), "entries": entries}
        put(self.records / "qemu" / "v2" / "configure.json", configure)
        put(self.records / "p" / "v2" / "checkpatch.pl.json", record())
        render.make_site(self.records, self.output)
        page = self.page("qemu", "v2", render.NO_BUILD_DIR)
        self.assertIn("<title>v2 x86_64-softmmu", page)
        self.assertIn(
            '<dt>Build</dt><dd><span class="mono">x86_64-softmmu</span></dd>', page
        )
        self.assertIn(">x86_64-softmmu</a>", self.page("qemu", "v2"))
        self.assertIn(">x86_64-softmmu</a>", self.root("qemu-bases.html"))
        self.assertNotIn("no build", page)
        entries[0] |= {"status": "fail", "exit": 1}
        put(self.records / "qemu" / "v2" / "configure.json", configure)
        kernel, qemu = sorted(render.load(self.records, {}), key=lambda t: t.project)
        self.assertEqual((qemu.named, qemu.build), ("x86_64-softmm", render.NO_BUILD))
        self.assertEqual(qemu.parts[2:], (render.NO_BUILD_DIR,))
        self.assertEqual(kernel.named, render.NO_BUILD)
        page = self.page("p", "v2", render.NO_BUILD_DIR)
        self.assertIn('<dt>Build</dt><dd><span class="mono">no build</span></dd>', page)

    def test_commit_without_builds_skipped(self) -> None:
        self.make(cover=False, link=None, revision=None)
        self.assertFalse((self.output / "linux" / "v2").exists())
        self.assertNotIn("lonely", self.page())

    def test_overviews(self) -> None:
        self.make(cover=False, link=None, revision=None)
        series = self.page("linux", "v1-1-gabc")
        self.assertIn('<p class="muted mono">v1-1-gabc</p>', series)
        self.assertIn('href="../../linux/v1/index.html" class="mono">v1</a>', series)
        self.assertIn("<th>Build</th><th>Findings</th><th>Producer</th>", series)
        self.assertIn(f'href="../../linux/v1-1-gabc/{B2}/index.html"', series)
        self.assertNotIn("<dt>Ref</dt>", series)
        base = self.page("linux", "v1")
        self.assertIn('<h1 class="mono">v1</h1>', base)
        self.assertIn(
            '<td class="num">2</td><td class="num">'
            '<span class="badge fail">1 failing</span></td>',
            base,
        )
        build = self.page("linux", "v1-1-gabc", *B1.split("/"))
        self.assertIn(
            '<a href="../../../../../linux/v1-1-gabc/index.html" class="mono">'
            "v1-1-gabc</a>",
            build,
        )
        self.assertIn(f'href="../../../../../linux/v1/{B1}/index.html"', build)
        self.assertIn(
            "(no records of this build)",
            self.page("linux", "v1-1-gabc", *B2.split("/")),
        )

    def test_ref_and_trees(self) -> None:
        tree = {"remote": "kdevops", "url": "https://github.com/linux-kdevops/linux"}
        self.make(cover=False, link=None, revision=None, ref="xfs-fix", trees=[tree])
        series = self.page("linux", "v1-1-gabc")
        self.assertIn('<dt>Ref</dt><dd><span class="mono">xfs-fix</span></dd>', series)
        self.assertIn(
            '<p class="muted mono"><a href="https://github.com/linux-kdevops/linux/'
            f'commit/{TIP}" class="mono" title="kdevops">v1-1-gabc</a></p>',
            series,
        )
        build = self.page("linux", "v1-1-gabc", *B1.split("/"))
        self.assertIn('<dt>Ref</dt><dd><span class="mono">xfs-fix</span></dd>', build)
        search = self.root("search.html")
        self.assertIn(" xfs-fix ", search)

    def test_every_link_resolves(self) -> None:
        self.make(cover=True, link=LINK, revision=SHA)
        pages = sorted(self.output.rglob("*.html"))
        self.assertEqual(len(pages), 11)
        for page in pages:
            for href in re.findall(r'href="([^"]+)"', page.read_text(encoding="utf-8")):
                if href.startswith("https://"):
                    continue
                path, _, fragment = unquote(href.replace("&amp;", "&")).partition("#")
                path = path.partition("?")[0]
                target = (page.parent / path).resolve()
                with self.subTest(page=page, href=href):
                    self.assertTrue(target.is_file())
                    if fragment:
                        self.assertIn(f'id="{fragment}"', target.read_text())

    def test_present(self) -> None:
        self.make(cover=True, link=LINK, revision=f"{SHA}-dirty", lists=LISTS)
        html = self.page("linux", "v1-1-gabc")
        subject = "[PATCH RFC 0/2] Support for Multiple Atomicity Mode"
        self.assertIn(f"<h1>{subject}</h1>", html)
        self.assertIn("<dd>Daniel Gomez &lt;da.gomez@kernel.org&gt;</dd>", html)
        self.assertIn("<dd>Tue, 29 Sep 2026 13:17:10 +0200</dd>", html)
        self.assertIn(f'<a href="https://lore.kernel.org/all/{THREAD}/"', html)
        lists = (
            '<span class="tag" title="linux-kernel@vger.kernel.org">LKML</span>'
            '<span class="tag" title="linux-block@vger.kernel.org">linux-block</span>'
            '<span class="tag" title="linux-nvme@lists.infradead.org">linux-nvme'
            "</span>"
        )
        self.assertIn(f"<dt>Mailing lists</dt><dd>{lists}</dd>", html)
        self.assertIn(f'<td>1/1</td><td><a href="{LINK}">nvme: x</a></td>', html)
        self.assertIn(f'commit/{SHA}" class="mono">{SHA[:12]}-dirty</a>', html)
        index = self.page()
        self.assertIn(
            f'<a href="./linux/v1-1-gabc/index.html" class="row-link">{subject}</a>'
            f'<div class="lists">{lists}</div></td>',
            index,
        )
        self.assertIn(
            '<td>Daniel Gomez<div class="text-muted-sm">da.gomez@kernel.org</div></td>'
            '<td>2026-09-29<div class="text-muted-sm">13:17 +0200</div></td>'
            '<td class="num">1</td>',
            index,
        )

    def test_lists_of_series_json(self) -> None:
        self.make(cover=True, link=None, revision=None, lists=LISTS[:1])
        tag = '<span class="tag" title="linux-block@vger.kernel.org">linux-block</span>'
        self.assertIn(
            f"<dt>Mailing lists</dt><dd>{tag}</dd>", self.page("linux", "v1-1-gabc")
        )
        index = self.page()
        self.assertIn(f'<div class="lists">{tag}</div>', index)
        self.assertIn('<option value="linux-block@vger.kernel.org"', index)
        for address in LISTS[1:]:
            self.assertNotIn(f'"{address}"', index)
        search = (self.output / "search.html").read_text(encoding="utf-8")
        self.assertIn(' data-lists="linux-block@vger.kernel.org" ', search)
        self.assertNotIn("lkml", search)

    def test_kept_in_a_commit(self) -> None:
        kept = {
            "subject": "Support <MAM> & more",
            "author": "D <d@x>",
            "date": "2026-09-29T10:19:18Z",
            "change_id": "20260929-nvme-mam-073f2ed13bdc",
            "revision": 1,
            "text": "NVMe has <2> modes & more.\n\nTo: A <a@x>",
        }
        self.make(cover=False, link=None, revision=None, cover_kept=kept)
        html = self.page("linux", "v1-1-gabc")
        self.assertIn("<h1>Support &lt;MAM&gt; &amp; more</h1>", html)
        self.assertIn("<dt>Author</dt><dd>D &lt;d@x&gt;</dd>", html)
        self.assertIn("<dt>Date</dt><dd>Tue, 29 Sep 2026 10:19:18 +0000</dd>", html)
        change = '<span class="mono">20260929-nvme-mam-073f2ed13bdc</span>'
        self.assertIn(f"<dt>Change-id</dt><dd>{change}</dd>", html)
        self.assertNotIn("<dt>Thread</dt>", html)
        self.assertNotIn("<dt>Cover letter</dt>", html)
        self.assertIn(
            '<section id="cover"><h2>Cover letter</h2><details open>'
            "<summary>3 lines</summary>"
            "<pre>NVMe has &lt;2&gt; modes &amp; more.\n\nTo: A &lt;a@x&gt;</pre>",
            html,
        )
        self.assertIn("<td>1/1</td><td>nvme: x</td>", html)
        build = self.page("linux", "v1-1-gabc", *B1.split("/"))
        self.assertIn("<h1>Support &lt;MAM&gt; &amp; more</h1>", build)
        self.assertNotIn('id="cover"', build)
        self.assertIn(">Support &lt;MAM&gt; &amp; more</a>", self.page())
        self.assertNotIn("no cover letter", self.page())

    def test_kept_in_a_commit_and_long(self) -> None:
        kept = {"subject": "s", "author": "D <d@x>", "date": "2026-09-29T10:19:18Z"}
        kept |= {"change_id": "", "revision": 1, "text": "line\n" * 51}
        self.make(cover=False, link=None, revision=None, cover_kept=kept)
        html = self.page("linux", "v1-1-gabc")
        self.assertIn("<details><summary>51 lines</summary><pre>line\n", html)
        self.assertNotIn("<dt>Change-id</dt>", html)

    def test_absent(self) -> None:
        self.make(cover=False, link=None, revision=None)
        html = self.page("linux", "v1-1-gabc")
        self.assertIn("<h1>nvme: x</h1>", html)
        self.assertIn("<dd>A &lt;a@x&gt;</dd>", html)
        self.assertIn('<dt>Cover letter</dt><dd><span class="muted">not recorded', html)
        self.assertIn("<td>1/1</td><td>nvme: x</td>", html)
        producer = (
            '<span class="mono">kdevops-ng</span> <span class="mono">30335b9</span>'
        )
        self.assertIn(f"<dt>Producer</dt><dd>{producer}</dd>", html)
        index = self.page()
        self.assertIn('<span class="text-muted-sm">no cover letter</span>', index)
        self.assertIn(
            '<td>A<div class="text-muted-sm">a@x</div></td>'
            '<td><span class="muted">not recorded</span></td>',
            index,
        )
        self.assertIn(f"<td>{producer}</td></tr>", self.root("bases.html"))

    def test_no_inline_style(self) -> None:
        self.make(cover=True, link=LINK, revision=SHA)
        for page in sorted(self.output.rglob("*.html")):
            text = page.read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertNotIn(" style=", text)
                self.assertNotIn("<style", text)
                self.assertIn("style-src 'self'", text)
                self.assertNotIn("<script>", text)

    def test_search(self) -> None:
        self.make(cover=True, link=LINK, revision=SHA, lists=LISTS)
        index = self.root("search.html")
        self.assertIn('<script src="./search.js" defer></script>', index)
        self.assertIn('<input type="search" id="search" name="q"', index)
        self.assertNotIn("search.js", self.page())
        self.assertNotIn("search.js", self.root("bases.html"))
        start = index.index('<tr data-search="') + len('<tr data-search="')
        found = index[start : index.index('"', start)]
        for word in (
            "v1-1-gabc",
            THREAD,
            "linux-nvme@lists.infradead.org",
            "lkml",
            "b" * 40,
            "nvme: x",
            B2,
        ):
            self.assertIn(word, found)
        self.assertEqual(found, found.lower())
        self.assertIn(f'<tr data-search="v1 {B1} ', index)
        self.assertEqual((self.output / "search.js").read_text(), render.SEARCH)
        self.assertNotIn("search.js", self.page("linux", "v1-1-gabc"))


class Layout(unittest.TestCase):
    """Builds nest under a commit; a Message-ID's directory is no commit."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.records = self.dir / "records"

    def test_builds(self) -> None:
        commit_dir = self.records / "linux" / "v7.3-2-g1da4e6e1d71d"
        for build in (
            "tinyconfig/x86_64/clang",
            "block-layer/nvm-express-driver/x86_64/clang",
            "fstests/arm64/gcc",
            "x86_64/clang/x86_64/gcc",
        ):
            put(commit_dir.joinpath(build, *MAKE_ALL), record())
        put(commit_dir / "notes" / "x.json", record())
        put(commit_dir / "x86_64" / "clang" / "make" / "all.json", record())
        put(self.records / "linux" / "m@x" / "x86_64" / "clang" / "b4.json", record())
        self.assertEqual(
            render.builds_of(commit_dir),
            [
                ("block-layer", "nvm-express-driver", "x86_64", "clang"),
                ("fstests", "arm64", "gcc"),
                ("tinyconfig", "x86_64", "clang"),
                ("x86_64", "clang", "x86_64", "gcc"),
            ],
        )
        commit_tree, *trees = render.load(self.records, {})
        self.assertEqual(commit_tree.build, render.NO_BUILD)
        self.assertEqual(commit_tree.parts[2:], (render.NO_BUILD_DIR,))
        self.assertEqual(
            list(commit_tree.records), ["notes/x", "x86_64/clang/make/all"]
        )
        self.assertEqual(
            [tree.build for tree in trees],
            [
                "block-layer/nvm-express-driver/x86_64/clang",
                "fstests/arm64/gcc",
                "tinyconfig/x86_64/clang",
                "x86_64/clang/x86_64/gcc",
            ],
        )
        self.assertEqual(
            trees[0].parts,
            (
                "linux",
                "v7.3-2-g1da4e6e1d71d",
                "block-layer",
                "nvm-express-driver",
                "x86_64",
                "clang",
            ),
        )
        self.assertEqual(list(trees[0].records), ["make/all"])
        output = self.dir / "public"
        render.make_site(self.records, output)
        page = output.joinpath(*trees[0].parts, "index.html").read_text()
        self.assertIn(
            '<p><a href="../../../../../../linux/v7.3-2-g1da4e6e1d71d/index.html">',
            page,
        )

    def test_record_keys(self) -> None:
        commit_dir = self.records.joinpath("linux", "v1")
        build = commit_dir / B
        for key in (
            "make/all",
            "sparse/init/main.o",
            "fstests/xfs_bs4k_ss512/generic/001",
        ):
            put(build / f"{key}.json", record(build=BUILT))
        # What reads no build lies under the commit, beside its series.json.
        for key in ("make/htmldocs/filesystems", "checkpatch.pl"):
            put(commit_dir / f"{key}.json", record())
        put(commit_dir / "series.json", series_json(base=TIP))
        objects = {"schema": 1, "commit": TIP, "build": BUILT}
        put(build / "objects.json", {**objects, "objects": [], "unbuilt": []})
        put(build / "config", "CONFIG_64BIT=y\n")
        commit_tree, tree = render.load(self.records, {})
        self.assertEqual(
            list(commit_tree.records), ["checkpatch.pl", "make/htmldocs/filesystems"]
        )
        self.assertIsNone(commit_tree.config)
        self.assertEqual(
            list(tree.records),
            [
                "fstests/xfs_bs4k_ss512/generic/001",
                "make/all",
                "sparse/init/main.o",
            ],
        )
        self.assertEqual(tree.config, b"CONFIG_64BIT=y\n")
        self.assertEqual(render.suites(tree.records), ["make", "fstests", "sparse"])
        self.assertEqual(render.suites(commit_tree.records), ["make", "checkpatch.pl"])

    def test_history(self) -> None:
        for path, key in (
            ("linux/v1/b/x86_64/clang/make/all.json", ("linux", "v1", *B.split("/"))),
            ("linux/m@x/b4/shazam.json", ("linux", "m@x")),
            ("linux/v1/series.json", None),
            ("linux/v1/b/x86_64/clang", None),
            ("linux/v1/checkpatch.pl.json", ("linux", "v1")),
            ("linux/v1/make/htmldocs/block.json", ("linux", "v1")),
            ("linux/v1/b/x86/gcc/config", ("linux", "v1", "b", "x86", "gcc")),
            ("linux/v1/notes", None),
        ):
            with self.subTest(path=path):
                self.assertEqual(render.tree_of(path.split("/")), key)


def cover_letter(subject: str, date: str | None) -> str:
    dated = f"Date: {date}\n" if date is not None else ""
    return f"Subject: {subject}\nFrom: A <a@x>\n{dated}"


def add_series(records: Path, name: str, cover: str | None, *lists: str) -> None:
    """A series name on v1 to lists, built as B, from <name>@x when it has a cover."""
    message_id = f"{name}@x" if cover is not None else ""
    put(records.joinpath("linux", name, B, *MAKE_ALL), record())
    put(
        records / "linux" / name / "series.json",
        series_json(commit(name), message_id=message_id, lists=list(lists)),
    )
    if cover is not None:
        put(records / "linux" / message_id / "b4" / "mbox.mbx", cover)


def base_build(records: Path) -> None:
    """The base v1, built as B."""
    put(records.joinpath("linux", "v1", B, *MAKE_ALL), record())


class Index(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.records = self.dir / "records"
        self.output = self.dir / "public"
        base_build(self.records)

    def index(self, name: str = "index.html") -> str:
        render.make_site(self.records, self.output)
        return uncovered((self.output / name).read_text(encoding="utf-8"))

    def test_newest_posted_first(self) -> None:
        add_series(
            self.records, "old", cover_letter("old", "Mon, 2 Mar 2026 10:00:00 +0000")
        )
        add_series(
            self.records, "new", cover_letter("new", "Tue, 3 Mar 2026 01:00:00 +0200")
        )
        add_series(
            self.records, "zone", cover_letter("zone", "Mon, 2 Mar 2026 12:00:00 -0000")
        )
        add_series(self.records, "bad", cover_letter("bad", "yesterday"))
        add_series(self.records, "none", cover_letter("none", None))
        add_series(self.records, "a-bare", None)
        unapplied(self.records, "m@x", "b4/shazam", record("fail"))
        put(
            self.records / "linux" / "m@x" / "b4" / "mbox.mbx",
            cover_letter("b4", "Mon, 2 Mar 2026 11:00:00 +0000"),
        )
        unapplied(self.records, "f@x", "b4/shazam", record("fail"))
        found = re.findall(
            r'<td><a href="\./linux/([^"]+)/index\.html" class="row-link">',
            self.index(),
        )
        self.assertEqual(
            found,
            ["new", "zone", "m%40x", "old", "a-bare", "bad", "f%40x", "none"],
        )

    def test_undated_by_recorded(self) -> None:
        earlier = datetime(2026, 1, 1, tzinfo=UTC)
        later = datetime(2026, 1, 2, tzinfo=UTC)
        cover = render.parse_cover(b"Date: Thu, 1 Jan 2026 00:00:00 +0000\n")
        orders = {
            "dated": render.row_order(cover, None, ("dated",)),
            "a": render.row_order(None, None, ("a",)),
            "b": render.row_order(None, earlier, ("b",)),
            "c": render.row_order(render.Unreadable("p", "r"), later, ("c",)),
            "d": render.row_order(None, later, ("d",)),
        }
        self.assertEqual(
            sorted(orders, key=orders.__getitem__), ["dated", "c", "d", "b", "a"]
        )

    def test_one_page(self) -> None:
        add_series(self.records, "s", None)
        index = self.index()
        self.assertNotIn('id="pager"', index)
        self.assertNotIn('id="bases"', index)
        self.assertFalse((self.output / "index-2.html").exists())

    def test_pages(self) -> None:
        for day in range(render.PER_PAGE + 1):
            date = f"1 Jan 2026 00:{day:02}:00 +0000"
            add_series(self.records, f"s{day:02}", cover_letter(f"s{day:02}", date))
        first, second = self.index(), self.index("index-2.html")
        self.assertFalse((self.output / "index-3.html").exists())
        names = [f"s{n:02}" for n in range(render.PER_PAGE, -1, -1)]
        for page, shown in ((first, names[:-1]), (second, names[-1:])):
            self.assertNotIn("hidden", page.split('id="stats"')[0])
            self.assertEqual(re.findall(r'<tr data-search="(s\d\d) ', page), shown)
        self.assertIn(
            '<nav class="pagination" id="pager" aria-label="Pages">'
            '<span class="button disabled">First</span>'
            '<span class="button disabled">Prev</span>'
            "<span>Page 1 of 2 (51 total)</span>"
            '<a class="button" href="./index-2.html">Next</a>'
            '<a class="button" href="./index-2.html">Last</a></nav>',
            first,
        )
        self.assertIn(
            '<a class="button" href="./index.html">First</a>'
            '<a class="button" href="./index.html">Prev</a>'
            "<span>Page 2 of 2 (51 total)</span>"
            '<span class="button disabled">Next</span>'
            '<span class="button disabled">Last</span>',
            second,
        )
        for part in ("<title>", "<footer>", "<header>", '<form class="controls"'):
            self.assertEqual(
                *(re.findall(f"{part}.*", page) for page in (first, second))
            )
        search = self.index("search.html")
        self.assertEqual(re.findall(r'<tr data-search="(s\d\d) ', search), names)

    def test_bases_and_search(self) -> None:
        add_series(self.records, "s", None)
        unapplied(self.records, "f@x", "b4/shazam", record("fail"))
        bases = self.index("bases.html")
        self.assertIn('<section id="bases"><h2>Bases</h2>', bases)
        self.assertNotIn('id="series"', bases)
        self.assertEqual(re.findall(r'<tr data-search="([^ "]*)', bases), ["v1"])
        search = self.index("search.html")
        self.assertEqual(
            re.findall(r'<tr data-search="([^ "]*)', search), ["f@x", "s", "v1"]
        )
        self.assertLess(search.index('id="series"'), search.index('id="bases"'))
        self.assertNotIn("<tr hidden", search)
        self.assertNotIn('id="pager"', search)

    def test_controls(self) -> None:
        for name, active, clear in (
            ("index.html", "./index.html", "./index.html"),
            ("bases.html", "./bases.html", "./bases.html"),
            ("search.html", None, "./index.html"),
        ):
            with self.subTest(page=name):
                page = self.index(name)
                self.assertIn(
                    '<form class="controls" action="./search.html" method="get"'
                    ' role="search"><select id="project" class="button"'
                    ' aria-label="Project" autocomplete="off">'
                    f'<option value="./{name}" selected>Linux kernel</option></select>'
                    '<select id="list" name="list" class="button"'
                    ' aria-label="Mailing list"><option value="">All lists</option>',
                    page,
                )
                self.assertIn(
                    '<button type="submit" class="button">Search</button>'
                    f'<a class="button" href="{clear}">Clear</a>',
                    page,
                )
                found = re.findall(
                    r'<a class="button active" aria-current="page" href="([^"]+)"',
                    page,
                )
                self.assertEqual(found, [active] if active else [])
                self.assertIn('href="./index.html">Series</a>', page)
                self.assertIn('href="./bases.html">Bases</a>', page)

    def test_names_reserved(self) -> None:
        for name in sorted(render.RESERVED | {"index-2.html"}):
            with self.subTest(name=name):
                put(self.records.joinpath(name, "v1", B, *MAKE_ALL), record())
                with self.assertRaises(render.RenderError):
                    self.index()
                shutil.rmtree(self.records / name)
        for name in ("index.html", render.REPORT):
            with self.subTest(build=name):
                build = (name, "x86_64", "clang", *MAKE_ALL)
                put(self.records.joinpath("linux", "v1", *build), record())
                with self.assertRaisesRegex(render.RenderError, name):
                    self.index()
                shutil.rmtree(self.records / "linux" / "v1" / name)

    def test_layout_shared(self) -> None:
        add_series(self.records, "s", cover_letter("s" * 200, None))
        render.make_site(self.records, self.output)
        table = re.compile(r'<table class="[^"]*">.*?</thead>', re.DOTALL)
        series, bases = (
            render.fixed_table(render.SERIES_COLUMNS, ""),
            render.bases_section([], dated=False),
        )
        heads = {
            name: table.findall(self.index(name))
            for name in ("index.html", "bases.html", "search.html")
        }
        self.assertEqual(heads["index.html"], table.findall(series))
        self.assertEqual(heads["bases.html"], table.findall(bases))
        self.assertEqual(heads["search.html"], table.findall(series + bases))
        self.assertIn(
            '<colgroup><col class="subject"><col class="author"><col class="date">'
            '<col class="parts"><col class="base"><col class="findings"></colgroup>',
            series,
        )
        for name, _ in render.SERIES_COLUMNS:
            self.assertIn(f"col.{name}", render.STYLE)
        self.assertIn('<table class="rows bases">', bases)
        self.assertNotIn("colgroup", bases)
        self.assertIn("table.fixed { table-layout: fixed;", render.STYLE)
        self.assertIn("table.fixed td { overflow-wrap: anywhere; }", render.STYLE)

    def test_status_bar(self) -> None:
        add_series(self.records, "s", None)
        unapplied(self.records, "f@x", "b4/shazam", record("fail"))
        render.make_site(self.records, self.output, revision="abc1234-dirty")
        bar = (
            '<div class="stats-container" id="stats"><div class="stats-group">'
            '<span class="stats-label">kresults</span>'
            ' <span class="mono">abc1234-dirty</span><span class="stats-sep"></span>'
            '<a class="stats-link page" href="./about.html">About</a>'
            '<button type="button" class="stats-link" popovertarget="about" hidden>'
            "About</button>"
            '<span class="stats-sep"></span>'
            '<a class="stats-link page" href="./settings.html">Settings</a>'
            '<button type="button" class="stats-link" popovertarget="settings"'
            " hidden>Settings</button>"
            '<span class="stats-sep"></span>'
            '<a class="stats-link" href="./stats.html">Stats</a></div>'
            '<div class="stats-sep"></div><div class="stats-group">'
            '<span class="stats-val">2</span> Series'
            ' <span class="stats-sub">(3 records)</span></div>'
            '<div class="stats-sep"></div><div class="stats-group">'
            '<span class="stats-val">1</span> Bases</div></div>'
        )
        for name in (
            "index.html",
            "bases.html",
            "search.html",
            "stats.html",
        ):
            with self.subTest(page=name):
                page = (self.output / name).read_text(encoding="utf-8")
                self.assertEqual(page.count(bar), 1)
                self.assertLess(page.index('id="stats"'), page.index("<footer>"))
                self.assertEqual(page.count('<div id="about" class="modal'), 1)
                self.assertEqual(page.count('<div id="settings" class="modal'), 1)
        for name, target in (("about.html", "about"), ("settings.html", "settings")):
            with self.subTest(page=name):
                page = (self.output / name).read_text(encoding="utf-8")
                self.assertEqual(page.count('id="stats"'), 1)
                self.assertNotIn(f'popovertarget="{target}"', page)
                self.assertNotIn(f'<div id="{target}"', page)
                self.assertIn(
                    f'<a class="stats-link" href="./{name}">{target.title()}</a>', page
                )
        overview = (self.output / "linux" / "s" / "index.html").read_text()
        self.assertNotIn('id="stats"', overview)
        render.make_site(self.records, self.output)
        self.assertIn(
            '<span class="stats-label">kresults</span><span class="stats-sep">',
            self.index(),
        )
        self.assertNotIn("status-", render.STYLE.replace("--status-", ""))

    def test_revision_option(self) -> None:
        add_series(self.records, "s", None)
        script = Path(__file__).resolve().parents[1] / "format" / "render.py"
        # The interpreter running the tests and this file; no shell runs.
        subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-E",
                str(script),
                "--records",
                str(self.records),
                "--output",
                str(self.output),
                "--revision",
                "<r>",
            ],
            check=True,
            capture_output=True,
        )
        page = (self.output / "index.html").read_text(encoding="utf-8")
        self.assertIn('<span class="mono">&lt;r&gt;</span>', page)

    def test_scripts(self) -> None:
        add_series(self.records, "s", None)
        self.index()
        self.assertEqual((self.output / "theme.js").read_text(), render.THEME)
        self.assertEqual((self.output / "settings.js").read_text(), render.SETTINGS)
        for page in sorted(self.output.rglob("*.html")):
            text = page.read_text(encoding="utf-8")
            with self.subTest(page=page):
                found = re.findall(r'<script src="([^"]+)"', text)
                self.assertEqual(
                    (page.parent / found[0]).resolve(),
                    (self.output / "theme.js").resolve(),
                )
                self.assertLess(text.index("theme.js"), text.index("</head>"))
        for name in ("index.html", "settings.html", "stats.html", "search.html"):
            with self.subTest(page=name):
                page = self.index(name)
                self.assertIn('<script src="./settings.js" defer></script>', page)
                # One copy of the controls a page: its own, else the popover's.
                for found in (
                    '<p class="muted" id="settings-nojs">Settings need JavaScript',
                    '<form class="settings" id="settings-form" hidden>',
                    '<select id="theme" class="button">',
                    '<input type="checkbox" id="colorblind">',
                ):
                    self.assertEqual(page.count(found), 1)
                for value in ("auto", "light", "dark"):
                    self.assertIn(f'<option value="{value}">', page)
                ids = re.findall(r' id="([^"]+)"', page)
                self.assertEqual(len(ids), len(set(ids)))
        overview = self.index("linux/s/index.html")
        self.assertNotIn("settings.js", overview)
        self.assertNotIn("popover", overview)
        self.assertIn("if (theme && colorblind) {", render.SETTINGS)
        self.assertIn(":root[data-theme=dark] {", render.STYLE)
        self.assertIn(":root:not([data-theme=light])", render.STYLE)
        self.assertIn(":root[data-colorblind][data-theme=dark] {", render.STYLE)

    def test_about(self) -> None:
        about = self.index("about.html")
        self.assertIn("<h1>About</h1>", about)
        self.assertIn('href="https://github.com/linux-kdevops/kresults"', about)
        self.assertIn("<title>About kresults</title>", about)
        self.assertIn('<a href="./index.html">\u2190 Back</a>', about)
        github = (
            '<p class="github"><a href="https://github.com/linux-kdevops/kresults">'
            '<svg viewBox="0 0 16 16" aria-hidden="true">'
        )
        self.assertEqual(about.count(github), 1)
        self.assertIn("</svg>View project on GitHub</a></p>", about)
        index = self.index()
        popover = index.split('<div id="about" class="modal stack" popover>')[1]
        self.assertTrue(
            popover.startswith(
                '<button type="button" class="modal-close" popovertarget="about"'
                ' popovertargetaction="hide" aria-label="Close">\u00d7</button>'
                '<h2 class="modal-title">About</h2>'
            )
        )
        self.assertIn(render.about_html(), popover)
        self.assertIn(render.about_html(), about)
        self.assertIn(render.settings_html(), self.index("settings.html"))
        self.assertIn(render.settings_html(), index)
        for token in (
            ".modal { display: none; }",
            ".modal:popover-open { display: block;",
            ".modal::backdrop {",
            "@supports selector(:popover-open) {",
        ):
            self.assertIn(token, render.STYLE)
        self.assertNotIn(" style=", index)
        self.assertNotIn(" onclick=", index)

    def test_stats(self) -> None:
        put(self.records.joinpath("linux", "v1", B, "sparse", "x.json"), record())
        dates = ("Mon, 2 Mar 2026 10:00:00 +0000", "Fri, 13 Mar 2026 23:30:00 -0200")
        for name, date in (("s1", dates[0]), ("s2", dates[1]), ("s3", None)):
            add_series(self.records, name, cover_letter(name, date))
        for name, data in (("s1", record("fail")), ("s2", record()), ("s3", "{")):
            put(self.records.joinpath("linux", name, B, "sparse", "x.json"), data)
        unapplied(self.records, "m@x", "b4/shazam", record("fail"))
        put(
            self.records / "linux" / "m@x" / "b4" / "mbox.mbx",
            cover_letter("b4", "Tue, 3 Mar 2026 11:00:00 +0000"),
        )
        stats = self.index("stats.html")
        cards = dict(
            re.findall(
                r'<div class="stat-card"><h3>([^<]+)</h3><div class="value">(\d+)<',
                stats,
            )
        )
        self.assertEqual(
            cards,
            {
                "Series": "4",
                "Applied": "3",
                "Could not apply": "1",
                "Bases": "1",
                "Builds": "4",
                "Records": "9",
            },
        )
        num = '<td class="num">'

        def row(*cells: str) -> str:
            return (
                f"<tr><td>{cells[0]}</td>"
                + "".join(f"{num}{cell}</td>" for cell in cells[1:])
                + "</tr>"
            )

        self.assertIn(row("Series", "4", "1", "0", "0", "1", "6"), stats)
        self.assertIn(row("Bases", "2", "0", "0", "0", "0", "2"), stats)
        self.assertIn(row("Could not apply", "0", "1", "0", "0", "0", "1"), stats)
        self.assertIn(row("make", "3", "0", "3"), stats)
        self.assertIn(row("sparse", "3", "1", "2"), stats)
        weekly = stats.split("<h2>Series per week</h2>")[1].split("</section>")[0]
        self.assertIn(row("2026-W10", "1", "0", "1", "2"), weekly)
        self.assertIn(row("2026-W11", "0", "1", "0", "1"), weekly)
        self.assertIn("1 series without a dated cover letter left out.", weekly)
        self.assertIn(
            '<a class="mark" href="./search.html?week=2026-W10&amp;outcome=introduced">'
            '<g class="o-introduced">',
            weekly,
        )
        checks = stats.split("<h2>Findings introduced, by tool</h2>")[1]
        checks = checks.split("</section>")[0]
        self.assertIn(row("sparse", "1", "3"), checks)
        self.assertIn(row("make", "0", "3"), checks)
        self.assertIn('<a class="mark" href="./search.html?check=sparse">', checks)
        self.assertIn("No checked series has a mailing list.", stats)
        times = stats.split("<h2>Kernel build time</h2>")[1].split("</section>")[0]
        self.assertEqual(times.count('<a class="mark"'), 2)
        self.assertIn("2 builds without a date left out.", times)
        marks = re.findall(r'<a class="mark"[^>]*>.*?</a>', stats)
        self.assertEqual(len(marks), 3 + 1 + 2 + 2)
        for found in marks:
            self.assertRegex(
                found, r'^<a class="mark" href="[^"]+">.*<title>[^<]+</title></a>$'
            )
        self.assertIn('<script src="./stats.js" defer></script>', stats)
        search = self.index("search.html")
        self.assertIn(
            ' data-week="2026-W10" data-outcome="introduced" data-checks="sparse">',
            search,
        )
        self.assertIn(' data-week="" data-outcome="clean" data-checks="">', search)
        self.assertIn(' data-week="2026-W10" data-outcome="unapplied"', search)
        render.make_site(self.records, self.dir / "again")
        self.assertEqual(stats, (self.dir / "again" / "stats.html").read_text())

    def test_charts(self) -> None:
        self.assertEqual(render.nice_ticks(7), [0, 2, 4, 6, 8])
        self.assertEqual(render.nice_ticks(1), [0, 1])
        self.assertEqual(render.log_ticks(0.1, 172.9), [0.1, 1.0, 10.0, 100.0, 1000.0])
        for build, family in (
            ("allmodconfig/x86_64/clang", "allmodconfig"),
            ("tinyconfig/x86_64/clang", "other"),
            ("x86_64_defconfig/x86_64/gcc", "other"),
            ("block-layer/nvm-express-driver/x86_64/clang", "profile"),
            ("kconfig/x86_64/clang", "profile"),
            ("allmodconfig/block-layer/x86_64/clang", "profile"),
        ):
            with self.subTest(build=build):
                self.assertEqual(render.family_of(build)[0], family)
        for token in (
            "--series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a;",
            "--series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;",
            ".s1 { fill: var(--series-1); }",
            ".o-introduced { fill: var(--status-failed); }",
            "svg.chart .marks:hover a.mark:not(:hover) { opacity: .35; }",
        ):
            self.assertIn(token, render.STYLE)
        for name in ("week", "outcome", "check", "list"):
            self.assertIn(
                f"{name}: ", render.SEARCH.split("const FILTERS")[1].split("\n")[0]
            )
        self.assertIn("stats.js", render.RESERVED)
        self.assertIn("title.remove();", render.STATS)


HOST = {
    "arch": "x86_64",
    "cpu": "AMD Ryzen 7 PRO 8700GE w/ Radeon 780M Graphics",
    "cpus": 16,
    "memory": 66496024576,
}
RYZEN = "AMD Ryzen 7 PRO 8700GE w/ Radeon 780M Graphics, 16 CPUs, 62 GiB"


class Machines(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.records = self.dir / "records"
        self.output = self.dir / "public"

    def series(self, name: str, day: int, host: Mapping[str, object]) -> None:
        add_series(
            self.records, name, cover_letter(name, f"{day} Mar 2026 10:00:00 +0000")
        )
        build = self.records.joinpath("linux", name, B)
        for file, seconds in (("make/all.json", 600 * day), ("sparse/x.json", 1)):
            put(build / file, record(seconds=seconds, machine=host))

    def chart(self) -> str:
        render.make_site(self.records, self.output)
        stats = (self.output / "stats.html").read_text(encoding="utf-8")
        return stats.split("<h2>Kernel build time by machine</h2>")[1].split(
            "</section>"
        )[0]

    def test_label(self) -> None:
        host = render.parse_record(record(machine=HOST)).host
        self.assertEqual(host.label if host else None, RYZEN)
        self.assertEqual(
            render.Host("aarch64", "c", 2, 3 * 2**30 + 2**29 - 1).label,
            "c, 2 CPUs, 3 GiB",
        )
        for bad in (
            "x",
            {**HOST, "cpus": "16"},
            {**HOST, "memory": True},
            {**HOST, "cpu": 1},
        ):
            with self.subTest(host=bad), self.assertRaises(render.RecordError):
                render.parse_record(record(machine=bad))

    def test_by_machine(self) -> None:
        small = {**HOST, "cpu": "Small", "cpus": 4, "memory": 8 * 2**30}
        self.series("s1", 2, HOST)
        self.series("s2", 3, HOST)
        self.series("s3", 4, small)
        chart = self.chart()
        self.assertIn(
            '<ul class="legend">'
            f'<li><svg viewBox="0 0 10 10" aria-hidden="true"><g class="s1">'
            f'<circle cx="5" cy="5" r="4"/></g></svg>{RYZEN}</li>'
            '<li><svg viewBox="0 0 10 10" aria-hidden="true"><g class="s2">'
            '<circle cx="5" cy="5" r="4"/></g></svg>Small, 4 CPUs, 8 GiB</li></ul>',
            chart,
        )
        marks = re.findall(r'<a class="mark" href="([^"]+)"><g class="([^"]+)">', chart)
        self.assertEqual(
            marks,
            [
                (f"./linux/s1/{B}/index.html", "s1"),
                (f"./linux/s2/{B}/index.html", "s1"),
                (f"./linux/s3/{B}/index.html", "s2"),
            ],
        )
        self.assertIn(f"<title>s1 {B} ({RYZEN}): 20.0 minutes,", chart)
        page = self.output.joinpath("linux", "s1", B, "index.html").read_text()
        self.assertIn(f"<dt>Machine</dt><dd>{RYZEN}</dd>", page)
        self.assertIn(f'<span class="muted" title="on {RYZEN}">1200 s</span>', page)
        render.make_site(self.records, self.dir / "again")
        for name in ("stats.html", f"linux/s1/{B}/index.html"):
            with self.subTest(page=name):
                self.assertEqual(
                    (self.output / name).read_bytes(),
                    (self.dir / "again" / name).read_bytes(),
                )

    def test_other_machines(self) -> None:
        for n in range(4):
            host = {"arch": "x86_64", "cpu": f"C{n}", "cpus": 2, "memory": 2**30}
            for day in range(4 - n):
                self.series(f"s{n}{day}", 2 + day, host)
        chart = self.chart()
        legend = re.findall(
            r'<g class="([^"]+)"><circle cx="5"[^>]*/></g></svg>([^<]+)</li>', chart
        )
        self.assertEqual(
            legend,
            [
                ("s1", "C0, 2 CPUs, 1 GiB"),
                ("s2", "C1, 2 CPUs, 1 GiB"),
                ("s3", "Other (2 machine types)"),
            ],
        )
        self.assertIn(f"<title>s30 {B} (C3, 2 CPUs, 1 GiB): ", chart)


class BaseLinks(unittest.TestCase):
    sha = "d68ce4e71248" + "0" * 28

    def series(self, name: str, trees: object = None) -> render.Series:
        given: object = [] if trees is None else trees
        fields = series_json(base=self.sha, base_name=name, base_trees=given)
        return render.parse_series(fields)

    def link(self, series: render.Series, project: str = "linux") -> str:
        return render.base_commit_html(project, series)

    def test_base_trees(self) -> None:
        torvalds = "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git"
        trees = [
            {"remote": "torvalds", "url": torvalds},
            {"remote": "mirror", "url": "https://github.com/torvalds/linux.git"},
        ]
        self.assertEqual(
            self.link(self.series("v7.4-rc1-261-gd68ce4e71248", trees)),
            f'<a href="{torvalds}/commit/?id={self.sha}" class="mono"'
            ' title="torvalds">d68ce4e71248</a>',
        )
        github = [{"remote": "gh", "url": "https://github.com/torvalds/linux.git"}]
        self.assertEqual(
            self.link(self.series("x", github)),
            f'<a href="https://github.com/torvalds/linux/commit/{self.sha}"'
            ' class="mono" title="gh">d68ce4e71248</a>',
        )
        bad = [{"remote": "local", "url": "file:///srv/linux.git"}]
        self.assertEqual(
            self.link(self.series("v7.4", bad)),
            '<span class="mono muted">d68ce4e71248</span>',
        )
        with self.assertRaises(render.RecordError):
            self.series("v7.4", [{"remote": "x"}])
        with self.assertRaises(render.RecordError):
            self.series("v7.4", "torvalds")

    def test_fallbacks(self) -> None:
        org = "https://git.kernel.org/pub/scm/linux/kernel/git/"
        for name, remote, tree in (
            ("v7.4", "torvalds", f"{org}torvalds/linux.git"),
            ("v7.4-rc1", "torvalds", f"{org}torvalds/linux.git"),
            ("v7.3.2", "stable", f"{org}stable/linux.git"),
            ("next-20261105", "linux-next", f"{org}next/linux-next.git"),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    self.link(self.series(name)),
                    f'<a href="{tree}/commit/?id={self.sha}" class="mono"'
                    f' title="{remote}">d68ce4e71248</a>',
                )
        self.assertEqual(
            self.link(self.series("v7.4-rc1-261-gd68ce4e71248")),
            f'<a href="https://github.com/linux-kdevops/linux/commit/{self.sha}"'
            ' class="mono" title="linux-kdevops">d68ce4e71248</a>',
        )
        self.assertEqual(
            self.link(self.series("v7.4-rc1-261-gd68ce4e71248"), "other"),
            '<span class="mono muted">d68ce4e71248</span>',
        )

    def test_qemu(self) -> None:
        for name in ("v11.1.0", "v11.1.0-3011-gd68ce4e71248"):
            with self.subTest(name=name):
                self.assertEqual(
                    self.link(self.series(name), "qemu"),
                    f'<a href="https://gitlab.com/qemu-project/qemu/-/commit/{self.sha}"'
                    ' class="mono" title="qemu-project">d68ce4e71248</a>',
                )
        qemu = dataclasses.replace(linux("v11.1.0"), project="qemu")
        cited = render.cites(qemu, None)
        self.assertEqual(
            render.pictured("../hw/nvme/ctrl.c:12:3: warning: x", cited),
            '<a href="https://gitlab.com/qemu-project/qemu/-/blob/v11.1.0/hw/nvme/'
            'ctrl.c#L12">../hw/nvme/ctrl.c:12</a>:3: warning: x',
        )


def letter(subject: str, date: str, author: str = "A <a@x>", **extra: str) -> str:
    lines = [f"Subject: {subject}", f"From: {author}", f"Date: {date}"]
    lines += [f"{name}: {value}" for name, value in extra.items()]
    body = f"\nchange-id: {extra['change']}\n" if "change" in extra else "\n"
    head = "\n".join(line for line in lines if not line.startswith("change:"))
    return f"From x Mon Sep 17 00:00:00 2001\n{head}\n{body}"


class Versions(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.records = self.dir / "records"
        self.output = self.dir / "public"
        base_build(self.records)

    def render(self) -> None:
        render.make_site(self.records, self.output)

    def read(self, *parts: str) -> str:
        return uncovered((self.output.joinpath(*parts)).read_text(encoding="utf-8"))

    def test_parse(self) -> None:
        self.assertEqual(render.version_of("[PATCH v3 0/2] x"), (3, False))
        self.assertEqual(render.version_of("[PATCH RFC v2] x"), (2, True))
        self.assertEqual(render.version_of("[RFC PATCH] x"), (1, True))
        self.assertEqual(render.version_of("x v2 y"), (1, False))
        self.assertEqual(render.version_label(1, rfc=True), "RFC")
        self.assertEqual(render.version_label(2, rfc=True), "RFC v2")
        cover = render.parse_cover(
            b"Subject: s\nchange-id: not-the-body\n\nhi\nchange-id: 2026-x-1\n"
        )
        self.assertEqual(cover.change_id, "2026-x-1")
        self.assertEqual(render.parse_cover(b"Subject: s\n\nhi\n").change_id, "")

    def test_by_change_id(self) -> None:
        change = "20261028-isol-e51694605371"
        add_series(
            self.records,
            "s1",
            letter(
                "[PATCH 0/5] nvme-fabrics: fix connects with isolated CPUs",
                "Mon, 2 Mar 2026 10:00:00 +0000",
                change=change,
            ),
        )
        add_series(
            self.records,
            "s3",
            letter(
                "[PATCH v3] blk-mq: allow passthrough requests on hctxs",
                "Mon, 9 Mar 2026 10:00:00 +0000",
                change=change,
            ),
        )
        add_series(
            self.records,
            "other",
            letter(
                "[PATCH v2] blk-mq: allow passthrough requests on hctxs",
                "Mon, 9 Mar 2026 09:00:00 +0000",
                author="B <b@x>",
            ),
        )
        self.render()
        overview = self.read("linux", "s3", "index.html")
        self.assertIn(
            '<dt>Versions</dt><dd><div class="versions">'
            '<a class="lineage-badge" href="../../linux/s1/index.html"',
            overview,
        )
        self.assertIn('<span class="lineage-badge current" title="[PATCH v3]', overview)
        self.assertNotIn("<dt>Versions</dt>", self.read("linux", "other", "index.html"))
        index = self.read("index.html")
        self.assertIn(
            '<div class="versions"><a class="lineage-badge"'
            ' href="./linux/s1/index.html"',
            index,
        )
        self.assertEqual(index.count('class="versions"'), 2)

    def test_by_subject_and_author(self) -> None:
        for name, subject, date in (
            ("s5", "[PATCH v5 0/2] nvme-tcp: io_cpu", "Mon, 2 Mar 2026 10:00:00 +0000"),
            ("s6", "[PATCH v6 0/2] NVMe-TCP: io_cpu", "Mon, 9 Mar 2026 10:00:00 +0000"),
            (
                "s6b",
                "[PATCH v6 0/2] nvme-tcp: io_cpu",
                "Tue, 10 Mar 2026 10:00:00 +0000",
            ),
        ):
            add_series(self.records, name, letter(subject, date))
        add_series(
            self.records,
            "x",
            letter(
                "[PATCH v4] nvme-tcp: io_cpu",
                "Mon, 2 Mar 2026 10:00:00 +0000",
                author="C <c@x>",
            ),
        )
        unapplied(self.records, "m@x", "b4/shazam", record("fail"))
        put(
            self.records / "linux" / "m@x" / "b4" / "mbox.mbx",
            letter("[PATCH v7] nvme-tcp: io_cpu", "Wed, 11 Mar 2026 10:00:00 +0000"),
        )
        self.render()
        page = self.read("linux", "s6b", "index.html")
        labels = re.findall(r'class="lineage-badge(?: current)?"[^>]*>([^<]+)<', page)
        self.assertEqual(labels, ["v5", "v6", "v6 resend", "v7"])
        self.assertIn(
            '<span class="lineage-badge current" title="[PATCH v6 0/2] nvme-tcp', page
        )
        not_applied = self.read("linux", "m@x", "index.html")
        self.assertIn('class="lineage-badge current"', not_applied)
        self.assertNotIn("<dt>Versions</dt>", self.read("linux", "x", "index.html"))


class ListFilter(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.records = self.dir / "records"
        self.output = self.dir / "public"
        base_build(self.records)

    def test_select_and_rows(self) -> None:
        add_series(
            self.records,
            "s",
            letter(
                "s",
                "Mon, 2 Mar 2026 10:00:00 +0000",
                To="linux-nvme@lists.infradead.org, L <linux-kernel@vger.kernel.org>",
            ),
            "linux-kernel@vger.kernel.org",
            "linux-nvme@lists.infradead.org",
        )
        add_series(
            self.records,
            "t",
            letter(
                "t", "Mon, 2 Mar 2026 11:00:00 +0000", Cc="linux-block@vger.kernel.org"
            ),
            "linux-block@vger.kernel.org",
        )
        render.make_site(self.records, self.output)
        options = (
            '<option value="">All lists</option>'
            '<option value="linux-kernel@vger.kernel.org"'
            ' title="linux-kernel@vger.kernel.org">LKML</option>'
            '<option value="linux-block@vger.kernel.org"'
            ' title="linux-block@vger.kernel.org">linux-block</option>'
            '<option value="linux-nvme@lists.infradead.org"'
            ' title="linux-nvme@lists.infradead.org">linux-nvme</option></select>'
            '<input type="search"'
        )
        for name in ("index.html", "bases.html", "search.html", "stats.html"):
            page = (self.output / name).read_text(encoding="utf-8")
            with self.subTest(page=name):
                if name == "stats.html":
                    self.assertNotIn('id="list"', page)
                else:
                    self.assertIn(options, page)
        search = (self.output / "search.html").read_text(encoding="utf-8")
        self.assertIn(
            ' data-lists="linux-kernel@vger.kernel.org'
            ' linux-nvme@lists.infradead.org" data-week=',
            search,
        )
        self.assertIn(' data-lists="linux-block@vger.kernel.org" data-week=', search)
        self.assertNotIn("data-lists", search.split('id="bases"')[1])
        self.assertIn('document.addEventListener("change"', render.THEME)
        self.assertIn(
            'if (event.target.id === "list") event.target.form.submit();', render.THEME
        )
        for part in (
            'list: "lists"',
            "list.value = chosen.list;",
            "bases.hidden = narrowed.length > 0",
        ):
            self.assertIn(part, render.SEARCH)


class Projects(unittest.TestCase):
    def setUp(self) -> None:
        top = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, top)
        self.records, self.output = top / "records", top / "public"

    def read(self, *parts: str) -> str:
        return self.output.joinpath(*parts).read_text(encoding="utf-8")

    def test_a_listing_is_of_one_project(self) -> None:
        base_build(self.records)
        add_series(self.records, "s", None)
        unapplied(self.records, "m@x", "b4/shazam", record("fail"))
        put(self.records / "qemu" / "v11.1.0" / "configure.json", record())
        put(self.records / "other" / "v2" / "make" / "all.json", record())
        render.make_site(self.records, self.output)
        pages = {"index.html": 2, "bases.html": 1, "search.html": 3}
        row = re.compile(r'<a href="\./([^/"]+)/[^"]*" class="(?:mono )?row-link">')
        for prefix, project in (("", "linux"), ("other-", "other"), ("qemu-", "qemu")):
            for name, rows in pages.items():
                with self.subTest(page=prefix + name):
                    page = self.read(prefix + name)
                    self.assertNotIn("All projects", page)
                    self.assertIn(
                        f' role="search"><select id="project" class="button"'
                        f' aria-label="Project" autocomplete="off">'
                        f'<option value="./{name}"{" selected" * (not prefix)}>'
                        "Linux kernel</option>"
                        f'<option value="./other-{name}"'
                        f'{" selected" * (project == "other")}>other</option>'
                        f'<option value="./qemu-{name}"'
                        f'{" selected" * (project == "qemu")}>QEMU</option>'
                        '</select><select id="list"',
                        page,
                    )
                    self.assertIn(f' action="./{prefix}search.html"', page)
                    self.assertIn(f' href="./{prefix}index.html">Series</a>', page)
                    self.assertIn(f' href="./{prefix}bases.html">Bases</a>', page)
                    self.assertIn(f' href="./{prefix}stats.html">Stats</a>', page)
                    found = row.findall(page)
                    self.assertLessEqual(set(found), {project})
                    if prefix:
                        self.assertEqual(len(found), name != "index.html")
                    else:
                        self.assertEqual(len(found), rows)
                    self.assertNotIn("data-project", page)
        bar = '<span class="stats-val">{}</span> Series'
        self.assertIn(bar.format(2), self.read("index.html"))
        self.assertIn(bar.format(0), self.read("qemu-index.html"))
        self.assertIn(
            'href="../../qemu-bases.html">', self.read("qemu", "v11.1.0", "index.html")
        )
        self.assertIn(
            'href="../../index.html">', self.read("linux", "m@x", "index.html")
        )
        self.assertNotIn("kernel.org", self.read("qemu", "v11.1.0", "index.html"))
        self.assertIn("location.assign(page);", render.THEME)
        self.assertNotIn("project", render.SEARCH)
        self.assertIn("(sought && !base)", render.SEARCH)

    def test_default_is_the_first_without_linux(self) -> None:
        put(self.records / "qemu" / "v11.1.0" / "configure.json", record())
        put(self.records / "other" / "v2" / "make" / "all.json", record())
        render.make_site(self.records, self.output)
        self.assertIn(
            '<option value="./index.html" selected>other</option>'
            '<option value="./qemu-index.html">QEMU</option>',
            self.read("index.html"),
        )
        self.assertFalse((self.output / "other-index.html").exists())

    def test_no_series_links_bases(self) -> None:
        put(self.records / "qemu" / "v11.1.0" / "configure.json", record())
        base_build(self.records)
        add_series(self.records, "s", None)
        render.make_site(self.records, self.output)
        line = (
            '<section id="series"><h2>Series</h2>\n<p class="muted">No series yet.'
            " Every record of this project is of a base: see"
            ' <a href="./qemu-bases.html">Bases</a>.</p></section>'
        )
        self.assertIn(line, self.read("qemu-index.html"))
        self.assertIn(line, self.read("qemu-search.html"))
        self.assertNotIn("No series yet.", self.read("index.html"))

    def test_project_named_as_a_listing(self) -> None:
        put(self.records / "qemu" / "v1" / "configure.json", record())
        put(self.records / "qemu-bases.html" / "v1" / "configure.json", record())
        with self.assertRaisesRegex(render.RenderError, "bases.html"):
            render.make_site(self.records, self.output)


class Site(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.records = self.dir / "records"
        self.output = self.dir / "public"
        self.build = self.records.joinpath("p", "c", B)

    def make(self) -> None:
        render.make_site(self.records, self.output)

    def html(self) -> str:
        pages = sorted(self.output.rglob("*.html"))
        return "".join(page.read_text(encoding="utf-8") for page in pages)

    def test_injection(self) -> None:
        project = self.records / "javascript:alert(1)"
        base = project.joinpath("<x>base", "<x>", "x86_64", "clang")
        series = project.joinpath("<x>series", "<x>", "x86_64", "clang")
        x = "<x>\"'&"
        bad = {"command": x, "env": {"X": x}, "producer": {"name": x, "version": x}}
        put(base.joinpath(*MAKE_ALL), record("fail", f"{x}\n", **bad))
        put(base / "sparse" / f"{x}.json", "{")
        put(series.joinpath(*MAKE_ALL), record("fail", f"{x} new\n{x}\n", **bad))
        put(series / "sparse" / f"{x}.json", record("fail", x, **bad))
        patch = {"commit": x, "subject": x, "author": x, "link": "javascript:x"}
        put(
            project / "<x>series" / "series.json",
            series_json(
                patch,
                base=x,
                base_name="<x>base",
                message_id="m@x",
                ref=x,
                trees=[{"remote": x, "url": "javascript:x"}],
            ),
        )
        put(series / "objects.json", {"schema": 1, "objects": [x], "unbuilt": [x]})
        put(series / "config", x)
        headers = ("Subject", "From", "Date", "Message-Id", "To", "Cc")
        put(
            project / "m@x" / "b4" / "mbox.mbx",
            "".join(f"{h}: {x}@y\n" for h in headers),
        )
        put(project / "m@x" / "b4" / "shazam.json", record(output=x))
        self.make()
        html = self.html()
        self.assertIn("&lt;x&gt;", html)
        self.assertIn("fails on both, +1 \u22120", html)
        self.assertIn("unreadable base record", html)
        self.assertNotIn("<x", html)
        self.assertNotIn('href="javascript', html)
        self.assertIn('href="./javascript%3Aalert%281%29/', html)
        self.assertNotIn("style=", html)
        self.assertIn("Content-Security-Policy", html)

    def test_unreadable(self) -> None:
        put(self.build / "a.json", "{")
        put(self.build / "b.json", record(output="\udcff"))
        put(self.build / "notes.json", {"not": "a record"})
        (self.build / "c.json").write_bytes(b'{"output": "\xff"}')
        self.make()
        html = self.html()
        self.assertIn(f"p/c/{B}/a.json: not JSON", html)
        self.assertIn(f"p/c/{B}/c.json: not UTF-8 at byte 12", html)
        self.assertIn(f"p/c/{B}/notes.json: no schema", html)
        self.assertIn("3 unreadable", html)
        self.assertIn("\\udcff", html)

    def test_series_without_lists(self) -> None:
        put(self.build.joinpath(*MAKE_ALL), record())
        put(self.records / "p" / "c" / "series.json", without(series_json(), "lists"))
        self.make()
        html = self.html()
        self.assertIn("p/c/series.json: no lists", html)
        self.assertIn('<span class="badge warn">unreadable</span>', html)

    def test_symlinks_skipped(self) -> None:
        put(self.build.joinpath(*MAKE_ALL), record())
        outside = self.dir / "outside"
        put(outside, record(output="outside"))
        (self.build / "config").symlink_to(outside)
        (self.build / "x.json").symlink_to(outside)
        (self.records / "q").symlink_to(self.records / "p")
        other = self.records.joinpath("p", "d", "e", "x86_64")
        other.mkdir(parents=True)
        (other / "clang").symlink_to(self.build)
        message = self.dir / "message"
        put(message / "b4" / "mbox.mbx", "Subject: outside\n")
        put(message / "b4" / "shazam.json", record(output="outside"))
        (self.records / "p" / "m@x").symlink_to(message)
        series = self.records.joinpath("p", "s", B)
        put(series.joinpath(*MAKE_ALL), record())
        put(
            series.parent.parent.parent / "series.json",
            series_json(commit(), message_id="m@x"),
        )
        self.make()
        self.assertFalse(self.output.joinpath("p", "c", B, "config.txt").exists())
        self.assertFalse((self.output / "q").exists())
        self.assertFalse((self.output / "p" / "d").exists())
        self.assertNotIn("outside", self.html())

    def test_cut(self) -> None:
        output = "".join(f"line {n}\r\n" for n in range(render.CUT + 1))
        put(self.build / "sparse" / "big.json", record("fail", output))
        self.make()
        page = self.output.joinpath("p", "c", B, "index.html").read_text()
        self.assertIn(f"The first {render.CUT} of {render.CUT + 1} lines", page)
        self.assertIn('href="./output/sparse/big.txt"', page)
        self.assertNotIn(f"line {render.CUT}\u240d", page)
        self.assertIn("line 0\u240d\nline 1\u240d", page)
        whole = self.output.joinpath("p", "c", B, "output", "sparse", "big.txt")
        self.assertEqual(whole.read_bytes(), output.encode())

    def test_colours_kept(self) -> None:
        output = "".join(f"\x1b[31m{n}\x1b[0m\n" for n in range(render.CUT + 1))
        put(self.build / "sparse" / "big.json", record("fail", output))
        self.make()
        page = self.output.joinpath("p", "c", B, "index.html").read_text()
        self.assertIn('<pre><span class="fail">0</span>\n<span class="fail">1', page)
        self.assertNotIn("\x1b", page)
        self.assertNotIn("\u241b", page)
        whole = self.output.joinpath("p", "c", B, "output", "sparse", "big.txt")
        self.assertEqual(whole.read_bytes(), output.encode())

    def test_cut_beside_its_base(self) -> None:
        output = "".join(f"line {n}\n" for n in range(render.CUT + 1))
        for name in ("v1", "v1-1-gx"):
            put(self.records.joinpath("p", name, B, "big.json"), record("fail", output))
        put(self.records / "p" / "v1-1-gx" / "series.json", series_json(commit()))
        self.make()
        page = self.output.joinpath("p", "v1-1-gx", B, "index.html").read_text()
        self.assertIn('href="./output/big.txt"', page)
        self.assertIn(f'href="../../../../../p/v1/{B}/output/big.txt"', page)
        for name in ("v1", "v1-1-gx"):
            whole = self.output.joinpath("p", name, B, "output", "big.txt")
            self.assertEqual(whole.read_text(), output)

    def test_refusals(self) -> None:
        put(self.build.joinpath(*MAKE_ALL), record())
        with self.assertRaisesRegex(render.RenderError, "inside --records"):
            render.make_site(self.records, self.records / "public")
        put(self.output / "kept", "")
        with self.assertRaisesRegex(render.RenderError, "holds no site render wrote"):
            self.make()
        self.assertEqual([p.name for p in self.output.iterdir()], ["kept"])
        shutil.rmtree(self.output)
        for name in ("style.css", "search.js"):
            put(self.records.joinpath(name, "c", B, *MAKE_ALL), record())
            with self.assertRaisesRegex(render.RenderError, name):
                self.make()
            self.assertFalse(self.output.exists())
            shutil.rmtree(self.records / name)

    def test_replaces_its_own_site(self) -> None:
        put(self.build.joinpath(*MAKE_ALL), record())
        self.make()
        put(self.output / "stale.html", "")
        self.make()
        self.assertTrue((self.output / render.MARK).is_file())
        self.assertFalse((self.output / "stale.html").exists())
        self.assertTrue(self.output.joinpath("p", "c", B, "index.html").is_file())
        self.assertEqual(
            [p.name for p in self.output.parent.iterdir() if p.name.startswith(".")],
            [],
        )

    def test_footer(self) -> None:
        put(self.build.joinpath(*MAKE_ALL), record())
        render.make_site(self.records, self.output, epoch=0)
        self.assertIn("records on 1970-01-01 00:00 UTC", self.html())


class Earlier(unittest.TestCase):
    FIRST, LAST = "1" * 40, "2" * 40
    NAME = "v1-1-g" + FIRST[:12]

    def tip(self) -> render.Tree:
        commits = (
            render.Commit(self.FIRST, "s", "A B <a@x>"),
            render.Commit(self.LAST, "t", "A B <a@x>"),
        )
        series = dataclasses.replace(SERIES, commits=commits)
        return dataclasses.replace(tree({}, "v1-2-g" + self.LAST[:12]), series=series)

    def test_folded(self) -> None:
        tip = self.tip()
        other = tree({"make/all": parsed()}, "v1-1-g" + "3" * 12)
        first = self.own(tree({"make/all": parsed("fail")}, self.NAME))
        dirty = self.own(tree({"make/all": parsed()}, self.NAME + "-dirty"))
        trees = render.fold_earlier([tip, first, other, dirty])
        self.assertEqual(trees[2:], [dirty])
        trees = trees[:2]
        self.assertEqual([t.commit for t in trees], [tip.commit, other.commit])
        key = f"bisect/{self.FIRST}"
        failed = dataclasses.replace(parsed("fail"), series_check=True)
        self.assertEqual(trees[0].records[key], failed)
        compared = render.compare(trees[0], None)[key]
        self.assertIs(compared.change, Change.INTRODUCED)
        self.assertEqual(compared.own, render.Own.SERIES_ONLY)

    def own(self, built: render.Tree) -> render.Tree:
        """A tree of FIRST that came from no message: its own base."""
        came = render.Series(self.FIRST, built.commit, (), (), commit=self.FIRST)
        return dataclasses.replace(built, series=came)

    def test_base_kept(self) -> None:
        on_it = dataclasses.replace(SERIES, base_name=self.NAME)
        other = dataclasses.replace(tree({}, "v1-3-g" + "3" * 12), series=on_it)
        own = self.own(tree({"make/all": parsed()}, self.NAME))
        self.assertIn(own, render.fold_earlier([self.tip(), own, other]))


class Git(unittest.TestCase):
    def setUp(self) -> None:
        git = shutil.which("git")
        if git is None:
            self.skipTest("git is not installed")
        self.path = git
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "GIT_COMMITTER_DATE": "2026-11-03T10:07:00+01:00",
        }

    def git(self, cwd: Path, *args: str) -> None:
        # The path is shutil.which's, the args are literals; no shell runs.
        subprocess.run(  # noqa: S603
            [self.path, "-C", str(cwd), *args],
            env=self.env,
            check=True,
            capture_output=True,
        )

    def checkout(self, top: Path, records: Path) -> None:
        put(records.joinpath("p", "c", B, *MAKE_ALL), record())
        self.git(top, "init", "--quiet")
        self.git(top, "add", ".")
        self.git(top, "commit", "--quiet", "--message", "run one")

    def test_dated(self) -> None:
        records = self.dir / "records"
        self.checkout(records, records)
        git = render.git_of(records)
        self.assertEqual(git, self.path)
        if git is None:
            self.fail("records is the top of its checkout")
        trees = render.load(records, render.history_of(git, records))
        recorded = trees[0].recorded
        if recorded is None:
            self.fail("the tree has no recorded commit")
        self.assertEqual(render.stamp(recorded.when), "2026-11-03 09:07")
        self.assertEqual(recorded.subject, "run one")

    def test_unapplied_dated(self) -> None:
        records = self.dir / "records"
        put(records / "p" / "a@x" / "b4" / "shazam.json", record("fail"))
        put(records / "p" / "b@x" / "b4" / "shazam.json", record("fail"))
        self.checkout(records, records)
        put(records / "p" / "b@x" / "b4" / "shazam.json", record("fail", "x\n"))
        self.env["GIT_COMMITTER_DATE"] = "2026-11-04T10:07:00+01:00"
        self.git(records, "commit", "--quiet", "--all", "--message", "run two")
        history = render.history_of(self.path, records)
        found = render.load_unapplied(records, history)
        recorded = {item.name: item.recorded for item in found}
        self.assertEqual(
            {name: r.subject if r else None for name, r in recorded.items()},
            {"a@x": "run one", "b@x": "run two"},
        )

    def test_not_the_top(self) -> None:
        records = self.dir / "records"
        self.checkout(self.dir, records)
        self.assertIsNone(render.git_of(records))
        render.make_site(records, self.dir / "public")
        index = (self.dir / "public" / "index.html").read_text()
        self.assertNotIn("Recorded", index)


@dataclasses.dataclass(frozen=True, slots=True)
class Builds:
    """The builds a report's series has, and the .config of each."""

    names: tuple[str, ...] = ("b1",)
    config: bytes | None = None


def mailed(
    series: str | Mapping[str, str],
    base: str = "",
    *,
    cover: render.Cover | None = None,
    link: str = "",
    address: str | None = None,
    **extra: object,
) -> str:
    """The report of a series on v1 whose sparse/x printed series, its base base.

    series may instead map each of several keys to what it printed; extra
    goes into each of the series' records, but builds, a Builds.
    """
    builds = extra.pop("builds", Builds())
    if not isinstance(builds, Builds):
        raise TypeError(builds)
    patch = render.Commit("c" * 40, "nvme: x", "A <a@x>", link)
    on = render.Series("b" * 40, "v1", (patch,), ())
    printed = {"sparse/x": series} if isinstance(series, str) else series
    found = {
        key: render.parse_record(record("fail", out, **extra))
        for key, out in printed.items()
    }
    olds = [
        render.Tree(
            "linux",
            "v1",
            build,
            {key: parsed("pass", base) for key in printed},
            None,
            None,
            None,
        )
        for build in builds.names
    ]
    news = [
        render.Tree("linux", "v1-1-gabc", build, found, on, builds.config, None, cover)
        for build in builds.names
    ]
    overview = render.overviews_of(render.pages_of([*olds, *news]))[1]
    return render.report(overview, on, address)


def said(text: str) -> list[str]:
    """The lines of a report's body up to its first section or signature."""
    body = text.partition("\n\n")[2].split("\n")
    return list(takewhile(lambda line: line not in {render.RULE, "-- "}, body))


def cover(subject: str, message_id: str = "<m@x>") -> render.Cover:
    return render.Cover(subject, "A <a@x>", "", message_id)


class Report(unittest.TestCase):
    def test_headers_reply_to_the_cover(self) -> None:
        text = mailed("w\n", cover=cover("[PATCH 0/1] nvme: x"))
        self.assertTrue(
            text.startswith(
                "Subject: Re: [PATCH 0/1] nvme: x\n"
                "In-Reply-To: <m@x>\n"
                "References: <m@x>\n"
                "MIME-Version: 1.0\n"
                "Content-Type: text/plain; charset=utf-8\n"
                "Content-Transfer-Encoding: 8bit\n\n"
                "kdevops-ng built this series of 1 patch on v1 ("
            )
        )
        self.assertIn("\n$ b4 shazam --add-link m@x\n", text)
        reply = mailed("w\n", cover=cover("RE: [PATCH] x"))
        self.assertTrue(reply.startswith("Subject: RE: [PATCH] x\n"))

    def test_headers_without_a_cover(self) -> None:
        text = mailed("w\n")
        self.assertTrue(text.startswith("Subject: Re: nvme: x\nMIME-Version: 1.0\n"))
        self.assertNotIn("In-Reply-To", text)
        self.assertNotIn("b4 shazam", text)
        linked = mailed("w\n", link=LINK)
        message_id = "20260929-nvme-mam-v1-1-48dcbe79cece@samsung.com"
        self.assertIn(f"\nIn-Reply-To: <{message_id}>\n", linked)
        self.assertIn(f"\n$ b4 shazam --add-link {message_id}\n", linked)
        self.assertNotIn("In-Reply-To", mailed("w\n", link="https://e.org/x@y"))

    def test_non_ascii_subject(self) -> None:
        subject = "[PATCH v2 0/3] nvme: caf\u00e9 for the \u00fcber " + "long " * 12
        text = mailed("w\n", cover=cover(subject))
        head = text.partition("\n\n")[0]
        self.assertTrue(head.isascii())
        self.assertIn("=?utf-8?", head)
        folded = policy.default.max_line_length or 0
        self.assertTrue(all(len(line) <= folded for line in head.split("\n")))
        message = BytesParser(policy=policy.default).parsebytes(text.encode())
        self.assertEqual(message["Subject"], "Re: " + " ".join(subject.split()))
        self.assertEqual(message["In-Reply-To"], "<m@x>")

    def test_prose_wrapped(self) -> None:
        text = render.prose(("word " * 30) + "check/sparse/a-very-long-path-name.o")
        rows = text.split("\n")
        self.assertTrue(all(len(row) <= render.WIDTH for row in rows))
        self.assertEqual(rows[-1].split()[-1], "check/sparse/a-very-long-path-name.o")
        self.assertEqual(render.prose("+1 \u22122 caf\u00e9"), "+1 -2 caf\\xe9")
        many = "\n".join(f"a.c:{n}: warning: w{n}" for n in range(9)) + "\n"
        for line in said(mailed(many, cover=cover("x " * 40), address="https://e.org")):
            if not line.startswith(("https://", "$ ")):
                self.assertLessEqual(len(line), render.WIDTH, line)
                self.assertTrue(line.isascii(), line)

    def test_tool_output_verbatim(self) -> None:
        wide = "x" * 300 + " -- " + "y" * 300
        huge = "\u00e9" * 1000
        text = mailed(f"{wide}\n{huge}\n")
        self.assertIn(f"\n{wide}\n", text)
        cut = next(line for line in text.split("\n") if line.endswith("[truncated]"))
        self.assertLessEqual(len(cut.encode()), render.LONGEST)
        self.assertTrue(cut.startswith("\u00e9" * 400))

    def test_colours_stripped(self) -> None:
        text = mailed("\x1b[1;31mred\x1b[0m: \x1b[Kx\r\ttab\n")
        self.assertIn("\nred: \u241b[Kx\u240d\ttab\n", text)

    def test_only_what_the_base_lacks(self) -> None:
        text = mailed("old\nnew\n", "old\n")
        self.assertIn("\nnew\n", text)
        self.assertNotIn("\nold\n", text)
        self.assertIn("\nsparse/x, b1\nintroduced, +1 -0\n", text)
        self.assertIn("\n$ ARCH=x86_64 LLVM=1 make -s\n\nnew\n", text)
        both = render.compare_records(parsed("fail", "a\nb\n"), parsed("fail", "c\n"))
        self.assertTrue(render.prose(both.label).isascii())
        self.assertIn("  b1: 1 introduced\n", text)

    def test_cut(self) -> None:
        text = mailed("".join(f"line {n}\n" for n in range(render.CUT + 10)))
        self.assertIn(f"\nline {render.CUT - 1}\n[10 more lines]\n", text)
        self.assertNotIn(f"line {render.CUT}\n", text)

    def test_clean(self) -> None:
        commit = render.Commit("c" * 40, "nvme: x", "A <a@x>")
        on = render.Series("b" * 40, "v1", (commit,), ())
        old = render.Tree("linux", "v1", "b1", {"build": parsed()}, None, None, None)
        new = render.Tree(
            "linux", "v1-1-gabc", "b1", {"build": parsed()}, on, None, None
        )
        overview = render.overviews_of(render.pages_of([old, new]))[1]
        text = render.report(overview, on)
        self.assertIn("\n  b1: nothing introduced\n", text)
        self.assertNotIn(render.RULE, text)
        self.assertTrue(
            text.endswith("\n\n-- \n" + render.SIGNATURE.split("\n")[1] + "\n")
        )

    def test_address(self) -> None:
        text = mailed("w\n", address="https://e.org/r/")
        self.assertIn(
            "\nFull results:\nhttps://e.org/r/linux/v1-1-gabc/index.html\n", text
        )
        self.assertIn(
            "\nhttps://e.org/r/linux/v1-1-gabc/b1/index.html#r-sparse-x\n", text
        )
        bare = mailed("w\n")
        self.assertNotIn("e.org", bare)
        self.assertNotIn("Full results", bare)

    def test_reproduce_pinned(self) -> None:
        text = mailed("w\n", cover=cover("x"), producer=kdevops_ng(SHA))
        self.assertIn(
            "\n\nRun these in a Linux clone. The exports make git commit as"
            " kdevops-ng\ndid, so commit hashes in the output match.\n\n"
            f"$ git checkout {'b' * 40}\n"
            f"$ nix develop \\\n    github:linux-kdevops/kdevops-ng/{SHA}\n"
            "$ export GIT_COMMITTER_NAME=kdevops-ng \\\n"
            "    GIT_COMMITTER_EMAIL=kdevops-ng@localhost\n"
            "$ export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=b4.shazam-am-flags \\\n"
            "    GIT_CONFIG_VALUE_0=--committer-date-is-author-date\n"
            "$ b4 shazam --add-link m@x\n\n",
            text,
        )
        self.assertNotIn("no single published", text)

    def test_another_producer(self) -> None:
        other = {"name": "other-ci", "version": SHA}
        text = mailed("w\n", cover=cover("x"), producer=other)
        self.assertIn("\n\nother-ci built this series of 1 patch on v1 (", text)
        self.assertIn(
            "\n\nRun these in a clone of the project.\n\n"
            f"$ git checkout {'b' * 40}\n$ b4 shazam m@x\n\n",
            text,
        )
        self.assertNotIn("nix develop", text)
        self.assertNotIn("GIT_COMMITTER_NAME", text)
        self.assertTrue(
            text.endswith("-- \nkresults https://github.com/linux-kdevops/kresults\n")
        )

    def test_reproduce_unpinned(self) -> None:
        older = parsed("fail", "w\n")
        for producer in (PRODUCER, kdevops_ng(SHA + "-dirty"), kdevops_ng("v1")):
            with self.subTest(producer=producer):
                text = mailed("w\n", cover=cover("x"), producer=producer)
                self.assertIn("\n$ nix develop github:linux-kdevops/kdevops-ng\n", text)
                self.assertIn("\nThe records don't name one published", text)
        # One record names SHA, another none.
        commit = render.Commit("c" * 40, "nvme: x", "A <a@x>")
        on = render.Series("b" * 40, "v1", (commit,), ())
        named = render.parse_record(record("fail", "w\n", producer=kdevops_ng(SHA)))
        new = render.Tree(
            "linux",
            "v1-1-gabc",
            "b1",
            {"a": named, "b": older},
            on,
            None,
            None,
            cover("x"),
        )
        overview = render.overviews_of(render.pages_of([new]))[0]
        self.assertIsNone(render.pinned(overview))
        mixed = dataclasses.replace(new, records={"a": named})
        overview = render.overviews_of(render.pages_of([mixed]))[0]
        self.assertEqual(render.pinned(overview), SHA)

    def test_command(self) -> None:
        make = "make -s C=2 " + "x" * 1000
        text = mailed("w\n", command=make)
        line = render.verbatim(f"ARCH=x86_64 LLVM=1 {make}")
        self.assertIn(f"\nintroduced, +1 -0, ran differently\n$ {line}\n\nw\n", text)
        same = mailed("w\n")
        self.assertIn("\nintroduced, +1 -0\n$ ARCH=x86_64 LLVM=1 make -s\n\nw\n", same)

    def test_config_fetched_once_per_build(self) -> None:
        outputs = {"sparse/x": "a\n", "sparse/y": "b\n", "sparse/z": "c\n"}
        address = "https://e.org/r"
        builds = Builds(("b1", "b2"), b"CONFIG_X=y\n")
        text = mailed(outputs, address=address, builds=builds)
        for build in ("b1", "b2"):
            curl = (
                "$ curl --create-dirs --output build/.config \\\n"
                f"    {address}/linux/v1-1-gabc/{build}/config.txt\n"
            )
            self.assertEqual(text.count(curl), 1)
            self.assertIn(
                f"sparse/x, {build}\n", text.split(curl)[0].split(render.RULE)[-1]
            )
        bare = mailed(outputs, builds=Builds(config=b"CONFIG_X=y\n"))
        self.assertNotIn("curl", bare)
        said_once = "Its .config is config.txt beside the build's page; copy it to"
        self.assertEqual(bare.count(said_once), 1)
        self.assertNotIn("curl", mailed(outputs, address=address))

    def test_config_not_for_the_series(self) -> None:
        outputs = {"sparse/x": "a\n", "sparse/y": "b\n"}
        builds = Builds(config=b"C=y\n")
        text = mailed(outputs, address="https://e.org", builds=builds, range=RANGE)
        self.assertNotIn("curl", text)

    def test_quoted_once(self) -> None:
        outputs = {
            "sparse/x": "same\ntotal: 1\n",
            "sparse/y": "same\ntotal: 2\nnew\n",
            "sparse/z": "same\nnew\n",
        }
        text = mailed(outputs, builds=Builds(("b1", "b2")))
        sections = text.split(render.RULE + "\n")[1:]
        self.assertTrue(sections[0].endswith("\n\nsame\ntotal: 1\n\n"))
        self.assertTrue(
            sections[1].endswith(
                "\n\n[1 line as quoted above for sparse/x, b1]\ntotal: 2\nnew\n\n"
            )
        )
        self.assertTrue(
            sections[2].endswith(
                "\n\n[1 line as quoted above for sparse/x, b1]\n"
                "[1 line as quoted above for sparse/y, b1]\n\n"
            )
        )
        self.assertTrue(
            sections[3].endswith("\n\n[2 lines as quoted above for sparse/x, b1]\n\n"),
            sections[3],
        )
        self.assertEqual(len(sections), 6)

    def test_written_linked_and_reproducible(self) -> None:
        records = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, records)
        base_build(records)
        add_series(records, "v1-1-gabc", cover_letter("[PATCH] x", None))
        out = [records.parent / f"{records.name}-{n}" for n in (1, 2)]
        for output in out:
            self.addCleanup(shutil.rmtree, output, ignore_errors=True)
            render.make_site(records, output, address="https://e.org")
        found = sorted(p.relative_to(out[0]) for p in out[0].rglob(render.REPORT))
        self.assertEqual(found, [Path("linux/v1-1-gabc/report.txt")])
        first, second = (o.joinpath(found[0]).read_bytes() for o in out)
        self.assertEqual(first, second)
        self.assertIn(b"https://e.org/linux/v1-1-gabc/index.html", first)
        page = (out[0] / "linux" / "v1-1-gabc" / "index.html").read_text()
        self.assertIn('<dt>Report</dt><dd><a href="./report.txt"', page)


# Every figure an invocation's resources can give, and how a page words them.
RESOURCES = {
    "cpu": {
        "seconds": 2015.005,
        "user_seconds": 1800,
        "system_seconds": 215.005,
        "stalled_seconds": 0.25,
    },
    "memory": {
        "peak": 2158465024,
        "swap_peak": 0,
        "stalled_seconds": 0,
        "high_events": 0,
        "max_events": 1,
        "oom_kills": 0,
        "major_faults": 12,
    },
    "io": {
        "stalled_seconds": 1.5,
        "read_bytes": 1536,
        "write_bytes": 5368709120,
        "read_operations": 3,
        "write_operations": 40,
    },
    "tasks": {"peak": 153},
}
TOOK = (
    "CPU time 2015.005 s, user 1800.000 s, system 215.005 s, stalled 0.250 s;"
    " memory peak 2.0 GiB, swap peak 0 B, stalled 0.000 s, high events 0,"
    " max events 1, OOM kills 0, major faults 12;"
    " I/O stalled 1.500 s, read 1.5 KiB, written 5.0 GiB, reads 3, writes 40;"
    " tasks peak 153"
)


class Resources(unittest.TestCase):
    def test_every_figure(self) -> None:
        took = render.parse_record(record(resources=RESOURCES))
        self.assertEqual(render.resources_text(took.resources), TOOK)
        self.assertEqual(
            render.invocation_html(took),
            '<p class="note text-muted-sm">Run <span class="mono">Gn7rQ4G1Nsn</span>'
            ' at <span class="mono">lab-a</span>, started 2026-11-10T09:12:03Z, 1 s'
            f" on AMD Ryzen 9, 32 CPUs, 128 GiB<br>{TOOK}</p>",
        )

    def test_only_those_given(self) -> None:
        some = {"cpu": {"seconds": 2}, "memory": {"peak": 1023}}
        took = render.parse_record(record(resources=some))
        self.assertEqual(
            render.resources_text(took.resources),
            "CPU time 2.000 s; memory peak 1023 B",
        )
        for none in (record(), record(resources={}), record(resources={"io": {}})):
            with self.subTest(none=none):
                bare = render.parse_record(none)
                self.assertEqual(bare.resources, ())
                self.assertNotIn("<br>", render.invocation_html(bare))

    def test_of_the_latest_invocation(self) -> None:
        later = invocation(started="2026-11-10T10:00:00Z", resources=RESOURCES)
        data = {
            **record(),
            "entries": [{**entry(), "invocations": [later, invocation()]}],
        }
        self.assertEqual(
            render.resources_text(render.parse_record(data).resources), TOOK
        )

    def test_bytes(self) -> None:
        for count, text in (
            (0, "0 B"),
            (1023, "1023 B"),
            (1024, "1.0 KiB"),
            (66496024576, "61.9 GiB"),
            (2**50, "1024.0 TiB"),
        ):
            with self.subTest(count=count):
                self.assertEqual(render.bytes_text(count), text)


class Format(unittest.TestCase):
    """render reads the tree validate accepts."""

    # A build with no ARCH set, under the one its .config names, of a tree
    # with changes not committed: a commit that came from no message.
    HOST = "tinyconfig/x86/gcc"
    DIRTY = "v1.2-dirty"

    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        self.records = root / "records"
        self.output = root / "public"
        tiny = test_validate.TINY
        clang, config = test_validate.CLANG, test_validate.CONFIG
        base = {"commit": test_validate.BASE, "build": test_validate.build(tiny)}
        make = test_validate.entry("make -s", clang, config=config)
        sparse = test_validate.entry("make -s C=2 init/main.o", clang, config=config)
        took = test_validate.entry("make", config=config, output="a.c:3: note: x\n")
        took["invocations"][0]["resources"] = RESOURCES
        host = {"commit": test_validate.BASE, "build": test_validate.build(self.HOST)}
        own = test_validate.series(test_validate.BASE, "")
        files = {
            **test_validate.good(),
            f"linux/v1.2/{tiny}/make/all.json": test_validate.record(base, make),
            f"linux/v1.2/{tiny}/sparse/init/main.o.json": test_validate.record(
                {**base, "file": "init/main.o"}, sparse
            ),
            f"linux/v1.2/{tiny}/config": "CONFIG_EXAMPLE=y\n",
            f"linux/{self.DIRTY}/series.json": {**own, "base_name": self.DIRTY},
            f"linux/{self.DIRTY}/{self.HOST}/make/all.json": test_validate.record(
                host, took
            ),
            f"linux/{self.DIRTY}/{self.HOST}/config": "# Linux/x86 1.2.0\n",
        }
        for path, data in files.items():
            put(self.records / path, data)
        self.assertEqual(list(validate.check(self.records)), [])
        render.make_site(self.records, self.output)

    def page(self, *parts: str) -> str:
        return uncovered(self.output.joinpath(*parts).read_text(encoding="utf-8"))

    def test_validated_tree(self) -> None:
        output = self.output
        html = "".join(
            page.read_text(encoding="utf-8") for page in sorted(output.rglob("*.html"))
        )
        self.assertNotIn("unreadable", html)
        commit_dir = output / test_validate.COMMIT
        tiny = test_validate.TINY
        build = self.page(test_validate.COMMIT, tiny, "index.html")
        index = self.page("index.html")
        self.assertIn(
            f'<span class="suite">make {CHECK}</span> <span class="suite">sparse'
            ' <span class="muted" title="1 finding new">+1</span></span>',
            index,
        )
        self.assertIn("ARCH=x86_64 LLVM=1 make -s C=2 init/main.o", build)
        self.assertIn('<details id="r-sparse-init-main.o" open>', build)
        self.assertIn("<dt>Machine</dt><dd>Example, 4 CPUs, 8 GiB</dd>", build)
        self.assertIn(
            'Run <span class="mono">run-1</span> at <span class="mono">lab-a</span>',
            build,
        )
        for page in (
            commit_dir / "index.html",
            commit_dir / test_validate.GUEST / "index.html",
            output / "linux" / test_validate.UNAPPLIED / "index.html",
            output / "linux" / "v1.2" / "index.html",
            output / "stats.html",
        ):
            with self.subTest(page=page):
                self.assertTrue(page.is_file())
        unapplied_page = self.page("linux", test_validate.UNAPPLIED, "index.html")
        self.assertIn("b4 could not apply the series", unapplied_page)
        self.assertFalse((output / "linux" / test_validate.APPLIED).exists())

    def test_records_under_the_commit(self) -> None:
        under = self.page(test_validate.COMMIT, render.NO_BUILD_DIR, "index.html")
        for anchor in (
            "r-checkpatch.pl",
            "r-make-htmldocs-filesystems",
            "r-kernel-doc-init-main.c",
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(f'<details id="{anchor}"', under)
        self.assertIn('<span class="badge none">series only</span>', under)
        self.assertNotIn("<dt>Configuration</dt>", under)
        overview = self.page(test_validate.COMMIT, "index.html")
        self.assertIn(
            f'/{render.NO_BUILD_DIR}/index.html" class="mono row-link">', overview
        )
        self.assertIn(">no build</a>", overview)
        build = self.page(test_validate.COMMIT, test_validate.TINY, "index.html")
        self.assertNotIn("checkpatch.pl", build)

    def test_resources_on_a_page(self) -> None:
        build = self.page("linux", self.DIRTY, self.HOST, "index.html")
        self.assertIn(
            '<p class="step muted mono">$ make</p><p class="note text-muted-sm">Run'
            ' <span class="mono">run-1</span> at <span class="mono">lab-a</span>,'
            " started 2026-01-02T03:04:05Z, 3 s on Example, 4 CPUs, 8 GiB"
            f"<br>{TOOK}</p>",
            build,
        )
        other = self.page("linux", "v1.2", test_validate.TINY, "index.html")
        self.assertIn(" 3 s on Example, 4 CPUs, 8 GiB</p>", other)
        self.assertNotIn("CPU time", other)

    def test_x86_arch_directory(self) -> None:
        trees = render.load(self.records, {})
        self.assertIn(
            ("linux", self.DIRTY, *self.HOST.split("/")), [t.parts for t in trees]
        )
        build = self.page("linux", self.DIRTY, self.HOST, "index.html")
        self.assertIn(f'<dt>Build</dt><dd><span class="mono">{self.HOST}</span>', build)
        self.assertIn('<p class="step muted mono">$ make</p>', build)
        self.assertIn('<a href="./config.txt">.config</a>', build)
        config = self.output.joinpath("linux", self.DIRTY, self.HOST, "config.txt")
        self.assertEqual(config.read_text(), "# Linux/x86 1.2.0\n")

    def test_dirty_tree(self) -> None:
        bases = self.page("bases.html")
        for name in ("v1.2", self.DIRTY):
            with self.subTest(name=name):
                href = f"./linux/{name}/index.html"
                self.assertIn(
                    f'<a href="{href}" class="mono row-link">{name}</a>', bases
                )
        build = self.page("linux", self.DIRTY, self.HOST, "index.html")
        self.assertIn(f'<h1 class="mono">{self.DIRTY}</h1>', build)
        self.assertIn(
            f'class="mono" title="linux-kdevops">{test_validate.BASE[:12]}</a>'
            ' <span class="muted">with changes not committed</span></dd>',
            build,
        )
        # A line of the tree is cited at the commit the changes lie on.
        self.assertIn(f"/blob/{test_validate.BASE}/a.c#L3", build)
        clean = self.page("linux", "v1.2", test_validate.TINY, "index.html")
        self.assertNotIn("with changes not committed", clean)

    def test_commit_that_is_its_own_base(self) -> None:
        series = render.parse_series(
            json.loads(
                (self.records / "linux" / self.DIRTY / "series.json").read_text()
            )
        )
        self.assertEqual((series.base, series.commits), (series.commit, ()))
        pages = render.pages_of(render.load(self.records, {}))
        own = [page for page in pages if page.tree.commit == self.DIRTY]
        self.assertEqual([(page.series, page.base) for page in own], [(None, None)])
        self.assertNotIn(self.DIRTY, self.page("index.html"))
        overview = self.page("linux", self.DIRTY, "index.html")
        self.assertIn("Records of this commit, one row per build", overview)
        self.assertFalse((self.output / "linux" / self.DIRTY / render.REPORT).exists())
        self.assertTrue((self.output / test_validate.COMMIT / render.REPORT).is_file())


if __name__ == "__main__":
    unittest.main()
