#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""File the results of one blktests run as records under --records (README.md).

Run in the tree of the kernel under test, once the run has copied its
results directory with, in it:

- machine/, the facts of the machine under test: arch, cpuinfo, meminfo,
  and version, the `git describe` of the blktests that ran;
- NAME.stdout, what check printed for each group or test the run asked
  for, which alone says why a test did not run. Without it such a test
  leaves no record.

    kblktests --results DIR --config build/.config --configuration NAME ...

--qemu names the QEMU the machine under test ran on, a tool of each entry.
Print each file written, and how many results leave no record with why.
Exit 0 either way, as krecord does.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from record import (
    VERSION,
    Json,
    NoRecordError,
    built,
    configuration_of,
    find,
    memory_of,
    merged,
    need,
    output_of,
    placed,
    printed_in,
    run,
    run_of,
    state,
    tool_of,
    version_of,
    write,
    write_base,
)

# A result file is named by its test's number alone: 001.full, 001.out.bad
# and 001.dmesg lie beside it.
RESULT = re.compile(r"[0-9]+")
# The files a failed test leaves beside its result, in the output's order.
FAILURES = ("out.bad", "dmesg")
FACTS = ("arch", "cpuinfo", "meminfo", "version")
# A result's path from the results directory ends in its device, its group
# and its number.
DEPTH = 3
DATE = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")
# An entry's verdict, by the status of a result.
VERDICTS = {"pass": "pass", "fail": "fail", "not run": "skip"}
# The line check prints of a test that did not run, on a device or on none.
# The lines of its reason follow, indented. One of a whole group, loop/***,
# names no test.
NOT_RUN = re.compile(r"(\S+/\d+)(?: => (\S+))? \(.*\)\s*\[not run\]")
INDENT = "    "


def results_in(results: Path) -> list[Path]:
    """Return the result files under a results directory, in order.

    A result lies in its group's directory under its device's. What lies
    above the device is what the run was asked for, a group or one test, so
    the depth varies.
    """
    found = (
        file.relative_to(results)
        for file in sorted(results.rglob("*"))
        if RESULT.fullmatch(file.name) and file.is_file()
    )
    return [
        results / file
        for file in found
        if len(file.parts) >= DEPTH and file.parts[0] != "machine"
    ]


def reasons_in(results: Path) -> dict[tuple[str, str], list[str]]:
    """Return why each test did not run, by its device and name, as check printed.

    The last file that names a test gives its reason.
    """
    reasons: dict[tuple[str, str], list[str]] = {}
    for file in sorted(results.glob("*.stdout")):
        # The reason being read. An indented line of anything else is kept
        # nowhere.
        reason: list[str] = []
        for line in printed_in([file]):
            if line.startswith(INDENT):
                reason.append(line.removeprefix(INDENT))
                continue
            reason = []
            if found := NOT_RUN.fullmatch(line):
                reasons[found[2] or "nodev", found[1]] = reason
    return reasons


def qemu_of(file: Path) -> Json:
    """Return the QEMU the machine under test ran on, as an entry names a tool."""
    path = shutil.which(file.resolve()) or ""
    need(path, f"{file} is no program")
    printed = run(path, "--version").partition("\n")[0]
    need(printed, f"{file} prints no version")
    # QEMU prints its version, then in brackets its package or the describe
    # name of its source, which holds a version of its own.
    tool = tool_of("qemu", path, re.sub(r"[()]", " ", printed))
    return {**tool, "version": find(f"({VERSION.pattern})", printed)}


def fields_of(file: Path) -> dict[str, str]:
    """Return a result file's fields: each line is a key, a tab and a value."""
    lines = file.read_text("utf-8", errors="replace").splitlines()
    return {key: value for key, _, value in (line.partition("\t") for line in lines)}


def machine_of(facts: Path) -> Json:
    """Return the machine under test, as an invocation names one."""
    cpuinfo = (facts / "cpuinfo").read_text("utf-8", errors="replace")
    machine: Json = {
        "arch": (facts / "arch").read_text("utf-8").strip(),
        "cpu": find(r"^model name\s*:\s*(.+)$", cpuinfo),
        "cpus": len(re.findall(r"^processor\s*:", cpuinfo, re.MULTILINE)),
        "memory": memory_of(facts / "meminfo"),
    }
    for fact, value in machine.items():
        need(value, f"{facts} names no {fact} of the machine under test")
    return machine


def started_of(file: Path, date: str) -> str:
    """Return when a test started, from the date of its result, taken as UTC."""
    need(DATE.fullmatch(date), f"{file} has no date: {date!r}")
    at = datetime.strptime(date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def entry_of(
    file: Path, fields: dict[str, str], ran: Json, top: str, reason: Sequence[str]
) -> Json:
    """Return a result as an entry, less who ran it and on what machine.

    The test ran on the device its result lies under: nodev is no device,
    for a test that makes its own. One that did not run has its reason.
    """
    device, group = file.parent.parent.name, file.parent.name
    status = fields.get("status")
    verdict = VERDICTS.get(status or "")
    need(verdict, f"{file} has the status {status!r}")
    devices = {} if device == "nodev" else {"TEST_DEVS": f"/dev/{device}"}
    entry: Json = {"tools": ran["tools"], "command": f"check {group}/{file.name}"}
    entry |= {"env": devices, "config": ran["config"], "status": verdict, "exit": 0}
    if verdict == "skip":
        entry["output"] = output_of(reason, top)
    if verdict == "fail":
        exited = int(fields.get("exit_status") or 0)
        printed = printed_in([file.with_name(f"{file.name}.{end}") for end in FAILURES])
        entry["exit"] = exited or 1
        entry["output"] = f"exited with status {exited}\n"
        if any(printed):
            entry["output"] = output_of(printed, top)
    seconds = round(float(fields.get("runtime", "0").removesuffix("s")))
    started = started_of(file, fields.get("date", ""))
    entry["invocations"] = [{"started": started, "seconds": seconds}]
    return entry


def recorded(filed: Path, subject: Json, entry: Json) -> bool:
    """Return whether a record holds a run already, from results read before.

    Its invocation is then in the entry the run equals, with the same
    producer, run and start.
    """
    if not filed.is_file():
        return False

    def named(invocation: Json) -> tuple[str, str, str]:
        return invocation["producer"]["name"], invocation["run"], invocation["started"]

    record: Json = json.loads(filed.read_bytes())
    return record["subject"] == subject and any(
        named(invocation) == named(entry["invocations"][0])
        for known in record["entries"]
        if run_of(known) == run_of(entry)
        for invocation in known["invocations"]
    )


def file_results(args: argparse.Namespace) -> None:
    """File each result under --results, or raise why they leave no record."""
    results: Path = args.results
    config: Path = args.config
    facts = results / "machine"
    lab = run("git", "config", "--global", "--get", "kresults.origin")
    top, _, commit = run("git", "rev-parse", "--show-toplevel", "HEAD").partition("\n")
    need(lab, "git config --global kresults.origin names no lab")
    need(commit, "the working directory is no Git work tree")
    need(config.is_file(), f"{config} does not exist")
    need(results.is_dir(), f"{results} is no directory")
    for fact in map(facts.joinpath, FACTS):
        need(fact.is_file(), f"{fact} does not exist: the run copies it")
    blktests = (facts / "version").read_text("utf-8").strip()
    need(blktests, f"{facts / 'version'} names no blktests")
    if args.producer_tree:
        args.producer_version = version_of(args.producer_tree)
    settings = config.read_bytes()
    subject, name = placed(None, commit, args.project)
    subject["build"] = names = built(config, settings, str(configuration_of(args)))
    directory = args.records / args.project / name
    build = directory.joinpath(*names["configuration"], names["arch"])
    build /= names["compiler"]
    ran: Json = {"tools": [{"name": "blktests", "version": "", "build": blktests}]}
    ran["tools"] += [qemu_of(args.qemu)] if args.qemu else []
    ran["config"] = "sha256:" + hashlib.sha256(settings).hexdigest()
    who = {"producer": {"name": args.producer, "version": args.producer_version}}
    who |= {"origin": lab, "run": args.run}
    where = {"machine": machine_of(facts)}
    found = [(file, fields_of(file)) for file in results_in(results)]
    need(found, f"{results} holds no result")
    # Every result is read before a record is written: one that cannot be
    # read leaves none.
    reasons = reasons_in(results)
    runs: list[tuple[Path, Json, Json]] = []
    for file, fields in found:
        device, test = file.parent.parent.name, f"{file.parent.name}/{file.name}"
        reason = reasons.get((device, test), [])
        # A skip has its reason as output (README.md, "Verdicts").
        if fields.get("status") == "not run" and not any(reason):
            continue
        entry = entry_of(file, fields, ran, top, reason)
        entry["invocations"] = [{**who, **entry["invocations"][0], **where}]
        filed = build / "blktests" / device / f"{test}.json"
        runs.append((filed, {**subject, "test": test}, entry))
    wrote = 0
    for filed, tested, entry in runs:
        if not recorded(filed, tested, entry):
            write(filed, merged(filed, tested, entry))
            wrote += 1
    left = {
        "did not run, and nothing kept their reason": len(found) - len(runs),
        "are recorded already": len(runs) - wrote,
    }
    for why, count in left.items():
        if count:
            sys.stderr.write(f"no record: {count} of {len(found)} results {why}\n")
    if wrote:
        write(build / "config", settings)
        write_base(directory, subject["commit"], name)
        # A path unit on the tree's top sees no write below it: mark the change.
        os.utime(args.records)


def main(argv: Sequence[str] | None = None) -> int:
    summary = (__doc__ or "").partition("\n")[0]
    parser = argparse.ArgumentParser(description=summary, allow_abbrev=False)
    parser.add_argument("--results", type=Path, required=True, metavar="DIR")
    parser.add_argument("--records", type=Path, default=state(), metavar="DIR")
    parser.add_argument("--producer", required=True, metavar="NAME")
    version = parser.add_mutually_exclusive_group(required=True)
    version.add_argument("--producer-version", metavar="VERSION")
    version.add_argument("--producer-tree", type=Path, metavar="DIR")
    parser.add_argument("--run", default="manual", metavar="NAME")
    parser.add_argument("--project", default="linux", metavar="NAME")
    # A test's record lies under the build of the kernel it ran on.
    parser.add_argument("--config", type=Path, required=True, metavar="FILE")
    name = parser.add_mutually_exclusive_group(required=True)
    name.add_argument("--configuration", metavar="NAME")
    name.add_argument("--configuration-file", type=Path, metavar="FILE")
    parser.add_argument("--qemu", type=Path, metavar="FILE")
    try:
        file_results(parser.parse_args(argv))
    except NoRecordError as why:
        sys.stderr.write(f"no record: {why}\n")
    except Exception as error:  # noqa: BLE001
        sys.stderr.write(f"no record: {type(error).__name__}: {error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
