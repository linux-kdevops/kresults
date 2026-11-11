#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
# /// script
# requires-python = ">=3.11"
# dependencies = ["jsonschema"]
# ///
"""Check the records under --records against the format (README.md).

Print each file that breaks it, with why, and exit 1 if any does.

    validate.py --records ../records
"""

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

from jsonschema import Draft7Validator, ValidationError, validators
from jsonschema.protocols import Validator
from record import ARCHES, COMPILERS, run_of

# The fields the checks read, which a file has once its schema passes it.


class Build(TypedDict):
    configuration: list[str]
    arch: str
    compiler: str


class TreeSubject(TypedDict):
    project: str
    commit: str
    build: NotRequired[Build]
    file: NotRequired[str]
    test: NotRequired[str]


class SeriesSubject(TypedDict):
    project: str
    message_id: str


class Entry(TypedDict):
    command: str
    env: dict[str, str]
    config: NotRequired[str]
    status: str
    exit: int
    output: NotRequired[str]


class Record(TypedDict):
    subject: TreeSubject | SeriesSubject
    entries: list[Entry]


class Series(TypedDict):
    commit: str
    base: str
    commits: list[dict[str, str]]


class Objects(TypedDict):
    commit: str
    build: Build


# The versions this validator knows. A file of any other is refused, so a
# reader learns a version before a producer writes it (CHANGES.md).
KNOWN = frozenset({1})
# The abbreviated hash in the name git describe gives a commit that no tag
# names: all of the name, or what follows a tag and a count.
HASH = re.compile(r"(?:.+-\d+-g)?([0-9a-f]{12,})")
# A home directory where a path starts, or right after an option as in
# -I/home/x. Inside a longer path, home is a directory of the tree.
HOME = re.compile(r"(?<![\w.~])(?:-\w+)?/(?:home|Users)/[^/\s]")


def match_pattern(
    _validator: object, pattern: str, instance: object, _schema: object
) -> Iterator[ValidationError]:
    """Yield an error for a string that a pattern of its schema does not match.

    The $ of JSON Schema matches at the end of a string alone. The $ of re
    matches before a last line break too, and would pass a commit that ends in
    one.
    """
    at_the_end = re.sub(r"(?<!\\)\$", r"\\Z", pattern)
    if isinstance(instance, str) and not re.search(at_the_end, instance):
        yield ValidationError(f"{instance!r} does not match {pattern!r}")


Draft7 = cast(
    "type[Validator]",
    # The stubs of jsonschema give extend() no types.
    validators.extend(  # pyright: ignore[reportUnknownMemberType]
        Draft7Validator, {"pattern": match_pattern}
    ),
)
HERE = Path(__file__).parent
SCHEMAS: dict[str, Validator] = {
    kind: Draft7(json.loads((HERE / f"{kind}.schema.json").read_text("utf-8")))
    for kind in ("record", "series", "objects")
}


# The fields the checks read of a line of commands.json.
class Command(TypedDict):
    record: str
    under: str


def names_of(record: str) -> re.Pattern[str]:
    """Return what matches a record's name: * is a name or part of one, ** a path."""
    path = re.escape(record).replace(r"\*\*", "[^/]+(?:/[^/]+)*")
    return re.compile(path.replace(r"\*", "[^/]+"))


COMMANDS: list[Command] = json.loads((HERE / "commands.json").read_bytes())["commands"]
RECORDS = [(names_of(command["record"]), command) for command in COMMANDS]
# What a record lies under, by the name commands.json gives it in "under".
UNDER = {"build": "a build", "commit": "the commit", "series": "a series"}


def closest(error: ValidationError) -> Iterator[ValidationError]:
    """Yield the errors of the alternative of a oneOf that a value is closest to.

    A value that fits no alternative fails each one, and the error of the
    oneOf prints only the value. The closest alternative has the fewest errors
    on the value itself, as a missing field is, and not on a field of it.
    """
    if not error.context:
        yield error
        return
    alternatives = groupby(error.context, lambda inner: inner.relative_schema_path[0])
    failures = [list(errors) for _, errors in alternatives]
    on_value = [sum(not inner.relative_path for inner in errors) for errors in failures]
    for inner in failures[on_value.index(min(on_value))]:
        yield from closest(inner)


@dataclass(frozen=True, slots=True)
class Place:
    """Where a file lies: under a series, a commit, or a build of a commit."""

    project: str
    # The directory of the commit, or of the series, whose name holds an @.
    name: str
    # The build's CONFIGURATION/ARCH/COMPILER; empty outside a build.
    build: tuple[str, ...]
    # The path below the build, or else below the commit or the series.
    below: tuple[str, ...]

    @property
    def series(self) -> bool:
        return "@" in self.name

    @property
    def level(self) -> str:
        return "series" if self.series else "build" if self.build else "commit"


def locate(parts: tuple[str, ...]) -> Place | None:
    """Return where a file lies, or None for one under no commit or series."""
    if not parts[2:]:
        return None
    project, name, *rest = parts
    if "@" not in name:
        # A configuration is one directory or more, and a file lies below the
        # compiler.
        for at in range(1, len(rest) - 2):
            if rest[at] in ARCHES and rest[at + 1] in COMPILERS:
                build, below = rest[: at + 2], rest[at + 2 :]
                return Place(project, name, tuple(build), tuple(below))
    return Place(project, name, (), tuple(rest))


def kind_of(place: Place) -> str:
    """Return which schema a JSON file follows, by where it lies."""
    if place.build and place.below == ("objects.json",):
        return "objects"
    if not place.series and not place.build and place.below == ("series.json",):
        return "series"
    return "record"


def is_named(place: Place) -> bool:
    """Return whether the format names a file that is not JSON, where it lies."""
    if place.series:
        return place.below == ("b4", "mbox.mbx")
    return bool(place.build) and place.below == ("config",)


def is_record(file: Path, place: Place) -> bool:
    named = file.name.endswith(".json") and kind_of(place) == "record"
    return named and not file.is_symlink()


def command_of(place: Place, level: str) -> Command | None:
    """Return the command whose record, under a level, has the name of a file."""
    path = "/".join(place.below)
    found = (c for names, c in RECORDS if c["under"] == level and names.fullmatch(path))
    return next(found, None)


def ran_in(env: dict[str, str], arch: str, compiler: str) -> bool:
    """Return whether an entry's ARCH and LLVM name the build it lies under.

    make compiles with clang under any LLVM that is not empty, and with gcc
    under none. An entry that sets no ARCH names no build: kbuild took the
    host's, or the tool is not make.
    """
    named = "clang" if env.get("LLVM") else "gcc"
    return not env.get("ARCH") or (env["ARCH"], named) == (arch, compiler)


def check_commit(place: Place, sha: str) -> Iterator[str]:
    """Yield the problem of a directory named for another commit.

    A tag tells nothing of its commit, so a directory named for one passes.
    """
    found = HASH.fullmatch(place.name)
    if found and not sha.startswith(found[1]):
        yield f"lies under {place.name}/, which does not name {sha}"


def check_build(place: Place, build: Build) -> Iterator[str]:
    """Yield how the build a file names differs from the one it lies under."""
    configuration = build["configuration"]
    directories = (*configuration, build["arch"], build["compiler"])
    if configuration != sorted(configuration):
        yield "names its configuration out of order; sort it by name"
    # A path names a build by its ARCH, so one of another ARCH lies under none.
    if build["arch"] not in ARCHES:
        yield f"names {build['arch']}, which is no ARCH of kbuild's"
    elif directories != place.build:
        yield f"lies outside its build, {'/'.join(directories)}/"


def check_tree(place: Place, subject: TreeSubject) -> Iterator[str]:
    yield from check_commit(place, subject["commit"])
    if "build" in subject:
        yield from check_build(place, subject["build"])
    elif place.build:
        yield "lies under a build that its subject does not name"
    # A record is named for its tool, then for what the tool was run on.
    path = "/".join(place.below)
    for target in (subject.get("file"), subject.get("test")):
        if target is not None and not path.endswith(f"/{target}.json"):
            yield f"is not named for {target}"


def check_name(place: Place) -> Iterator[str]:
    """Yield the problem of a record that commands.json lists elsewhere or nowhere."""
    levels = [level for level in UNDER if command_of(place, level)]
    if not levels:
        yield "is of no known command; add its line to commands.json"
    elif place.level not in levels:
        here, there = UNDER[place.level], UNDER[levels[0]]
        yield f"lies under {here}, and commands.json has it under {there}"


def check_entry(place: Place, entry: Entry) -> Iterator[str]:
    """Yield what an entry breaks, to be told after its number."""
    if place.build and "config" not in entry:
        yield "has no config, and lies under a build"
    if not place.build and "config" in entry:
        yield "has a config, and lies under no build"
    if place.build and not ran_in(entry["env"], *place.build[-2:]):
        yield "sets an ARCH or LLVM its build does not name"
    texts = (entry["command"], entry.get("output", ""), *entry["env"].values())
    if any(HOME.search(text) for text in texts):
        yield "names a home directory; write it as ~"
    if entry["status"] == "pass" and entry["exit"]:
        yield f"is a pass, and its exit status is {entry['exit']}"
    if entry["status"] == "skip" and not entry.get("output"):
        yield "is a skip, and has no output to give its reason"


def check_entries(place: Place, entries: list[Entry]) -> Iterator[str]:
    runs: dict[str, int] = {}
    for number, entry in enumerate(entries, 1):
        if (first := runs.setdefault(run_of(entry), number)) != number:
            yield f"entry {number} is the run of entry {first}; add to its invocations"
        for problem in check_entry(place, entry):
            yield f"entry {number} {problem}"


def check_record(place: Place, record: Record) -> Iterator[str]:
    subject = record["subject"]
    if place.project != subject["project"]:
        yield f"lies under {place.project}/, not {subject['project']}/"
    if "message_id" not in subject:
        if place.series:
            yield "lies under a series, and its subject is a commit"
        else:
            yield from check_tree(place, subject)
    elif not place.series:
        yield "lies under a commit, and its subject is a series"
    elif place.name != subject["message_id"]:
        yield f"lies under {place.name}/, not {subject['message_id']}/"
    yield from check_entries(place, record["entries"])


def check_series(place: Place, series: Series) -> Iterator[str]:
    yield from check_commit(place, series["commit"])
    commits = series["commits"]
    last = commits[-1]["commit"] if commits else series["base"]
    if series["commit"] != last:
        yield "names a commit that is neither its last one nor, with none, its base"


def check_objects(place: Place, objects: Objects) -> Iterator[str]:
    yield from check_commit(place, objects["commit"])
    yield from check_build(place, objects["build"])


CHECKS: dict[str, Callable[[Place, Any], Iterator[str]]] = {
    "record": check_record,
    "series": check_series,
    "objects": check_objects,
}


def check_json(file: Path, place: Place) -> Iterator[str]:
    try:
        data: object = json.loads(file.read_text(encoding="utf-8"))
    except (ValueError, RecursionError) as error:
        yield f"is not JSON in UTF-8: {error}"
        return
    if not isinstance(data, dict):
        yield "is not a JSON object"
        return
    fields = cast("dict[str, Any]", data)
    version = fields.get("schema")
    if not isinstance(version, int):
        yield "has no integer schema"
        return
    if version not in KNOWN:
        yield f"schema {version} is not a version this validator knows"
        return
    kind = kind_of(place)
    found = SCHEMAS[kind].iter_errors(fields)
    errors = [error for outer in found for error in closest(outer)]
    for error in errors:
        where = "/".join(map(str, error.absolute_path))
        yield f"{where}: {error.message}" if where else error.message
    if not errors:
        yield from CHECKS[kind](place, fields)


def check_file(file: Path, place: Place | None) -> Iterator[str]:
    if file.is_symlink():
        yield "is a symbolic link"
    elif place is None:
        yield "lies under no commit or series"
    elif file.name.endswith(".json"):
        if is_record(file, place):
            yield from check_name(place)
        yield from check_json(file, place)
    elif not is_named(place):
        yield "is not a file the format names"


def files(directory: Path) -> Iterator[Path]:
    """Yield each file under a directory, in order, and none of a hidden one."""
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_dir():
            yield path
        elif not path.name.startswith("."):
            yield from files(path)


def check(records: Path) -> Iterator[tuple[str, str]]:
    """Yield each file under records that breaks the format, and why."""
    commits: set[str] = set()
    for file in files(records):
        relative = file.relative_to(records)
        # A linked worktree keeps .git as a file.
        if relative.parts == (".git",):
            continue
        place = locate(relative.parts)
        # A commit is told of its missing series.json once, at its first record.
        if place and not place.series and is_record(file, place):
            series = f"{place.project}/{place.name}/series.json"
            if series not in commits and not (records / series).is_file():
                yield series, "is missing; each commit that has a record holds one"
            commits.add(series)
        for problem in check_file(file, place):
            yield relative.as_posix(), problem


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument("--records", type=Path, required=True, metavar="DIR")
    records: Path = parser.parse_args(argv).records
    if not records.is_dir():
        parser.error(f"{records} is not a directory")
    problems = [f"{path}: {problem}\n" for path, problem in check(records)]
    sys.stdout.writelines(problems)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
