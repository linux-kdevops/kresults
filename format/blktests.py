# SPDX-License-Identifier: copyleft-next-0.3.1
"""File the results of one blktests run as records under --records (README.md).

Run in the tree of the kernel under test, once the run has copied its
results directory with, in it:

- machine/, the facts of the machine under test: arch, cpuinfo, meminfo,
  and version, the `git describe` of the blktests that ran; url, where
  that source can be read, is a tool's `url` when the run copies it;
- NAME.stdout, what check printed for each group or test the run asked
  for, which alone says why a test did not run. Without it such a test
  leaves no record.

    krecord blktests --results DIR --config build/.config --configuration NAME ...

--qemu names the QEMU the machine under test ran on, a tool of each entry.
Print each file written, and how many results leave no record with why.
Exit 0 either way, as krecord run does.
"""

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from record import (
    Json,
    add_build,
    add_producer,
    add_tree,
    build_under,
    built,
    configuration_of,
    digest_of,
    exit_of,
    filed_from,
    installed,
    machine_of,
    made_by,
    merged,
    need,
    output_of,
    parser_of,
    placed,
    printed_in,
    qemu_of,
    recorded,
    touch,
    write,
    write_base,
)

# A result file is named by its test's number alone: 001.full, 001.out.bad
# and 001.dmesg lie beside it.
RESULT = re.compile(r"[0-9]+")
# The files a failed test leaves beside its result, in the output's order.
FAILURES = ("out.bad", "dmesg")
FACTS = ("arch", "cpuinfo", "meminfo", "version")
# A result's path from the results directory ends in the directory of its
# device, its group and its number.
DEPTH = 3
DATE = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")
STAMP = "%Y-%m-%d %H:%M:%S"
# An entry's verdict, by the status of a result.
VERDICTS = {"pass": "pass", "fail": "fail", "not run": "skip"}
# The line check prints of what did not run, on a device or on none: a test,
# with its condition if it has one and its description, or a whole group,
# loop/***, with neither. The lines of its reason follow, indented.
NOT_RUN = re.compile(
    r"(\S+?)(?:/\*\*\*|(/\d+))(?: => (\S+))?"
    r"(?:(?: \(([^()]*)\))? \(.*\))?\s*\[not run\]"
)
INDENT = "    "
# Why each test or group did not run, with the file that says so, by the
# directory of its results and its name.
type Reasons = dict[tuple[str, str], tuple[Path, list[str]]]


@dataclass(frozen=True, slots=True)
class Result:
    """What check left of a test, or printed of a group it did not run."""

    file: Path
    # Where check put the result: the device, or nodev for a test that makes
    # its own, then the condition the test ran under, as in nvme0n1_tr_loop.
    directory: str
    test: str
    fields: dict[str, str]


def results_in(results: Path) -> list[Result]:
    """Return the results under a results directory, in the order of their files.

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
        Result(
            results / file,
            file.parts[-DEPTH],
            "/".join(file.parts[-2:]),
            fields_of(results / file),
        )
        for file in found
        if len(file.parts) >= DEPTH and file.parts[0] != "machine"
    ]


def reasons_in(results: Path) -> Reasons:
    """Return why each test or group did not run, as check printed.

    A group is named without ***. The last file that names a test or a
    group gives its reason.
    """
    reasons: Reasons = {}
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
                # check names the directory as its device, then its condition
                # with a _ for each space and each =.
                postfix = "_" + re.sub("[ =]", "_", found[4]) if found[4] else ""
                directory = (found[3] or "nodev") + postfix
                reasons[directory, found[1] + (found[2] or "")] = file, reason
    return reasons


def groups_in(reasons: Reasons) -> list[Result]:
    """Return the groups check did not run, each as a result.

    check writes no result of such a group: its date is when check last
    wrote the file that names it.
    """
    return [
        Result(file, directory, group, {"status": "not run", "date": date_of(file)})
        for (directory, group), (file, _) in reasons.items()
        if "/" not in group
    ]


def date_of(file: Path) -> str:
    """Return when a file was last written, as a result's date."""
    return datetime.fromtimestamp(file.stat().st_mtime, UTC).strftime(STAMP)


def fields_of(file: Path) -> dict[str, str]:
    """Return a result file's fields: each line is a key, a tab and a value."""
    lines = file.read_text("utf-8", errors="replace").splitlines()
    return {key: value for key, _, value in (line.partition("\t") for line in lines)}


def started_of(file: Path, date: str) -> str:
    """Return when a test started, from the date of its result, taken as UTC."""
    need(DATE.fullmatch(date), f"{file} has no date: {date!r}")
    at = datetime.strptime(date, STAMP).replace(tzinfo=UTC)
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def entry_of(result: Result, ran: Json, top: str, reason: Sequence[str]) -> Json:
    """Return a result as an entry, less who ran it and on what machine.

    The test ran on the device its result lies under: nodev is no device,
    for a test that makes its own. One that did not run has its reason.
    """
    file, fields = result.file, result.fields
    # A condition follows the device's name after a _.
    device = result.directory.partition("_")[0]
    status = fields.get("status")
    verdict = VERDICTS.get(status or "")
    need(verdict, f"{file} has the status {status!r}")
    devices = {} if device == "nodev" else {"TEST_DEVS": f"/dev/{device}"}
    entry: Json = {"tools": ran["tools"], "command": f"check {result.test}"}
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


def file_results(args: argparse.Namespace) -> None:
    """File each result under --results, or raise why they leave no record."""
    results: Path = args.results
    config: Path = args.config
    facts = results / "machine"
    lab, top, commit = filed_from()
    need(config.is_file(), f"{config} does not exist")
    need(results.is_dir(), f"{results} is no directory")
    for fact in map(facts.joinpath, FACTS):
        need(fact.is_file(), f"{fact} does not exist: the run copies it")
    who = made_by(args, lab)
    settings = config.read_bytes()
    subject, name = placed(None, commit, args.project)
    subject["build"] = names = built(config, settings, str(configuration_of(args)))
    directory = args.records / args.project / name
    build = build_under(directory, names)
    ran: Json = {"tools": [installed("blktests", facts)]}
    ran["tools"] += [qemu_of(args.qemu)] if args.qemu else []
    ran["config"] = digest_of(settings)
    where = {"machine": machine_of(facts)}
    reasons = reasons_in(results)
    found = results_in(results) + groups_in(reasons)
    need(found, f"{results} holds no result")
    # Every result is read before a record is written: one that cannot be
    # read leaves none.
    runs: list[tuple[Path, Json, Json]] = []
    for result in found:
        said = reasons.get((result.directory, result.test))
        reason = said[1] if said else []
        # A skip has its reason as output (README.md, "Verdicts").
        if result.fields.get("status") == "not run" and not any(reason):
            continue
        entry = entry_of(result, ran, top, reason)
        entry["invocations"] = [{**who, **entry["invocations"][0], **where}]
        filed = build / "blktests" / result.directory / f"{result.test}.json"
        runs.append((filed, {**subject, "test": result.test}, entry))
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
        touch(args.records)


def main(argv: Sequence[str] | None = None) -> int:
    parser = parser_of(__doc__, "blktests")
    parser.add_argument("--results", type=Path, required=True, metavar="DIR")
    add_tree(parser)
    add_producer(parser)
    # A test's record lies under the build of the kernel it ran on.
    add_build(parser, required=True)
    parser.add_argument("--qemu", type=Path, metavar="FILE")
    args = parser.parse_args(argv)
    return exit_of(lambda: file_results(args), fails=False)
