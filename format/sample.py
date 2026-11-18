# SPDX-License-Identifier: copyleft-next-0.3.1
"""File one run a machine under test made as a record under --records (README.md).

Run in the tree of the kernel that ran, once the run is over, with what
krecord run reads of its unit and its host given instead:

    krecord sample --command 'fio atomic-merge.fio' \
        --output fio/nvme0n1/atomic-merge.json --exit 0 \
        --started 2026-11-18T12:00:00Z --seconds 60 \
        --machine DIR --install DIR --config build/.config ...

--machine names the facts of the machine under test: arch, cpuinfo and
meminfo. --install names the directory the install of the command's tool
left: version, the `git describe` of its source, and url, where that
source can be read. --qemu names the QEMU the machine ran on, a second
tool. --env names a variable the command read, as this program's
environment sets it.

--metrics names a file of the numbers the run measured, one a line: its
name, its value, its unit and higher or lower, the way it improves, with
a tab between them. --job names the job file the command ran. Print each
file written, or why the run leaves no record. Exit 0 either way, as
krecord run does.
"""

import argparse
import math
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from record import (
    Json,
    NoRecordError,
    add_build,
    add_producer,
    add_tree,
    build_under,
    built,
    configuration_of,
    digest_of,
    env_of,
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

STARTED = "%Y-%m-%dT%H:%M:%SZ"
NUMBER = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?")
BETTER = ("higher", "lower")
# A line of --metrics: a name, a value, a unit and a way.
FIELDS = 4


def metrics_in(file: Path) -> list[Json]:
    """Return the numbers a file holds, as an invocation's metrics."""
    need(file.is_file(), f"{file} does not exist")
    metrics: dict[str, Json] = {}
    for number, line in enumerate(file.read_text("utf-8").splitlines(), 1):
        where = f"{file}:{number}"
        fields = line.split("\t")
        need(len(fields) == FIELDS, f"{where} is not four fields between tabs")
        name, text, unit, better = fields
        need(name, f"{where} names no metric")
        need(name not in metrics, f"{where} names {name} again")
        need(NUMBER.fullmatch(text), f"{where}: {text!r} is no number")
        value = float(text) if re.search("[.eE]", text) else int(text)
        need(math.isfinite(value), f"{where}: {text} is no number a record holds")
        need(better in BETTER, f"{where}: {better!r} is neither higher nor lower")
        metrics[name] = {"name": name, "value": value, "unit": unit, "better": better}
    return list(metrics.values())


def file_sample(args: argparse.Namespace) -> None:
    """File the run the options name, or raise why it leaves no record."""
    lab, top, commit = filed_from()
    config: Path = args.config
    job: Path | None = args.job
    need(config.is_file(), f"{config} does not exist")
    need(job is None or job.is_file(), f"{job} does not exist")
    need(min(args.exit, args.seconds) >= 0, "--exit and --seconds are not negative")
    try:
        datetime.strptime(args.started, STARTED)  # noqa: DTZ007
    except ValueError:
        why = f"--started {args.started} is no time in UTC, as 2026-11-18T12:00:00Z"
        raise NoRecordError(why) from None
    who = made_by(args, lab)
    settings = config.read_bytes()
    subject, name = placed(None, commit, args.project)
    subject["build"] = names = built(config, settings, str(configuration_of(args)))
    directory = args.records / args.project / name
    build = build_under(directory, names)
    # A tool in the tree is named without its directory (README.md, "Tools").
    first = Path(args.command.partition(" ")[0]).name
    need(first, "the command is empty")
    tools = [installed(first, args.install)]
    tools += [qemu_of(args.qemu)] if args.qemu else []
    entry: Json = {"tools": tools, "command": args.command}
    entry |= {"env": env_of(args, first), "config": digest_of(settings)}
    entry |= {"job": digest_of(job.read_bytes())} if job else {}
    entry |= {"status": "fail" if args.exit else "pass", "exit": args.exit}
    printed = printed_in(args.output_file)
    if any(printed):
        entry["output"] = output_of(printed, top)
    metrics = metrics_in(args.metrics) if args.metrics else []
    invocation = {**who, "started": args.started, "seconds": args.seconds}
    invocation |= {"metrics": metrics} if metrics else {}
    entry["invocations"] = [invocation | {"machine": machine_of(args.machine)}]
    filed = build / args.output
    need(not recorded(filed, subject, entry), f"{filed} holds the run already")
    write(filed, merged(filed, subject, entry))
    write(build / "config", settings)
    write_base(directory, subject["commit"], name)
    touch(args.records)


def main(argv: Sequence[str] | None = None) -> int:
    parser = parser_of(__doc__, "sample")
    add_tree(parser)
    parser.add_argument("--command", required=True)
    parser.add_argument("--output", required=True, metavar="NAME")
    add_producer(parser)
    # A run's record lies under the build of the kernel it ran on.
    add_build(parser, required=True)
    # What krecord run reads of its unit, and of the host it runs on.
    parser.add_argument("--exit", type=int, required=True, metavar="STATUS")
    parser.add_argument("--started", required=True, metavar="TIME")
    parser.add_argument("--seconds", type=int, required=True, metavar="SECONDS")
    parser.add_argument("--machine", type=Path, required=True, metavar="DIR")
    parser.add_argument("--install", type=Path, required=True, metavar="DIR")
    parser.add_argument("--qemu", type=Path, metavar="FILE")
    parser.add_argument("--env", action="append", default=[], metavar="VAR")
    parser.add_argument(
        "--output-file", type=Path, action="append", default=[], metavar="FILE"
    )
    parser.add_argument("--metrics", type=Path, metavar="FILE")
    parser.add_argument("--job", type=Path, metavar="FILE")
    args = parser.parse_args(argv)
    return exit_of(lambda: file_sample(args), fails=False)
