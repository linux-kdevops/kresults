#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Refuse the phrasing top kernel maintainers almost never write.

    check-prose.py --message FILE|-    a commit message, as the hook sees it
    check-prose.py --commit REV        REV's message and the prose REV adds
    check-prose.py --file PATH...      all the prose in each file

DENY fails, WARN only prints. Each DENY pattern was measured on commit
messages up to Linux v7.3-rc7: 7,742 from three years of 15 maintainers
match 14 times (0.18%), 13 of them the patch-as-subject opener, while the
rest of the kernel matches 2.4%. Dashes alone appear 1.3 times in 10,000
maintainer messages and 199 times in 10,000 that declare a tool's use.

Prose is a .md or .rst file outside its code blocks, the comments and
docstrings of Python, and the comments of a shell script, Nix, YAML, TOML,
INI, JSONC and systemd units. Code is not prose, so a string can hold a
refused word, as the patterns here do.
"""

import argparse
import ast
import io
import re
import subprocess
import sys
import tokenize
from collections.abc import Collection, Iterable
from pathlib import Path, PurePath

# A line's number, its text, and whether it opens a comment or a docstring.
Line = tuple[int, str, bool]

DENY = {
    "AI vocabulary": r"\b(?:delv(?:e|es|ed|ing)|leverag(?:e|es|ed|ing)|pivotal"
    r"|meticulous(?:ly)?|intricat(?:e|ely|ies)|showcas(?:e|es|ed|ing)"
    r"|foster(?:s|ed|ing)?|tapestry|holistic(?:ally)?"
    r"|empower(?:s|ed|ing|ment)?|bolster(?:s|ed|ing)?|vital(?:ly)?"
    r"|cutting-edge|state-of-the-art)\b",
    "plays a role": r"\bplay(?:s|ed|ing)? an? (?:\w+ )?role\b",
    "filler opener": r"(?:^|[.!?]\s+)(?:Importantly|In summary|In conclusion"
    r"|Previously),",
    "'This patch' opener": r"\bThis (?:commit|patch|change|series|PR) (?:adds"
    r"|introduces|implements|updates|refactors|improves|ensures|fixes|removes"
    r"|enhances|provides|addresses|makes|changes|moves)\b",
    "en or em dash": "[\u2013\u2014]",
    "headed section": r"^\s*(?:Key changes|Changes made|Summary of changes"
    r"|Benefits|Root cause|Impact)\s*:\s*$",
}
WARN = {
    "inflated word": r"\b(?:enhanc(?:e|es|ed|ing|ement|ements)"
    r"|facilitat(?:e|es|ed|ing)|comprehensive(?:ly)?|seamless(?:ly)?"
    r"|crucial(?:ly)?)\b",
    "connective opener": r"(?:^|[.!?]\s+)(?:Additionally|Furthermore|Moreover"
    r"|Overall),",
    "worth noting": r"\b[Ii]t(?: is|'s) worth (?:noting|mentioning|pointing out)\b",
    "ensuring tail": r", ensuring\b|\bwhich ensures\b",
    "in order to": r"\bin order to\b",
    "abstract quality": r"\b(?:readability|maintainability)\b",
    "key + noun": r"\bkey\s+(?:aspect|benefit|feature|role|component|factor"
    r"|improvement|insight|consideration|challenge)s?\b",
    "variety of": r"\ba (?:wide )?variety of\b",
}
# Tested on the line that opens a comment or a docstring only.
COMMENT_DENY = {
    "history in a comment": r"^(?:(?:Added|Fixed|Changed|Updated|Removed"
    r"|Refactored)\b|New:)",
    "restates the code": r"^(?:Increment|Decrement)s?\b",
}
COMMENT_WARN = {
    "restates the code": r"^(?:Check (?:if|whether)|(?:Loop|Iterate)s? "
    r"(?:over|through))\b",
    "'This function' opener": r"^(?:This|The) (?:function|method|helper|script"
    r"|module|class|task|step) (?:is used to|is responsible for|will|handles"
    r"|takes care of)\b",
    "helper opener": r"^(?:A )?[Hh]elper (?:function )?(?:to|for|that)\b",
}
LIST_ITEM = re.compile(r"\s*(?:[-*]|\d+[.)])\s")
TRAILER = re.compile(r"[A-Za-z][A-Za-z-]*: \S")
# Below this line a verbose commit's template holds the diff, not the message.
SCISSORS = "# ------------------------ >8 ------------------------"
HUNK = re.compile(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@")
FENCE = re.compile(r"`{3,}|~{3,}")
# A code directive, or a paragraph that ends in "::": any other directive
# holds prose.
RST_LITERAL = re.compile(r"\.\. (?:code-block|code|sourcecode)::|(?!\.\.).*::$")
DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
# A file with no suffix that starts with a shebang takes # comments too.
HASH = (".sh", ".nix", ".yaml", ".yml", ".toml", ".ini", ".service", ".timer")


def git(*args: str) -> str:
    """Return what a git command prints; git reports its own failure."""
    command = ["git", "-c", "core.quotePath=false", *args]
    return subprocess.check_output(command, text=True, errors="replace")


def read(path: str) -> str:
    """Return the text of a file, whatever its encoding."""
    return Path(path).read_text(encoding="utf-8", errors="replace")


def scan(where: str, lines: Iterable[Line]) -> bool:
    """Print each match as an error or a warning; return whether any is an error."""
    failed = False
    for n, text, opener in lines:
        levels = (("error", DENY, COMMENT_DENY), ("warning", WARN, COMMENT_WARN))
        for level, table, opening in levels:
            for name, pattern in (table | opening if opener else table).items():
                if re.search(pattern, text.strip()):
                    print(f"{level}: {where}:{n}: {name}: {text.strip()}")
                    failed |= level == "error"
    return failed


def message_lines(message: str) -> list[Line]:
    """Return a message's prose: no git comment, trailer, quoted log or code."""
    kept = [
        (n, text, False)
        for n, text in enumerate(message.partition(SCISSORS)[0].splitlines(), 1)
        if not text.startswith("#")
    ]
    # The subject stays, though "area: summary" reads as a trailer.
    while len(kept) > 1 and (not kept[-1][1].strip() or TRAILER.match(kept[-1][1])):
        kept.pop()
    return [line for line in kept if not line[1].startswith(("\t", "    ", ">"))]


def check_message(where: str, message: str) -> bool:
    """Report the findings of a commit message; return whether it fails."""
    lines = message_lines(message)
    failed = scan(where, lines)
    listed = [n for n, text, _ in lines[1:] if LIST_ITEM.match(text)]
    # One such line can be a wrapped sentence; two are a list.
    if len(listed) > 1:
        print(f"error: {where}:{listed[0]}: a list in the body; write paragraphs")
        failed = True
    return failed


def markdown_lines(source: str) -> list[Line]:
    """Return the lines of Markdown outside its fenced code blocks."""
    lines: list[Line] = []
    fence = ""
    for n, text in enumerate(source.splitlines(), 1):
        stripped = text.strip()
        if fence:
            if stripped.startswith(fence) and not stripped.strip(fence[0]):
                fence = ""
        elif mark := FENCE.match(stripped):
            fence = mark[0]
        else:
            lines.append((n, text, False))
    return lines


def rst_lines(source: str) -> list[Line]:
    """Return the lines of reStructuredText outside its literal blocks."""
    lines: list[Line] = []
    margin = -1
    for n, text in enumerate(source.splitlines(), 1):
        indent = len(text) - len(text.lstrip())
        # A literal block lasts while its lines are indented past its opener.
        if margin < 0 or (text.strip() and indent <= margin):
            margin = indent if RST_LITERAL.match(text.strip()) else -1
            lines.append((n, text, False))
    return lines


def comments(found: Iterable[tuple[int, str]], leader: str = "#") -> list[Line]:
    """Return the text of comment lines; the first of each block opens it."""
    lines: list[Line] = []
    previous = -1
    for n, text in found:
        if (n, text[:2]) != (1, "#!"):
            lines.append((n, text.lstrip(leader).strip(), n != previous + 1))
            previous = n
    return lines


def line_comments(source: str, leader: str = "#") -> list[Line]:
    """Return the comments that fill a line; one after code takes a parser to tell."""
    found = ((n, text.strip()) for n, text in enumerate(source.splitlines(), 1))
    return comments((line for line in found if line[1].startswith(leader)), leader)


def python_prose(source: str) -> list[Line]:
    """Return the comments and docstrings of Python source."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        tree = ast.parse(source)
    except (SyntaxError, tokenize.TokenError):
        return line_comments(source)
    prose = comments(
        (t.start[0], t.string) for t in tokens if t.type == tokenize.COMMENT
    )
    for node in ast.walk(tree):
        if isinstance(node, DOCUMENTED) and (
            text := ast.get_docstring(node, clean=False)
        ):
            first = node.body[0].lineno
            prose += [
                (n, line, n == first) for n, line in enumerate(text.splitlines(), first)
            ]
    return prose


def prose_of(path: str, source: str) -> list[Line]:
    """Return the prose of a file, as its name and its first line sort it."""
    suffix = PurePath(path).suffix
    if suffix == ".md":
        return markdown_lines(source)
    if suffix == ".rst":
        return rst_lines(source)
    if suffix == ".py":
        return python_prose(source)
    if suffix == ".jsonc":
        return line_comments(source, "//")
    if suffix in HASH or (not suffix and source.startswith("#!")):
        return line_comments(source)
    return []


def check_prose(path: str, source: str, added: Collection[int] | None = None) -> bool:
    """Report the findings of a file, or of its added lines; return whether it fails."""
    lines = sorted(prose_of(path, source))
    return scan(path, [line for line in lines if added is None or line[0] in added])


def added_lines(rev: str) -> dict[str, set[int]]:
    """Return the lines REV adds to each file, as REV's tree numbers them."""
    options = ("--unified=0", "--no-renames", "--no-color", "--no-ext-diff")
    diff = git("show", "--format=", *options, "--dst-prefix=b/", rev)
    added: dict[str, set[int]] = {}
    path = None
    for line in diff.splitlines():
        # A path is read from a file's header only: an added line can start
        # with the same characters.
        if line.startswith("diff --git "):
            path = None
        elif path is None and line.startswith("+++ b/"):
            path = line.removeprefix("+++ b/")
        elif path and (hunk := HUNK.match(line)):
            first, count = int(hunk[1]), int(hunk[2] or 1)
            added.setdefault(path, set()).update(range(first, first + count))
    return added


def check_commit(rev: str) -> bool:
    """Report the findings of REV's message and added prose; return whether it fails."""
    message = git("log", "--max-count=1", "--format=%B", rev)
    failed = check_message(f"{rev[:12]} message", message)
    for path, added in added_lines(rev).items():
        failed |= check_prose(path, git("show", f"{rev}:{path}"), added)
    return failed


def main() -> int:
    """Check what the command line names; return the exit status."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--message", metavar="FILE", help="a commit message; - is stdin")
    mode.add_argument("--commit", metavar="REV", help="its message and added prose")
    mode.add_argument("--file", metavar="PATH", nargs="+", help="all of its prose")
    args = parser.parse_args()
    if args.commit:
        return int(check_commit(git("rev-parse", "--verify", args.commit).strip()))
    if args.message == "-":
        return int(check_message("commit message", sys.stdin.read()))
    if args.message:
        return int(check_message("commit message", read(args.message)))
    return max(int(check_prose(path, read(path))) for path in args.file)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
