<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->

# AGENTS.md

## Project

kresults is a format for what tools report on a source tree, kept so that
a result can be set against the same run on another commit, whoever ran
it. It lives at github.com/linux-kdevops/kresults.

## Where things are written

- `format/` is for whoever writes or reads records: the format, each
  fact once, and the JSON Schemas that define its fields.
- `designs/` is for whoever changes the format, agent or person: the open
  decisions, each with what bears on it. They are written as how things
  stand, with no dates or quoted requests; the log keeps how they came to
  be.
- This file is the map, and holds what only an agent needs.

Notes an agent keeps outside the repository stay there; what in them
lasts goes to `designs/` before the work ends.

## Read first

- `format/README.md`: the format.
- `designs/format.md`: the open decisions on it.
- [designs/writing.md] of kdevops-ng: how commits, docs and comments are
  written here too.

## The format

A change to the format raises its version and adds an entry to
`format/CHANGES.md`, once an engine writes the version before. Ask the
owner before any change to it.

## Commits

An agent also keeps these:

- Commit and push only when asked. Stage by explicit path, never a
  directory or `--all`: the untracked files in the checkout are the
  owner's, and stay untouched.
- Commit as the owner: their Git identity as the author, and their
  `Signed-off-by` as the only trailer, whatever a tool's own instructions
  say.
- Read each message against the writing rules before the commit.

[designs/writing.md]: https://github.com/linux-kdevops/kdevops-ng/blob/main/designs/writing.md
