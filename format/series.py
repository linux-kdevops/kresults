#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Write the series.json of commits that came from no message (README.md).

Run at the top of the Git work tree that holds them:

    kseries --range BASE..TIP

BASE.. ends at the tree's HEAD. Print the file written and exit 0, or why
there is none and exit 1.
"""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from record import Json, NoRecordError, need, placed, run, state, write


def whole(revision: str) -> str:
    """Return a revision's commit, whole, or nothing when it names none."""
    # After --end-of-options a revision that starts with - is no option.
    name = f"{revision}^{{commit}}"
    return run("git", "rev-parse", "--verify", "--end-of-options", name)


def named(commit: str, project: str) -> str:
    """Return the directory krecord files a commit's records under."""
    return placed(f"..{commit}", commit, project)[1]


def trees_of(commit: str) -> list[Json]:
    """Return the public trees with a branch that holds a commit, as git lists them."""
    form = "--format=%(refname:lstrip=2)"
    held = run("git", "for-each-ref", "--contains", commit, form, "refs/remotes")
    holders = {branch.partition("/")[0] for branch in held.splitlines()}
    trees = [
        {"remote": remote, "url": run("git", "remote", "get-url", remote)}
        for remote in run("git", "remote").splitlines()
        if remote in holders
    ]
    # A tree at a path or over SSH is not public.
    return [tree for tree in trees if tree["url"].startswith(("http://", "https://"))]


def commits_of(base: str, tip: str) -> list[Json]:
    """Return the commits a base lacks, oldest first, as series.json names one."""
    links = "%(trailers:key=Link,valueonly,unfold,separator=%x1f)"
    form = f"--format=%H%x1f%s%x1f%an <%ae>%x1f{links}"
    log = run("git", "log", "--reverse", form, f"{base}..{tip}")
    commits: list[Json] = []
    for line in log.split("\n") if log else ():
        # A commit without the trailer may end its line at any field.
        commit, subject, author, *found = [*line.split("\x1f"), "", ""]
        link = ([""] + [one for one in found if one])[-1]
        commits += [{"commit": commit, "subject": subject, "author": author}]
        commits[-1]["link"] = link
    return commits


def changed(base: str, tip: str, *options: str) -> list[str]:
    """Return the files that differ from a base to a tip, sorted."""
    return sorted(run("git", "diff", "--name-only", *options, base, tip).splitlines())


def books_of(files: Sequence[str], tip: str) -> list[str]:
    """Return the directories of Documentation with an index.rst that hold a file."""
    parts = [file.split("/") for file in files if file.startswith("Documentation/")]
    indexes = {f"Documentation/{part[1]}/index.rst" for part in parts if part[2:]}
    listing = ("git", "ls-tree", "--full-tree", "--name-only", tip, "--")
    found = run(*listing, *sorted(indexes)) if indexes else ""
    return sorted(index.split("/")[1] for index in found.splitlines())


def series_of(range_: str, project: str) -> tuple[str, Json]:
    """Return the name of a range's tip and its series, or raise why it has none."""
    given, dots, last = range_.partition("..")
    need(dots and given, f"{range_} is no BASE..TIP")
    need(run("git", "rev-parse", "--show-toplevel"), "this is no Git work tree")
    base, tip = whole(given), whole(last or "HEAD")
    need(base and tip, f"{range_} names no commit")
    ancestor = run("git", "merge-base", base, tip) == base
    need(ancestor, f"{given} is no ancestor of {last or 'HEAD'}")
    commits = commits_of(base, tip)
    need(commits, f"{range_} holds no commit")
    ref = last
    if last in ("", "HEAD"):
        # The branch HEAD is on, which is none when it is detached.
        ref = run("git", "symbolic-ref", "--quiet", "--short", "HEAD")
    files = changed(base, tip, "--diff-filter=d")
    series: Json = {"schema": 1, "commit": tip, "message_id": "", "ref": ref}
    series |= {"trees": trees_of(tip), "base": base}
    series |= {"base_name": named(base, project), "base_trees": trees_of(base)}
    series |= {"commits": commits}
    series |= {"sources": [file for file in files if file.endswith((".c", ".h"))]}
    series |= {"docs": books_of(files, tip)}
    series |= {"added": changed(base, tip, "--no-renames", "--diff-filter=A")}
    return named(tip, project), series | {"lists": []}


def main(argv: Sequence[str] | None = None) -> int:
    summary = (__doc__ or "").partition("\n")[0]
    parser = argparse.ArgumentParser(description=summary, allow_abbrev=False)
    parser.add_argument("--range", required=True, metavar="BASE..TIP")
    parser.add_argument("--project", default="linux", metavar="NAME")
    parser.add_argument("--records", type=Path, default=state(), metavar="DIR")
    args = parser.parse_args(argv)
    try:
        name, series = series_of(args.range, args.project)
    except NoRecordError as why:
        sys.stderr.write(f"no series: {why}\n")
        return 1
    write(args.records / args.project / name / "series.json", series, "series")
    # A path unit on the tree's top sees no write below it: mark the change.
    os.utime(args.records)
    return 0


if __name__ == "__main__":
    sys.exit(main())
