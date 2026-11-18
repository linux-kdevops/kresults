# SPDX-License-Identifier: copyleft-next-0.3.1
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""File the series of a maildir lorefetch fills as records under --records (README.md).

Run once `lorefetch --query QUERY --maildir DIR` has added the threads
that match, with the query it ran:

    krecord lore --maildir DIR --query QUERY --producer NAME ...

A thread is a series when its first message, the one that answers no other
of the maildir, posts a patch: its subject starts with a prefix that holds
PATCH, as [PATCH v3 0/2] and [RFC PATCH] do. One whose prefix names a
kernel version, as [PATCH 6.12 000/670], is for a stable tree and is left
out. A series is filed under its Message-ID: the thread as b4/mbox.mbx,
and b4/mbox.json, whose tool is lorefetch.

--lorefetch names the directory an install of lorefetch left, with
version, the `git describe` of its source, and url, where that source can
be read. Without it lorefetch is the one on PATH. Print each file written,
and how many mails and threads leave no record with why. Exit 0 either
way, as krecord run does.
"""

import argparse
import re
import shlex
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.errors import MessageError
from email.parser import BytesHeaderParser
from itertools import takewhile
from pathlib import Path

from record import (
    MESSAGE,
    SERIES,
    Json,
    add_producer,
    add_tree,
    exit_of,
    installed,
    machine,
    made_by,
    merged,
    need,
    parser_of,
    run,
    tool,
    touch,
    write,
)
from series import parts_of

# The directories of a maildir that hold its mails; tmp holds those still
# being written.
HELD = ("cur", "new")
ID = re.compile(r"<([^<>\s]+)>")
# A word of a subject's prefixes that names a kernel version, the stable
# tree a series is for: 6.12, 6.12.118 or 6.12.y.
STABLE = re.compile(r"\d+\.\d+(?:\.\d+)?(?:\.y)?")
# The line public-inbox starts each message of an mbox with.
FROM = b"From mboxrd@z Thu Jan  1 00:00:00 1970\n"
# A line of a mail that would start a message of the mbox. lorefetch
# writes none, as it splits lore's mbox at each.
SPLITS = re.compile(rb"^From ", re.MULTILINE)
# The bytes a directory's name may take (README.md, "Where records go").
LONGEST = 255
# Why a thread leaves no record, in the order told.
NO_PATCH = "start with no patch"
FOR_STABLE = "are for a stable tree"
NO_NAME = "have a Message-ID the format does not record"
RECORDED = "are recorded already"


@dataclass(frozen=True, slots=True)
class Mail:
    """A mail of the maildir, by what its headers say."""

    file: Path
    # Its Message-ID, without <>.
    name: str
    subject: str
    # The Message-IDs it answers, the nearest first.
    answers: tuple[str, ...]


def mail_of(file: Path) -> Mail | None:
    """Return a mail by its headers, or None of one that names no Message-ID.

    The body stays unread: a maildir holds every mail of every thread, and
    few of them are filed.
    """
    try:
        with file.open("rb") as mail:
            head = b"".join(takewhile(bytes.strip, mail))
        headers = BytesHeaderParser(policy=policy.default).parsebytes(head)
        name = ID.search(str(headers["Message-ID"] or ""))
        subject = str(headers["Subject"] or "")
        answered = f"{headers['References'] or ''} {headers['In-Reply-To'] or ''}"
    except (OSError, MessageError, LookupError, ValueError):
        return None
    answers = tuple(reversed(ID.findall(answered)))
    return Mail(file, name[1], subject, answers) if name else None


def threads_of(mails: Mapping[str, Mail]) -> list[list[Mail]]:
    """Return the maildir's threads, each with its first message first.

    A mail is of the thread of the nearest mail it answers that the maildir
    holds. The rest of a thread follow by Message-ID: the order holds as
    replies arrive, whatever a mail's date claims.
    """

    def first(mail: Mail) -> str:
        # Two mails that answer each other must not turn for ever.
        seen = {mail.name}
        while near := [n for n in mail.answers if n in mails and n not in seen]:
            mail = mails[near[0]]
            seen.add(mail.name)
        return mail.name

    threads: dict[str, list[str]] = {}
    for mail in mails.values():
        threads.setdefault(first(mail), []).append(mail.name)
    return [
        [mails[name] for name in (start, *sorted(set(thread) - {start}))]
        for start, thread in sorted(threads.items())
    ]


def left_out(first: Mail) -> str:
    """Return why a thread is no series to record, or nothing of one that is.

    A Message-ID names a directory, and a mail may claim any: the format's
    own form, which has no slash, in characters that print.
    """
    prefixes = parts_of(first.subject)[0]
    name = first.name
    if "PATCH" not in prefixes.upper():
        return NO_PATCH
    if any(STABLE.fullmatch(word) for word in re.split(r"[\[\]\s]+", prefixes)):
        return FOR_STABLE
    plain = name.isascii() and name.isprintable() and len(name) <= LONGEST
    return "" if plain and MESSAGE.fullmatch(name) else NO_NAME


def mbox_of(thread: Sequence[Mail]) -> bytes:
    """Return a thread as the mbox lore gives of it, in the thread's order."""
    return b"".join(
        FROM + SPLITS.sub(b">From ", mail.file.read_bytes()).rstrip(b"\n") + b"\n\n"
        for mail in thread
    )


def held(mbox: Path) -> int:
    """Return how many messages an mbox holds, none where there is no file."""
    if not mbox.is_file():
        return 0
    with mbox.open("rb") as lines:
        return sum(line.startswith(b"From ") for line in lines)


def file_series(args: argparse.Namespace) -> None:
    """File each series of --maildir, or raise why they leave no record."""
    maildir: Path = args.maildir
    lab = run("git", "config", "--global", "--get", "kresults.origin")
    need(lab, "git config --global kresults.origin names no lab")
    for part in HELD:
        need((maildir / part).is_dir(), f"{maildir} is no maildir: it has no {part}")
    fetcher = args.lorefetch
    used = installed("lorefetch", fetcher) if fetcher else tool("lorefetch")
    # lorefetch's --maildir only places files, and is no part of the command.
    entry: Json = {"tools": [used]}
    entry |= {"command": f"lorefetch --query {shlex.quote(args.query)}", "env": {}}
    entry |= {"status": "pass", "exit": 0}
    who = made_by(args, lab)
    # What lorefetch took for one thread of a query, no file says.
    where = {"seconds": 0, "machine": machine()}
    files = [
        file
        for part in HELD
        for file in sorted((maildir / part).iterdir())
        if file.is_file()
    ]
    read = [mail for mail in map(mail_of, files) if mail]
    threads = threads_of({mail.name: mail for mail in read})
    left: dict[str, int] = dict.fromkeys((NO_PATCH, FOR_STABLE, NO_NAME, RECORDED), 0)
    wrote = 0
    for thread in threads:
        name = thread[0].name
        # No path is made of a Message-ID before left_out passes it.
        if why := left_out(thread[0]):
            left[why] += 1
            continue
        directory: Path = args.records / args.project / SERIES / name / "b4"
        filed = directory / "mbox.json"
        # A series filed is left as it is until its thread holds more.
        if filed.is_file() and held(directory / "mbox.mbx") >= len(thread):
            left[RECORDED] += 1
            continue
        # lorefetch writes a mail once: the thread's last was fetched then.
        fetched = max(mail.file.stat().st_mtime for mail in thread)
        started = datetime.fromtimestamp(fetched, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        ran = {**entry, "invocations": [{**who, "started": started, **where}]}
        write(directory / "mbox.mbx", mbox_of(thread))
        write(filed, merged(filed, {"project": args.project, "message_id": name}, ran))
        wrote += 1
    for why, count in left.items():
        if count:
            sys.stderr.write(f"no record: {count} of {len(threads)} threads {why}\n")
    if unread := len(files) - len(read):
        sys.stderr.write(
            f"no record: {unread} of {len(files)} mails name no Message-ID\n"
        )
    if wrote:
        touch(args.records)


def main(argv: Sequence[str] | None = None) -> int:
    parser = parser_of(__doc__, "lore")
    parser.add_argument("--maildir", type=Path, required=True, metavar="DIR")
    add_tree(parser)
    add_producer(parser)
    # The maildir keeps no trace of the query, and the command has to name it.
    parser.add_argument("--query", required=True, metavar="QUERY")
    parser.add_argument("--lorefetch", type=Path, metavar="DIR")
    args = parser.parse_args(argv)
    return exit_of(lambda: file_series(args), fails=False)
