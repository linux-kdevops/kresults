#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""File one run of a command as a record under --records (README.md).

Run as the ExecStopPost= of the transient systemd user unit that ran the
command, in the tree it ran on. The journal holds what the command printed.

    systemd-run --user --wait -p ExecStopPost='krecord --records DIR ...' make

Print each file written, or why the run leaves no record. Exit 0 either way:
a failing ExecStopPost= would change the unit's result.
"""

import argparse
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

type Json = dict[str, Any]

OS_RELEASE = Path("/etc/os-release")
MEMINFO = Path("/proc/meminfo")
SELF_CGROUP = Path("/proc/self/cgroup")
CGROUPS = Path("/sys/fs/cgroup")
# An invocation's resources: each value by the file of the cgroup that holds
# it and the pattern of its number there, summed over the lines that match.
ONLY = r"^(\d+)$"
SOME = r"^some .*\btotal=(\d+)$"
RESOURCES = {
    "cpu": {
        "seconds": ("cpu.stat", r"^usage_usec (\d+)$"),
        "user_seconds": ("cpu.stat", r"^user_usec (\d+)$"),
        "system_seconds": ("cpu.stat", r"^system_usec (\d+)$"),
        "stalled_seconds": ("cpu.pressure", SOME),
    },
    "memory": {
        "peak": ("memory.peak", ONLY),
        "swap_peak": ("memory.swap.peak", ONLY),
        "stalled_seconds": ("memory.pressure", SOME),
        "high_events": ("memory.events", r"^high (\d+)$"),
        "max_events": ("memory.events", r"^max (\d+)$"),
        "oom_kills": ("memory.events", r"^oom_kill (\d+)$"),
        "major_faults": ("memory.stat", r"^pgmajfault (\d+)$"),
    },
    "io": {
        "stalled_seconds": ("io.pressure", SOME),
        "read_bytes": ("io.stat", r"\brbytes=(\d+)"),
        "write_bytes": ("io.stat", r"\bwbytes=(\d+)"),
        "read_operations": ("io.stat", r"\brios=(\d+)"),
        "write_operations": ("io.stat", r"\bwios=(\d+)"),
    },
    "tasks": {"peak": ("pids.peak", ONLY)},
}
# The names kbuild takes as ARCH: each directory of arch/, then each name its
# top Makefile maps to one.
ARCHES = frozenset(
    {
        "alpha",
        "arc",
        "arm",
        "arm64",
        "csky",
        "hexagon",
        "loongarch",
        "m68k",
        "microblaze",
        "mips",
        "nios2",
        "openrisc",
        "parisc",
        "powerpc",
        "riscv",
        "s390",
        "sh",
        "sparc",
        "um",
        "x86",
        "xtensa",
        "i386",
        "parisc64",
        "sparc32",
        "sparc64",
        "x86_64",
    }
)
COMPILERS = frozenset({"clang", "gcc"})
# What moves from one run of a command to the next, dropped from a line before
# two outputs are compared (README.md, "Output"): a kernel log's time stamp, a
# code address, and the time diff prints after a file's name.
STAMP = re.compile(r"^\[ *\d+\.\d+\]")
ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")
DIFF_TIME = re.compile(r"^((?:---|\+\+\+) [^\t]*)\t.*")
VERSION = re.compile(r"\d+(?:\.\d+)+")
# syslog's levels by name, each at its number.
PRIORITIES = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")


class NoRecordError(Exception):
    """Why a run leaves no record."""


def need(holds: object, why: str) -> None:
    """Leave no record, and say why, unless a condition holds."""
    if not holds:
        raise NoRecordError(why)


def unmoved(line: str) -> str:
    """Return a line of an output less what moves from one run to the next."""
    return DIFF_TIME.sub(r"\1", ADDRESS.sub("", STAMP.sub("", line)))


def lines(output: str) -> list[str]:
    """Return an output's lines in a fixed order, less what moves between runs."""
    return sorted(map(unmoved, output.splitlines()))


def run_of(entry: Mapping[str, object]) -> str:
    """Return what is equal in two entries of one run: all but who ran it."""
    output = lines(str(entry.get("output", "")))
    return json.dumps({**entry, "output": output, "invocations": []}, sort_keys=True)


def run(*command: str) -> str:
    """Return what a command printed, or nothing when it failed."""
    # lscpu and the tools name their fields and versions in the C locale.
    environ = {**os.environ, "LC_ALL": "C"}
    done = subprocess.run(  # noqa: S603
        command, capture_output=True, check=False, env=environ
    )
    return done.stdout.decode(errors="replace").strip() if not done.returncode else ""


def state() -> Path:
    """Return where the records go when no option says: the user's state."""
    home = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(home, "krecords")


def version_of(tree: Path) -> str:
    """Return what git describe names the work tree that holds a path's real one."""
    need(tree.exists(), f"{tree} does not exist")
    version = run("git", "-C", str(tree.resolve()), "describe", "--always", "--dirty")
    need(version, f"{tree} is in no Git work tree")
    return version


def configuration_of(args: argparse.Namespace) -> str | None:
    """Return the configuration's name: the one given, or its file's first line."""
    file: Path | None = args.configuration_file
    if file is None:
        return args.configuration
    need(file.is_file(), f"{file} does not exist")
    name = file.read_text("utf-8").partition("\n")[0].strip()
    need(name, f"{file} names no configuration")
    return name


def named(text: str, configuration: str | None) -> str:
    """Return a text with the configuration's name where it has {configuration}."""
    return text.replace("{configuration}", configuration) if configuration else text


@functools.cache
def known() -> Json:
    """Return commands.json, which lies beside this file whatever links to it."""
    return json.loads(Path(__file__).resolve().with_name("commands.json").read_bytes())


def record_of(command: str) -> str:
    """Return the name of a command's record: that of the one argv it matches."""
    names = [
        entry["record"].replace("*", "".join(found.groups()))
        for entry in known()["commands"]
        # An argv's * is the text its record's * stands for: part of one name.
        if (argv := re.escape(entry.get("argv", "")).replace(r"\*", "([^ /]+)"))
        and (found := re.fullmatch(argv, command))
    ]
    count = "more than one record" if names else "no record"
    need(len(names) == 1, f"commands.json names {count} for {command!r}; pass --output")
    return names[0]


def ran(command: str, tools: Sequence[str], variables: Sequence[str]) -> Json:
    """Return what an entry says ran: the tools, the command and its environment.

    The command's first word is the first tool, and commands.json lists the
    variables of its environment; the tools and variables given follow them.
    """
    # A tool in the tree is named without its directory (README.md, "Tools").
    first = Path(command.partition(" ")[0]).name
    need(first, "the command is empty")
    names = dict.fromkeys([*known()["env"].get(first, []), *variables])
    env = {name: os.environ[name] for name in names if os.environ.get(name)}
    used = list(map(tool, dict.fromkeys([first, *tools])))
    return {"tools": used, "command": command, "env": env}


def find(pattern: str, text: str) -> str:
    """Return the first group of a pattern's first match in a text, or nothing."""
    found = re.search(pattern, text, re.MULTILINE)
    return found[1] if found else ""


def message_of(field: object) -> str:
    """Return a journal entry's line, which is a list of bytes when not UTF-8."""
    if isinstance(field, list):
        return bytes(cast("list[int]", field)).decode(errors="replace")
    return field if isinstance(field, str) else ""


def journal(invocation_id: str, level: int | None = None) -> tuple[list[str], int]:
    """Return the lines a unit's command printed, and its start in microseconds.

    With a level, only the lines at that priority or a more severe one.
    """
    run("journalctl", "--sync")
    query = ("journalctl", "--user", "--no-pager", "--output=json")
    unit = f"INVOCATION_ID={invocation_id}"
    found = run(*query, f"_SYSTEMD_{unit}", "+", f"USER_{unit}")
    entries: list[Json] = [json.loads(line) for line in found.splitlines()]
    printed = [
        message_of(entry.get("MESSAGE"))
        for entry in entries
        if entry.get("_SYSTEMD_INVOCATION_ID") == invocation_id
        # A line the journal gives no priority is at info.
        and (level is None or int(entry.get("PRIORITY", "6")) <= level)
    ]
    # The manager's start message has the time; without one, the first line's.
    by_manager = [entry for entry in entries if entry.get("JOB_TYPE") == "start"]
    first = (by_manager or entries or [{}])[0]
    return printed, int(first.get("__REALTIME_TIMESTAMP", time.time_ns() // 1000))


def build_of(path: str) -> str:
    """Return the exact build of a program: its store path, or its package."""
    if path.startswith("/nix/store/"):
        return "/".join(path.split("/")[:4])
    release = OS_RELEASE.read_text("utf-8") if OS_RELEASE.is_file() else ""
    distribution = find(r'^ID="?([^"\n]*)', release)
    if distribution == "fedora":
        form = "%{NAME} %{VERSION}-%{RELEASE}"
        package = run("rpm", "--query", "--file", path, "--queryformat", form)
    else:
        owner = run("dpkg-query", "--search", path).partition(":")[0]
        form = "--showformat=${Package} ${Version}"
        package = owner and run("dpkg-query", "--show", form, owner)
    need(package, f"no package of {distribution or 'the host'} holds {path}")
    return f"{distribution}:{package}"


def tool(name: str) -> Json:
    """Return a tool as an entry names it: its name, version and build."""
    path = shutil.which(name) or ""
    need(path, f"{name} is not on PATH")
    versions = VERSION.findall(run(name, "--version").partition("\n")[0])
    build = build_of(str(Path(path).resolve()))
    return {"name": name, "version": versions[-1] if versions else "", "build": build}


def counted(file: Path, pattern: str) -> int | None:
    """Return the sum of a pattern's numbers in a file, or nothing without one."""
    try:
        found = re.findall(pattern, file.read_text("utf-8"), re.MULTILINE)
    except (OSError, ValueError):
        return None
    return sum(map(int, found)) if found else None


def resources() -> Json:
    """Return what the kernel counted of this process's cgroup: the unit's."""
    own = SELF_CGROUP.read_text("utf-8") if SELF_CGROUP.is_file() else ""
    # Without a cgroup v2 path, CGROUPS alone would be the whole machine's.
    directory = f"{CGROUPS}{find(r'^0::(/.*)$', own)}"
    used: Json = {}
    for group, members in RESOURCES.items() if directory != str(CGROUPS) else ():
        for member, (file, pattern) in members.items():
            if (total := counted(Path(directory, file), pattern)) is not None:
                seconds = member.endswith("seconds")
                value = round(total / 10**6, 3) if seconds else total
                used.setdefault(group, {})[member] = value
    return {"resources": used} if used else {}


def invocation(args: argparse.Namespace, lab: str, started: int) -> Json:
    """Return who ran the command, when and where, and what it used."""
    at = datetime.fromtimestamp(started // 10**6, UTC)
    memory = find(r"^MemTotal:\s*(\d+) kB$", MEMINFO.read_text("utf-8"))
    return {
        "producer": {"name": args.producer, "version": args.producer_version},
        "origin": lab,
        "run": args.run,
        "started": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seconds": max(0, int(time.time() - started / 10**6)),
        **resources(),
        "machine": {
            "arch": os.uname().machine,
            "cpu": find(r"^Model name:\s*(.+)$", run("lscpu")),
            "cpus": len(os.sched_getaffinity(0)),
            "memory": int(memory or 0) * 1024,
        },
    }


def write(file: Path, data: Json | bytes) -> None:
    """Write a file whole, so that no reader finds part of it, and print its name."""
    if not isinstance(data, bytes):
        data = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
    file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=file.parent, delete=False) as temporary:
        temporary.write(data)
    Path(temporary.name).chmod(0o644)
    Path(temporary.name).replace(file)
    sys.stderr.write(f"record: {file}\n")


def merged(path: Path, subject: Json, entry: Json) -> Json:
    """Return the record at path with a run added: to the entry it equals, or as one."""
    record: Json = {"schema": 1, "subject": subject, "entries": []}
    if path.is_file():
        record = json.loads(path.read_bytes())
    entries: list[Json] = record["entries"]
    same = [known for known in entries if run_of(known) == run_of(entry)]
    if same:
        same[0]["invocations"] += entry["invocations"]
    else:
        entries.append(entry)
    return record


def file_run(args: argparse.Namespace) -> None:
    """File the run the environment names, or raise why it leaves no record."""
    environ = os.environ
    level = PRIORITIES.index(args.priority) if args.priority else None
    printed, started = journal(environ.get("INVOCATION_ID", ""), level)
    lab = run("git", "config", "--global", "--get", "kresults.origin")
    top, _, commit = run("git", "rev-parse", "--show-toplevel", "HEAD").partition("\n")
    config: Path | None = args.config
    need(lab, "git config --global kresults.origin names no lab")
    need(commit, "the working directory is no Git work tree")
    need(config is None or config.is_file(), f"{config} does not exist")
    need(environ.get("EXIT_CODE") == "exited", "the command was stopped")
    configuration = configuration_of(args)
    if args.producer_tree:
        args.producer_version = version_of(args.producer_tree)
    describe = ("--tags", "--match", "v[0-9]*", "--abbrev=12", "--always", "--dirty")
    name = run("git", "describe", *describe)
    directory = build = args.records / args.project / name
    subject: Json = {"project": args.project, "commit": commit}
    entry = ran(named(args.command, configuration), args.tool, args.env)
    settings = config.read_bytes() if config else b""
    if config:
        text = settings.decode(errors="replace")
        # kbuild takes the ARCH the run set before the host's (README.md).
        header = r"^# Linux/(\S+) .*Kernel Configuration$"
        arch = environ.get("ARCH") or find(header, text)
        need(arch, f"{config} names no ARCH")
        compiler = "clang" if find(r"^(CONFIG_CC_IS_CLANG=y)$", text) else "gcc"
        names = {"configuration": [configuration], "arch": arch}
        subject["build"] = {**names, "compiler": compiler}
        # The .config says which compiler built: no producer names it too.
        named_too = any(one["name"] == compiler for one in entry["tools"])
        entry["tools"] += [] if named_too else [tool(compiler)]
        entry["config"] = "sha256:" + hashlib.sha256(settings).hexdigest()
        build = directory / str(configuration) / arch / compiler
    status = int(environ.get("EXIT_STATUS", "0"))
    entry |= {"status": "fail" if status else "pass", "exit": status}
    if printed:
        # The records may be public: paths lie in the tree, and home is ~.
        output = "\n".join(printed).replace(f"{top}/", "")
        home = re.escape(str(Path.home())) + r"(?![\w.-])"
        entry["output"] = re.sub(home, "~", output) + "\n"
    entry["invocations"] = [invocation(args, lab, started)]
    # Without a name given, commands.json names the record of the command.
    given = named(args.output or "", configuration)
    filed = build / (given or record_of(entry["command"]))
    write(filed, merged(filed, subject, entry))
    if config:
        write(build / "config", settings)
    if not (directory / "series.json").exists():
        # A commit that came from no message is its own base.
        series = {"schema": 1, "commit": commit, "message_id": "", "ref": ""}
        series |= {"trees": [], "base": commit, "base_name": name, "base_trees": []}
        series |= {"commits": [], "sources": [], "docs": [], "added": [], "lists": []}
        write(directory / "series.json", series)
    # A path unit on the tree's top sees no write below it: mark the change.
    os.utime(args.records)


def main(argv: Sequence[str] | None = None) -> int:
    summary = (__doc__ or "").partition("\n")[0]
    parser = argparse.ArgumentParser(description=summary, allow_abbrev=False)
    parser.add_argument("--records", type=Path, default=state(), metavar="DIR")
    for option in ("command", "producer"):
        parser.add_argument(f"--{option}", required=True)
    parser.add_argument("--output", metavar="NAME")
    version = parser.add_mutually_exclusive_group(required=True)
    version.add_argument("--producer-version", metavar="VERSION")
    version.add_argument("--producer-tree", type=Path, metavar="DIR")
    parser.add_argument("--run", default="manual", metavar="NAME")
    parser.add_argument("--project", default="linux", metavar="NAME")
    parser.add_argument("--config", type=Path, metavar="FILE")
    name = parser.add_mutually_exclusive_group()
    name.add_argument("--configuration", metavar="NAME")
    name.add_argument("--configuration-file", type=Path, metavar="FILE")
    parser.add_argument("--tool", action="append", default=[], metavar="NAME")
    parser.add_argument("--env", action="append", default=[], metavar="VAR")
    parser.add_argument("--priority", choices=PRIORITIES, metavar="LEVEL")
    args = parser.parse_args(argv)
    if args.config and not (args.configuration or args.configuration_file):
        parser.error("--config needs --configuration or --configuration-file")
    try:
        file_run(args)
    except NoRecordError as why:
        sys.stderr.write(f"no record: {why}\n")
    except Exception as error:  # noqa: BLE001
        sys.stderr.write(f"no record: {type(error).__name__}: {error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
