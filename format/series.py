# SPDX-License-Identifier: copyleft-next-0.3.1
"""Write the series.json of a range of commits (README.md).

Run at the top of the Git work tree that holds them:

    krecord series --range BASE..TIP [--mbox FILE]

BASE.. ends at the tree's HEAD. FILE is the mbox of the thread the
commits were posted as; one that is not there, or whose patches are not
the commits, names none. Print the file written and exit 0, or why there
is none and exit 1.
"""

import argparse
import contextlib
import json
import mailbox
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from email import policy
from email.errors import MessageError
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

from record import (
    MESSAGE,
    Json,
    NoRecordError,
    add_tree,
    exit_of,
    need,
    parser_of,
    placed,
    run,
    touch,
    whole,
    write,
)

# The line b4 prep starts its tracking block with, in a cover letter's commit.
TRACKING = "--- b4-submit-tracking ---"
# A subject's prefixes, each in its brackets, and what one says of the series.
PREFIXES = re.compile(r"(?:\s*\[[^\]]*\])*")
LETTER = re.compile(r"(?<![\w/])0+/\d+")
COUNTER = re.compile(r"(?<![\w/])(\d+)/(\d+)")
REVISION = re.compile(r"\bv(\d+)\b")
# Where a letter's text ends: the --- line above b4's trailers, or the signature.
END = re.compile(r"^---\n(?:base-commit|change-id):|^-- +$", re.MULTILINE)
CHANGE_ID = re.compile(r"^change-id: *(\S+)[ \t]*$", re.MULTILINE)
DATE = "%Y-%m-%dT%H:%M:%SZ"


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


def cover_of(commit: str) -> Json | None:
    """Return the cover letter b4 prep keeps in a commit, or None of another.

    Its commit changes no file, and its message holds b4's tracking block.
    """
    form = "--format=%an <%ae>%x1f%at%x1f%B"
    author, at, message = run("git", "log", "-1", form, commit).split("\x1f", 2)
    rows = message.split("\n")
    if TRACKING not in rows or changed(f"{commit}^", commit):
        return None
    end = rows.index(TRACKING)
    # b4 drops its template's lines, which start with #, when it sends.
    text, block = (
        "\n".join(row for row in part if not row.startswith("#"))
        for part in (rows[1:end], rows[end + 1 :])
    )
    try:
        tracked = dict(json.loads(block)["series"])
    except (ValueError, KeyError, TypeError):
        tracked = {}
    revision = tracked.get("revision")
    when = datetime.fromtimestamp(int(at), UTC)
    cover: Json = {"subject": rows[0], "author": author}
    cover |= {"date": when.strftime(DATE)}
    cover |= {"change_id": str(tracked.get("change-id") or "")}
    cover |= {"revision": revision if type(revision) is int and revision > 0 else 1}
    return cover | {"text": text.strip("\n")}


def letter_of(message: EmailMessage) -> Json | None:
    """Return the cover letter a message is, or None of a patch.

    A prefix of a cover letter's subject counts it as patch 0.
    """
    given = str(message["Subject"] or "")
    prefixes = PREFIXES.match(given)
    subject = given[prefixes.end() :].strip() if prefixes else ""
    if not (prefixes and subject and LETTER.search(prefixes[0])):
        return None
    return told_by(message, subject, prefixes[0])


def told_by(message: EmailMessage, subject: str, prefixes: str) -> Json:
    """Return what a message says of its series, as a cover letter names it."""
    sender, sent = message["From"], message["Date"]
    need(sender and sender.addresses, "the message it starts with names no author")
    need(sent and sent.datetime, "the message it starts with has no date")
    # A date without a zone is in UTC.
    when = sent.datetime
    when = (when if when.tzinfo else when.replace(tzinfo=UTC)).astimezone(UTC)
    part = message.get_body(("plain",))
    body = "\n".join(part.get_content().splitlines()) if part else ""
    change = CHANGE_ID.search(body)
    end = END.search(body)
    name, address = sender.addresses[0].display_name, sender.addresses[0].addr_spec
    cover: Json = {"subject": subject, "author": f"{name} <{address}>".strip()}
    cover |= {"date": when.strftime(DATE), "change_id": change[1] if change else ""}
    cover |= {"revision": revision_of(prefixes)}
    return cover | {"text": body[: end.start() if end else None].strip("\n")}


def posted_of(first: EmailMessage, name: str) -> Json:
    """Return the series.json of a series posted as a Message-ID names it.

    Its subject stays whole, as its prefixes say what the series is. One
    that starts with a patch has no letter, and so no text.
    """
    prefixes, subject = parts_of(first["Subject"])
    need(subject, f"{name} starts with no subject")
    counted = COUNTER.search(prefixes)
    posted = told_by(first, f"{prefixes} {subject}".strip(), prefixes)
    if not LETTER.search(prefixes):
        posted["text"] = ""
    posted["patches"] = max(int(counted[2]), 1) if counted else 1
    return {"schema": 1, "message_id": name} | posted


def thread_of(file: Path) -> list[EmailMessage]:
    """Return the messages of an mbox, in its order."""
    with contextlib.closing(mailbox.mbox(file, create=False)) as box:
        parser = BytesParser(policy=policy.default)
        return [parser.parsebytes(box.get_bytes(key)) for key in box.iterkeys()]


def write_posted(directory: Path, mbox: Path) -> None:
    """Write a series' own series.json from the thread a tool fetched of it.

    The directory is named for the Message-ID the tool took, and the
    message of that name starts the series. A tool that failed left no
    thread, or that of the series a tree held before, which names another.
    """
    name = directory.name
    need(mbox.exists(), f"{mbox} does not exist")
    try:
        thread = thread_of(mbox)
        ids = [str(one["Message-ID"] or "").strip() for one in thread]
        first = dict(zip(ids, thread, strict=True)).get(f"<{name}>")
        need(first, f"{mbox} holds no message {name}")
        posted = posted_of(first, name) if first else {}
    except (OSError, mailbox.Error, MessageError, LookupError, ValueError) as error:
        why = f"{mbox} cannot be read: {error}"
        raise NoRecordError(why) from error
    write(directory / "series.json", posted, "series")


def revision_of(prefixes: str) -> int:
    """Return the revision a subject's prefixes name, which is 1 when none does."""
    revision = REVISION.search(prefixes)
    return max(int(revision[1]), 1) if revision else 1


def parts_of(subject: object) -> tuple[str, str]:
    """Return a subject's prefixes and what follows them, its blanks single."""
    given = " ".join(str(subject or "").split())
    prefixes = PREFIXES.match(given)
    end = prefixes.end() if prefixes else 0
    return given[:end], given[end:].strip()


def patches_of(root: EmailMessage, thread: Sequence[EmailMessage]) -> set[str]:
    """Return the subjects of the patches a thread's root posted, without prefixes.

    A root that counts no patch is the only one. A reply's subject starts
    with no prefix, and a patch of another revision is of another series.
    """
    prefixes, subject = parts_of(root["Subject"])
    if not COUNTER.search(prefixes):
        return {subject}
    patches: set[str] = set()
    for message in thread:
        own, subject = parts_of(message["Subject"])
        counted = COUNTER.search(own)
        if counted and int(counted[1]) and revision_of(own) == revision_of(prefixes):
            patches.add(subject)
    return patches


def posted_as(file: Path | None, commits: Sequence[Json]) -> tuple[str, Json | None]:
    """Return the Message-ID of the root of an mbox's thread, and its cover letter.

    A file that is not there holds no message, and a thread whose patches
    are not the commits is of another series: a work tree keeps its mbox.
    """
    if file is None or not file.exists():
        return "", None
    try:
        thread = thread_of(file)
        need(thread, f"{file} holds no message")
        # The first message is the root of a thread whose root was not saved.
        root = next((one for one in thread if not one["In-Reply-To"]), thread[0])
        name = str(root["Message-ID"] or "").strip()
        patches = patches_of(root, thread)
        subjects = (" ".join(commit["subject"].split()) for commit in commits)
        if not all(subject in patches for subject in subjects):
            sys.stderr.write(f"no mbox: {file} is of another series, {name}\n")
            return "", None
        cover = letter_of(root)
    except (OSError, mailbox.Error, MessageError, LookupError, ValueError) as error:
        why = f"{file} cannot be read: {error}"
        raise NoRecordError(why) from error
    message_id = name.removeprefix("<").removesuffix(">")
    need(MESSAGE.fullmatch(message_id), f"{name or file} is no Message-ID")
    return message_id, cover


def books_of(files: Sequence[str], tip: str) -> list[str]:
    """Return the directories of Documentation with an index.rst that hold a file."""
    parts = [file.split("/") for file in files if file.startswith("Documentation/")]
    indexes = {f"Documentation/{part[1]}/index.rst" for part in parts if part[2:]}
    listing = ("git", "ls-tree", "--full-tree", "--name-only", tip, "--")
    found = run(*listing, *sorted(indexes)) if indexes else ""
    return sorted(index.split("/")[1] for index in found.splitlines())


def series_of(range_: str, project: str, mbox: Path | None = None) -> tuple[str, Json]:
    """Return the name of a range's tip and its series, or raise why it has none."""
    given, dots, last = range_.partition("..")
    need(dots and given, f"{range_} is no BASE..TIP")
    need(run("git", "rev-parse", "--show-toplevel"), "this is no Git work tree")
    base, tip = whole(given), whole(last or "HEAD")
    need(base and tip, f"{range_} names no commit")
    ancestor = run("git", "merge-base", base, tip) == base
    need(ancestor, f"{given} is no ancestor of {last or 'HEAD'}")
    commits = commits_of(base, tip)
    # A cover letter is no patch.
    if cover := cover_of(commits[0]["commit"]) if commits else None:
        del commits[0]
    need(commits, f"{range_} holds no commit")
    # The letter the tree holds is its own, and one posted is of its time.
    message_id, posted = posted_as(mbox, commits)
    cover = cover or posted
    ref = last
    if last in ("", "HEAD"):
        # The branch HEAD is on, which is none when it is detached.
        ref = run("git", "symbolic-ref", "--quiet", "--short", "HEAD")
    files = changed(base, tip, "--diff-filter=d")
    series: Json = {"schema": 1, "commit": tip, "message_id": message_id, "ref": ref}
    series |= {"trees": trees_of(tip), "base": base}
    series |= {"base_name": named(base, project), "base_trees": trees_of(base)}
    series |= {"commits": commits} | ({"cover": cover} if cover else {})
    series |= {"sources": [file for file in files if file.endswith((".c", ".h"))]}
    series |= {"docs": books_of(files, tip)}
    series |= {"added": changed(base, tip, "--no-renames", "--diff-filter=A")}
    return named(tip, project), series | {"lists": []}


def write_series(args: argparse.Namespace) -> None:
    """Write the series.json of --range, or raise why it has none."""
    name, series = series_of(args.range, args.project, args.mbox)
    write(args.records / args.project / name / "series.json", series, "series")
    touch(args.records)


def main(argv: Sequence[str] | None = None) -> int:
    parser = parser_of(__doc__, "series")
    parser.add_argument("--range", required=True, metavar="BASE..TIP")
    parser.add_argument("--mbox", type=Path, metavar="FILE")
    add_tree(parser)
    args = parser.parse_args(argv)
    return exit_of(lambda: write_series(args), "series", fails=True)
