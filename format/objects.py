# SPDX-License-Identifier: copyleft-next-0.3.1
"""Write the objects.json of one build of a series (README.md).

Run at the top of the Git work tree that was built, its HEAD the series'
tip, once krecord series has written the series:

    krecord objects --config build/.config --configuration-file build/.config.name

Print each object and each file not built, then the file written, and exit
0, or why there is none and exit 1.
"""

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from record import (
    Json,
    add_build,
    add_tree,
    build_under,
    built,
    configuration_of,
    exit_of,
    need,
    parser_of,
    touch,
    whole,
    write,
)
from series import named

# kbuild compiles no object of the kernel from a file under these.
OUTSIDE = ("tools/", "scripts/", "Documentation/")


def sources_of(file: Path) -> list[str]:
    """Return the C files a series.json lists, or raise why it lists none."""
    need(file.is_file(), f"{file} does not exist: krecord series writes it")
    series: Json = json.loads(file.read_bytes())
    need(series["commits"], f"{file} is a base's: krecord series names a series")
    return [
        source
        for source in series["sources"]
        if source.endswith(".c") and not source.startswith(OUTSIDE)
    ]


def object_of(source: str, build: Path) -> str | None:
    """Return the object a build compiles a C file into, or nothing without one.

    kbuild writes .NAME.o.cmd beside each object it compiles: the file's own
    object, or one whose source includes the file and so depends on it.
    """
    own = Path(source).with_suffix(".o")
    if (build / own.parent / f".{own.name}.cmd").is_file():
        return str(own)
    # A dependency is a path that ends a line, or all of it but make's " \".
    listed = re.compile(rf"(?:^|[\s/]){re.escape(source)}(?: \\)?$", re.MULTILINE)
    for command in sorted((build / own.parent).glob(".*.o.cmd")):
        if listed.search(command.read_text("utf-8", errors="replace")):
            return str(own.with_name(command.name[1:-4]))
    return None


def objects_of(args: argparse.Namespace) -> tuple[Path, Json]:
    """Return a build's objects.json and where it lies, or raise why it has none."""
    config: Path = args.config
    need(config.is_file(), f"{config} does not exist")
    commit = whole("HEAD")
    need(commit, "the working directory is no Git work tree")
    directory = args.records / args.project / named(commit, args.project)
    sources = sources_of(directory / "series.json")
    need((config.parent / ".vmlinux.o.cmd").is_file(), f"{config.parent} is not built")
    build = built(config, config.read_bytes(), str(configuration_of(args)))
    found = {source: object_of(source, config.parent) for source in sources}
    objects: Json = {"schema": 1, "commit": commit, "build": build}
    objects["objects"] = sorted({object_ for object_ in found.values() if object_})
    objects["unbuilt"] = sorted(source for source in found if not found[source])
    return build_under(directory, build) / "objects.json", objects


def write_objects(args: argparse.Namespace) -> None:
    """Write a build's objects.json and print what it lists, or raise why not."""
    file, objects = objects_of(args)
    for kind, paths in (("object", "objects"), ("unbuilt", "unbuilt")):
        sys.stderr.write("".join(f"{kind}: {path}\n" for path in objects[paths]))
    write(file, objects, "objects")
    touch(args.records)


def main(argv: Sequence[str] | None = None) -> int:
    parser = parser_of(__doc__, "objects")
    add_build(parser, required=True)
    add_tree(parser)
    args = parser.parse_args(argv)
    return exit_of(lambda: write_objects(args), "objects", fails=True)
