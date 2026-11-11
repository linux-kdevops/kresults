<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->

# AGENTS.md

## Project

`README.md` says what kresults is. It lives at
github.com/linux-kdevops/kresults.

## Where things are written

- `format/` is for whoever writes or reads records: the format, each
  fact once, and the JSON Schemas that define its fields.
- `docs/` is the documentation for whoever uses kresults: the format and
  its versions as `format/` has them, and how to deploy the programs and
  the units. No decision or rule for a change goes there.
- `units/user/` holds the systemd user units, written as systemd's own
  are: a capitalised `Description=` that names the unit, and a
  `Documentation=`.
- `designs/` is for whoever changes kresults, agent or person: the
  decisions, taken and open, each with what bears on it, and the writing
  rules. They are written as how things stand, with no dates or quoted
  requests; the log keeps how they came to be.
- This file is the map, and holds what only an agent needs.

Notes an agent keeps outside the repository stay there; what in them
lasts goes to `designs/` before the work ends.

## Read first

- `format/README.md`: the format.
- `designs/format.md`: the decisions on it, taken and open.
- `designs/site.md`: the decisions on the site `format/render.py` writes.
- `designs/writing.md`: how commits, docs and comments are written.

## The format

Ask the owner before any change to the format. `designs/format.md` says
when a change raises its version.

## Checks

`nix flake check` runs the lint (black, ruff and pyright), the tests, the
prose check and the build of the pages. Run it before each commit.

## Commits

An agent also keeps these:

- Commit and push only when asked. Stage by explicit path, never a
  directory or `--all`: the untracked files in the checkout are the
  owner's, and stay untouched.
- Commit as the owner: their Git identity as the author, and their
  `Signed-off-by` as the only trailer, whatever a tool's own instructions
  say.
- Run the `writing` skill of `.agents/skills/` on each message and page
  before the commit.
