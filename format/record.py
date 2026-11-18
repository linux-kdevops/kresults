#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""File one run of a command as a record under --records (README.md).

Run as the ExecStopPost= of the transient systemd user unit that ran the
command, in the tree it ran on. The unit's StandardOutput= and StandardError=
append to files, and --output-file names those the record's output is read
from.

    systemd-run --user --unit=NAME --wait -p StandardError=append:FILE \
        -p ExecStopPost='krecord --output-file FILE ...' make

Print each file written, or why the run leaves no record. Exit 0 either way:
a failing ExecStopPost= would change the unit's result.
"""

import argparse
import functools
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
# The machines an arch of two word sizes builds, which the .config's symbols
# name as kbuild's UTS_MACHINE does (README.md). An arch not here builds one.
MACHINES = {
    "x86": (("CONFIG_X86_64=y", "x86_64"), ("CONFIG_X86_32=y", "i386")),
}
# What moves from one run of a command to the next, dropped from a line before
# two outputs are compared (README.md, "Output"): a kernel log's time stamp, a
# code address, and the time diff prints after a file's name.
STAMP = re.compile(r"^\[ *\d+\.\d+\]")
ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")
DIFF_TIME = re.compile(r"^((?:---|\+\+\+) [^\t]*)\t.*")
VERSION = re.compile(r"\d+(?:\.\d+)+")
# A word that is a `git describe` name, as a tool built from its Git tree
# prints for its version: v0.5.0-9187-g5189e3fb.
DESCRIBE = re.compile(r"(?<!\S)\S*-g[0-9a-f]{7,}(?:-dirty)?(?!\S)")
# A Message-ID as the schema allows one (record.schema.json): a series
# whose Message-ID holds a slash is not recorded.
MESSAGE = re.compile(r"[^<>\s/]+@[^<>\s/]+")
# The directory of a project that holds its series, each under its
# Message-ID. No commit takes the name: git describe gives a tag that
# starts with v and a digit, or a hash.
SERIES = "series"


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


def whole(revision: str) -> str:
    """Return a revision's commit, whole, or nothing when it names none."""
    # After --end-of-options a revision that starts with - is no option.
    name = f"{revision}^{{commit}}"
    return run("git", "rev-parse", "--verify", "--end-of-options", name)


def state() -> Path:
    """Return where the records go when no option says: the user's state."""
    home = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(home, "krecords")


def touch(records: Path) -> None:
    """Mark a change of the records, as a path unit on their top sees no write below."""
    os.utime(records)


def filed_from() -> tuple[str, str, str]:
    """Return where a run is filed from: the lab, the tree's top and its commit."""
    lab = run("git", "config", "--global", "--get", "kresults.origin")
    top, _, commit = run("git", "rev-parse", "--show-toplevel", "HEAD").partition("\n")
    need(lab, "git config --global kresults.origin names no lab")
    need(commit, "the working directory is no Git work tree")
    return lab, top, commit


def version_of(tree: Path) -> str:
    """Return what git describe names the work tree that holds a path's real one."""
    need(tree.exists(), f"{tree} does not exist")
    version = run("git", "-C", str(tree.resolve()), "describe", "--always", "--dirty")
    need(version, f"{tree} is in no Git work tree")
    return version


def made_by(args: argparse.Namespace, lab: str) -> Json:
    """Return who made a run, as its invocation says: the producer, the lab, the run.

    The producer's version is what git describe names --producer-tree, given one.
    """
    tree: Path | None = args.producer_tree
    version = version_of(tree) if tree else args.producer_version
    producer = {"name": args.producer, "version": version}
    return {"producer": producer, "origin": lab, "run": args.run}


def configuration_of(args: argparse.Namespace) -> str | None:
    """Return the configuration's name: the one given, or its file's first line."""
    file: Path | None = args.configuration_file
    if file is None:
        return args.configuration
    need(file.is_file(), f"{file} does not exist")
    name = file.read_text("utf-8").partition("\n")[0].strip()
    need(name, f"{file} names no configuration")
    return name


def named(
    text: str,
    configuration: str | None,
    range_: str | None = None,
    file_: str | None = None,
    compiler: str | None = None,
) -> str:
    """Return a text with its placeholders filled.

    {configuration} takes the configuration's name, {range} the range,
    {base} its first commit, {file} the file the command worked on and
    {compiler} the build's compiler. One that nothing given fills would be
    filed as it stands: the run leaves no record.
    """
    fills = {"configuration": configuration, "range": range_, "file": file_}
    fills |= {"base": (range_ or "").partition("..")[0], "compiler": compiler}

    def fill(placeholder: re.Match[str]) -> str:
        value = fills[placeholder[1]]
        need(value, f"{text} holds {placeholder[0]}, which nothing given fills")
        return str(value)

    return re.sub(r"\{(" + "|".join(fills) + r")\}", fill, text)


def range_of(args: argparse.Namespace) -> str | None:
    """Return the commits a tool read as patches, BASE..COMMIT both whole.

    Nothing without --range or --range-file. The option, or the file's first
    line, holds the range as the task took it: BASE..COMMIT, BASE.. for the
    tree's HEAD, or one commit, whose patch is its parent's..its own.
    """
    text: str | None = args.range
    if args.range_file is not None:
        need(args.range_file.is_file(), f"{args.range_file} does not exist")
        text = args.range_file.read_text("utf-8").partition("\n")[0].strip()
        need(text, f"{args.range_file} names no range")
    if text is None:
        return None
    base, dots, tip = text.partition("..")
    if not dots:
        base, tip = text + "^", text
    tip = tip or "HEAD"
    first, last = whole(base), whole(tip)
    need(first and last, f"{text} names no commit")
    need(first != last, f"{text} checks no commit")
    return f"{first}..{last}"


def message_id_of(args: argparse.Namespace) -> str | None:
    """Return the Message-ID of the series a tool took, as it was posted.

    Nothing without --message-id-file. The file holds the Message-ID as
    the task took it, one line, in the schema's own form: a series whose
    Message-ID holds a slash is not recorded.
    """
    if args.message_id_file is None:
        return None
    need(args.message_id_file.is_file(), f"{args.message_id_file} does not exist")
    message = args.message_id_file.read_text("utf-8").partition("\n")[0].strip()
    need(message, f"{args.message_id_file} names no Message-ID")
    need(MESSAGE.fullmatch(message), f"{message} is no Message-ID")
    return message


def file_of(args: argparse.Namespace) -> str | None:
    """Return what the command worked on, as the command names it.

    Nothing without --file. The option holds the name as the task took it:
    a file or an object, a path from the top of the tree, or a name a
    command of make's takes, as SPHINXDIRS.
    """
    path: str | None = args.file
    if path is None:
        return None
    need(path, "--file names no file")
    need(not path.startswith("/"), f"{path} is no path from the top of the tree")
    # A directory names no file: {file}.json under it would be .json.
    need(not path.endswith("/"), f"{path} names no file")
    # The schema allows no . or .. in it either, nor an empty part: one
    # would name a file outside the tree, or the tree's own directory.
    need(
        not re.search(r"(^|/)(\.\.?)?(/|$)", path),
        f"{path} is no path from the top of the tree",
    )
    return path


def arguments_of(args: argparse.Namespace) -> str:
    """Return the arguments --arguments-file adds to the command (docs/deploy.rst)."""
    if args.arguments_file is None:
        return ""
    need(args.arguments_file.is_file(), f"{args.arguments_file} does not exist")
    lines = args.arguments_file.read_text("utf-8").splitlines()
    return "".join(f" {shlex.quote(line)}" for line in lines if line)


def placed(
    checked: str | None,
    commit: str,
    project: str,
    file_: str | None = None,
    message: str | None = None,
) -> tuple[Json, str]:
    """Return the subject of a record and the name of its directory.

    A tool that read a range read the commits, not the working tree: the
    range's last commit names the record, without the -dirty of a tree with
    changes not committed, for the commits it read are the same whatever
    the tree wears. A tool that took a series names it by the Message-ID
    it was posted as, before it applies to any commit.
    """
    describe = ("--tags", "--match", "v[0-9]*", "--abbrev=12", "--always")
    if message:
        return {"project": project, "message_id": message}, f"{SERIES}/{message}"
    if checked:
        tip = checked.partition("..")[2]
        subject: Json = {"project": project, "commit": tip, "range": checked}
        name = run("git", "describe", *describe, tip)
    else:
        subject = {"project": project, "commit": commit}
        name = run("git", "describe", *describe, "--dirty")
    if file_:
        subject["file"] = file_
    return subject, name


@functools.cache
def known() -> Json:
    """Return commands.json, which lies beside this file whatever links to it."""
    return json.loads(Path(__file__).resolve().with_name("commands.json").read_bytes())


def listed(project: str) -> Json:
    """Return what commands.json lists for a project: its commands and their env."""
    projects = known()["projects"]
    need(project in projects, f"commands.json lists no project {project!r}")
    return projects[project]


def record_of(command: str, project: str) -> str:
    """Return the name of a command's record: that of the one argv it matches."""
    names = [
        entry["record"].replace("*", "".join(found.groups()))
        for entry in listed(project)["commands"]
        # An argv's * is the text its record's * stands for: part of one name.
        if (argv := re.escape(entry.get("argv", "")).replace(r"\*", "([^ /]+)"))
        and (found := re.fullmatch(argv, command))
    ]
    count = "more than one record" if names else "no record"
    need(len(names) == 1, f"commands.json names {count} for {command!r}; pass --output")
    return names[0]


def env_of(args: argparse.Namespace, first: str) -> dict[str, str]:
    """Return the variables an entry records, each as set and not empty.

    They are those commands.json lists for the command's first tool, then
    --env's.
    """
    names = dict.fromkeys([*listed(args.project)["env"].get(first, []), *args.env])
    return {name: os.environ[name] for name in names if os.environ.get(name)}


def ran(args: argparse.Namespace, command: str, top: str) -> Json:
    """Return what an entry says ran: the tools, the command and its environment.

    The command's first word is the first tool, and commands.json lists the
    variables of its environment; the tools and variables given follow them.
    """
    # A tool in the tree is named without its directory (README.md, "Tools").
    word = command.partition(" ")[0]
    first = Path(word).name
    need(first, "the command is empty")
    env = env_of(args, first)
    # A tool of the tree is not run for its version: QEMU's configure empties
    # its build directory before it reads an option.
    own = first if not word.startswith("/") and Path(top, word).is_file() else ""
    used = [
        {"name": name, "version": "", "build": "tree"} if name == own else tool(name)
        for name in dict.fromkeys([first, *args.tool])
    ]
    return {"tools": used, "command": command, "env": env}


def find(pattern: str, text: str) -> str:
    """Return the first group of a pattern's first match in a text, or nothing."""
    found = re.search(pattern, text, re.MULTILINE)
    return found[1] if found else ""


def printed_in(files: Sequence[Path]) -> list[str]:
    """Return the lines the unit's command printed to files, in the files' order.

    A file that does not exist holds no line: the command was killed before
    it wrote.
    """
    printed: list[str] = []
    for file in files:
        try:
            text = file.read_bytes().decode(errors="replace")
        except FileNotFoundError:
            continue
        # Only a newline ends a line, and the last one starts no line.
        printed += text.removesuffix("\n").split("\n") if text else []
    return printed


def started_at() -> int:
    """Return when the unit's command started, in seconds since the epoch."""
    # The cgroup's last part is the unit's name.
    name = Path(cgroup()).name
    show = ("systemctl", "--user", "show", "--value", "--timestamp=unix")
    at = name and run(*show, "--property=ExecMainStartTimestamp", "--", name)
    return int(find(r"^@(\d+)$", at) or time.time())


def build_of(path: str, version: str = "") -> str:
    """Return the exact build of a program.

    That is its store path, or its package, or else the `git describe` name
    in the line it prints of its version.
    """
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
    if package:
        return f"{distribution}:{package}"
    described = DESCRIBE.search(version)
    packages = f"no package of {distribution or 'the host'}"
    need(described, f"{path} is in {packages} and prints no git describe")
    return described[0] if described else ""


def tool(name: str) -> Json:
    """Return a tool as an entry names it: its name, version and build."""
    path = shutil.which(name) or ""
    need(path, f"{name} is not on PATH")
    return tool_of(name, path, run(name, "--version").partition("\n")[0])


def tool_of(name: str, path: str, printed: str) -> Json:
    """Return a tool as an entry names it, from the line it prints of its version."""
    versions = VERSION.findall(printed)
    build = build_of(str(Path(path).resolve()), printed)
    return {"name": name, "version": versions[-1] if versions else "", "build": build}


def installed(name: str, directory: Path) -> Json:
    """Return a tool as an entry names it, from the files its install left.

    version holds the `git describe` of its source, the tool's build, and
    url, of an install that left one, where that source can be read.
    """
    version, url = directory / "version", directory / "url"
    need(version.is_file(), f"{version} does not exist")
    build = version.read_text("utf-8").strip()
    need(build, f"{version} names no {name}")
    source = url.read_text("utf-8").strip() if url.is_file() else ""
    found: Json = {"name": name, "version": "", "build": build}
    return found | {"url": source} if source else found


def counted(file: Path, pattern: str) -> int | None:
    """Return the sum of a pattern's numbers in a file, or nothing without one."""
    try:
        found = re.findall(pattern, file.read_text("utf-8"), re.MULTILINE)
    except (OSError, ValueError):
        return None
    return sum(map(int, found)) if found else None


def cgroup() -> str:
    """Return the path of this process's cgroup, the unit's, or nothing."""
    own = SELF_CGROUP.read_text("utf-8") if SELF_CGROUP.is_file() else ""
    return find(r"^0::(/.*)$", own)


def resources() -> Json:
    """Return what the kernel counted of this process's cgroup: the unit's."""
    # Without a cgroup v2 path, CGROUPS alone would be the whole machine's.
    directory = f"{CGROUPS}{cgroup()}"
    used: Json = {}
    for group, members in RESOURCES.items() if directory != str(CGROUPS) else ():
        for member, (file, pattern) in members.items():
            if (total := counted(Path(directory, file), pattern)) is not None:
                seconds = member.endswith("seconds")
                value = round(total / 10**6, 3) if seconds else total
                used.setdefault(group, {})[member] = value
    return {"resources": used} if used else {}


def memory_of(meminfo: Path) -> int:
    """Return a machine's memory in bytes, as its /proc/meminfo counts it."""
    total = find(r"^MemTotal:\s*(\d+) kB$", meminfo.read_text("utf-8"))
    return int(total or 0) * 1024


def machine() -> Json:
    """Return the machine this program runs on, as an invocation names one."""
    return {
        "arch": os.uname().machine,
        "cpu": find(r"^Model name:\s*(.+)$", run("lscpu")),
        "cpus": len(os.sched_getaffinity(0)),
        "memory": memory_of(MEMINFO),
    }


def machine_of(facts: Path) -> Json:
    """Return the machine under test, as an invocation names one."""
    for fact in ("arch", "cpuinfo", "meminfo"):
        need((facts / fact).is_file(), f"{facts / fact} does not exist")
    cpuinfo = (facts / "cpuinfo").read_text("utf-8", errors="replace")
    found: Json = {
        "arch": (facts / "arch").read_text("utf-8").strip(),
        "cpu": find(r"^model name\s*:\s*(.+)$", cpuinfo),
        "cpus": len(re.findall(r"^processor\s*:", cpuinfo, re.MULTILINE)),
        "memory": memory_of(facts / "meminfo"),
    }
    for fact, value in found.items():
        need(value, f"{facts} names no {fact} of the machine under test")
    return found


def qemu_of(file: Path) -> Json:
    """Return the QEMU the machine under test ran on, as an entry names a tool."""
    path = shutil.which(file.resolve()) or ""
    need(path, f"{file} is no program")
    printed = run(path, "--version").partition("\n")[0]
    need(printed, f"{file} prints no version")
    # QEMU prints its version, then in brackets its package or the describe
    # name of its source, which holds a version of its own.
    found = tool_of("qemu", path, re.sub(r"[()]", " ", printed))
    return {**found, "version": find(f"({VERSION.pattern})", printed)}


def invocation(who: Json, started: int) -> Json:
    """Return who ran the command, when and where, and what it used."""
    at = datetime.fromtimestamp(started, UTC)
    return {
        **who,
        "started": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seconds": max(0, int(time.time() - started)),
        **resources(),
        "machine": machine(),
    }


def output_of(printed: Sequence[str], top: str) -> str:
    """Return what a run printed, with no path of the host in it.

    The records may be public: paths lie in the tree, and home is ~.
    """
    output = "\n".join(printed).replace(f"{top}/", "")
    home = re.escape(str(Path.home())) + r"(?![\w.-])"
    return re.sub(home, "~", output) + "\n"


def write(file: Path, data: Json | bytes, kind: str = "record") -> None:
    """Write a file whole, so that no reader finds part of it, and print its name."""
    if not isinstance(data, bytes):
        data = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
    file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=file.parent, delete=False) as temporary:
        temporary.write(data)
    Path(temporary.name).chmod(0o644)
    Path(temporary.name).replace(file)
    sys.stderr.write(f"{kind}: {file}\n")


def merged(path: Path, subject: Json, entry: Json) -> Json:
    """Return the record at path with a run added: to the entry it equals, or as one."""
    record: Json = {"schema": 1, "subject": subject, "entries": []}
    if path.is_file():
        filed: Json = json.loads(path.read_bytes())
        record = filed if filed["subject"] == subject else record
    entries: list[Json] = record["entries"]
    same = [known for known in entries if run_of(known) == run_of(entry)]
    if same:
        same[0]["invocations"] += entry["invocations"]
    else:
        entries.append(entry)
    return record


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


def write_base(directory: Path, commit: str, name: str) -> None:
    """Write the series.json of a commit's directory that holds none.

    A commit that came from no message is its own base.
    """
    if (directory / "series.json").exists():
        return
    series = {"schema": 1, "commit": commit, "message_id": "", "ref": ""}
    series |= {"trees": [], "base": commit, "base_name": name, "base_trees": []}
    series |= {"commits": [], "sources": [], "docs": [], "added": [], "lists": []}
    write(directory / "series.json", series)


def built(config: Path, settings: bytes, configuration: str) -> Json:
    """Return the build a .config names, as a subject and its directories do."""
    text = settings.decode(errors="replace")
    # The .config names the machine built for: its header the arch, and,
    # where an arch builds two word sizes, its symbols the machine, as
    # kbuild's UTS_MACHINE does (README.md). The ARCH the run set
    # changes neither and stays in env.
    srcarch = find(r"^# Linux/(\S+) .*Kernel Configuration$", text)
    arch = next(
        (
            machine
            for symbol, machine in MACHINES.get(srcarch, ())
            if find(rf"^({symbol})$", text)
        ),
        srcarch,
    )
    need(arch, f"{config} names no ARCH")
    compiler = "clang" if find(r"^(CONFIG_CC_IS_CLANG=y)$", text) else "gcc"
    names = {"configuration": sorted(configuration.split("+")), "arch": arch}
    return {**names, "compiler": compiler}


def build_under(directory: Path, build: Json) -> Path:
    """Return the directory of a build, under its commit's."""
    names = (*build["configuration"], build["arch"], build["compiler"])
    return directory.joinpath(*names)


def digest_of(settings: bytes) -> str:
    """Return the hash an entry names a file by: its .config, or its job."""
    return "sha256:" + hashlib.sha256(settings).hexdigest()


def file_run(args: argparse.Namespace) -> None:
    """File the run the environment names, or raise why it leaves no record."""
    environ = os.environ
    # The files take what this program prints too, as it inherits the unit's
    # standard output and error: read them before anything is printed.
    printed = printed_in(args.output_file)
    started = started_at()
    lab, top, commit = filed_from()
    config: Path | None = args.config
    need(config is None or config.is_file(), f"{config} does not exist")
    need(environ.get("EXIT_CODE") == "exited", "the command was stopped")
    configuration = configuration_of(args)
    checked = range_of(args)
    worked = file_of(args)
    who = made_by(args, lab)
    subject, name = placed(checked, commit, args.project, worked, message_id_of(args))
    directory = build = args.records / args.project / name
    entry = ran(
        args,
        named(args.command, configuration, checked, worked) + arguments_of(args),
        top,
    )
    settings = config.read_bytes() if config else b""
    compiler = ""
    if config:
        subject["build"] = names = built(config, settings, str(configuration))
        compiler = names["compiler"]
        # The .config says which compiler built: no producer names it too.
        named_too = any(one["name"] == compiler for one in entry["tools"])
        entry["tools"] += [] if named_too else [tool(compiler)]
        entry["config"] = digest_of(settings)
        build = build_under(directory, names)
    status = int(environ.get("EXIT_STATUS", "0"))
    entry |= {"status": "fail" if status else "pass", "exit": status}
    if any(printed):
        entry["output"] = output_of(printed, top)
    entry["invocations"] = [invocation(who, started)]
    # Without a name given, commands.json names the record of the command.
    given = named(args.output or "", configuration, checked, worked, compiler)
    filed = build / (given or record_of(entry["command"], args.project))
    write(filed, merged(filed, subject, entry))
    if config:
        write(build / "config", settings)
    if "message_id" not in subject:
        # The series names the commit the record lies under, the range's
        # tip where one was checked, not the tree's HEAD.
        write_base(directory, subject["commit"], name)
    touch(args.records)


def parser_of(doc: str | None, name: str) -> argparse.ArgumentParser:
    """Return the parser of a subcommand, described by its docstring's first line."""
    summary = (doc or "").partition("\n")[0]
    return argparse.ArgumentParser(
        prog=f"krecord {name}", description=summary, allow_abbrev=False
    )


def add_tree(parser: argparse.ArgumentParser) -> None:
    """Add the options that name the tree of records and the project in it."""
    parser.add_argument("--records", type=Path, default=state(), metavar="DIR")
    parser.add_argument("--project", default="linux", metavar="NAME")


def add_producer(parser: argparse.ArgumentParser) -> None:
    """Add the options that name who made a run (made_by())."""
    parser.add_argument("--producer", required=True, metavar="NAME")
    version = parser.add_mutually_exclusive_group(required=True)
    version.add_argument("--producer-version", metavar="VERSION")
    version.add_argument("--producer-tree", type=Path, metavar="DIR")
    parser.add_argument("--run", default="manual", metavar="NAME")


def add_build(parser: argparse.ArgumentParser, *, required: bool) -> None:
    """Add the options that name a build: its .config and its configuration."""
    parser.add_argument("--config", type=Path, required=required, metavar="FILE")
    name = parser.add_mutually_exclusive_group(required=required)
    name.add_argument("--configuration", metavar="NAME")
    name.add_argument("--configuration-file", type=Path, metavar="FILE")


def exit_of(work: Callable[[], None], kind: str = "record", *, fails: bool) -> int:
    """Return a subcommand's exit status once it did its work or said why not.

    One that fails exits 1 with the reason. One that does not exits 0 whatever
    went wrong, and says an error it did not expect too: it runs as an
    ExecStopPost=, where a failure would change the unit's result.
    """
    try:
        work()
    except NoRecordError as why:
        sys.stderr.write(f"no {kind}: {why}\n")
        return int(fails)
    except Exception as error:
        if fails:
            raise
        sys.stderr.write(f"no {kind}: {type(error).__name__}: {error}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = parser_of(__doc__, "run")
    add_tree(parser)
    parser.add_argument("--command", required=True)
    parser.add_argument("--output", metavar="NAME")
    add_producer(parser)
    add_build(parser, required=False)
    checked = parser.add_mutually_exclusive_group()
    checked.add_argument("--range", metavar="RANGE")
    checked.add_argument("--range-file", type=Path, metavar="FILE")
    parser.add_argument("--file", metavar="NAME")
    parser.add_argument("--arguments-file", type=Path, metavar="FILE")
    parser.add_argument("--message-id-file", type=Path, metavar="FILE")
    parser.add_argument("--tool", action="append", default=[], metavar="NAME")
    parser.add_argument("--env", action="append", default=[], metavar="VAR")
    parser.add_argument(
        "--output-file", type=Path, action="append", default=[], metavar="FILE"
    )
    args = parser.parse_args(argv)
    if args.config and not (args.configuration or args.configuration_file):
        parser.error("--config needs --configuration or --configuration-file")
    ranged = args.range is not None or args.range_file
    if args.config and ranged:
        parser.error(
            "--config names a build of the working tree, and "
            "--range or --range-file commits apart from it"
        )
    if args.config and args.message_id_file:
        parser.error(
            "--config names a build of the working tree, and "
            "--message-id-file a series apart from it"
        )
    if ranged and args.message_id_file:
        parser.error(
            "--range or --range-file names commits a tool read, and "
            "--message-id-file a series it took"
        )
    return exit_of(lambda: file_run(args), fails=False)


if __name__ == "__main__":
    sys.exit(main())
