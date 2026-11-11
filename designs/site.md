<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->

# The site

`format/render.py` turns a tree of records into a static site. These are
the decisions behind it, each with what forced it. A change to the
renderer is reviewed against them.

## What it is modelled on

Two projects show a patch series with what tools found in it. [bodnuti]
keeps its state as files, one report per series, and writes plain HTML
with no script. [sashiko] is a single-page application over an API and a
database, and has the listing a reviewer already knows.

The site takes its storage from the first and its look from the second.
It reads files and no producer's database, so any producer's records
render and a site can be rebuilt from the tree alone. Its index, status
bar, colours and mailing-list tags are sashiko's. The GitHub mark on
About is Octicons', under MIT.

## Decided

### Reading the records

- The format is the authority. The renderer reads every field of a
  version, and shows a file of a version it does not know as unreadable.
- A record that cannot be read shows as unreadable on its page. One bad
  file, or a `config` that is a symbolic link, neither stops the site nor
  reaches a page.
- Every recorded string is escaped, and every link goes through
  `quote()` under a content security policy that allows no inline
  script. A directory named `javascript:...` once came out as a live
  link.
- Output is split on newlines only. `splitlines()` broke a tool's
  verbatim output at a carriage return. A control character shows as its
  control picture, and matching keeps it raw.
- Of a tool's terminal escapes only the SGR colours are drawn, and a
  colour ends with its line, so each line of a diff reads alone. b4 marks
  a good signature in green and a bad one in red.
- Records are loaded into frozen, typed values. Black, Ruff with every
  rule and strict Pyright report nothing, and the tests import the
  renderer, so a call with the wrong type fails the lint.

### A series against its base

- A commit whose `series.json` lists commits is a series, and each of
  its builds is set against the same build of its base. Any other commit
  is a base, and its pages show every output whole.
- A series record shows only where it differs from its base's: the
  lines added and removed, with three of context. The analyzer printed
  551 lines for one object file of a series whose base printed the same.
- Lines are matched as the format says two outputs are equal, and with
  positions dropped as well: the `:LINE:COL` after a file name, "line N"
  in a note, and a store path's hash. The second rule is the site's
  alone, since a series moves code and a record of one commit does not.
  Without it a kernel-doc warning that moved from line 3615 to 3616 read
  as two new lines, and checkstack as +72 -72 where one function grew
  from 640 to 656 bytes.
- A compiler's diagnostics are compared by finding. A patch that moves
  code reroutes the path of notes under a finding, and every rerouted
  line counted as new: one object file with the base's 4 findings and no
  other showed 198 new lines. The output decides, not the tool: a record
  that prints `file:line: error` is compared by finding, matched by the
  finding's first line, and any other line by line.
- A finding is drawn by its path, without the notes that name a macro.
  One new finding of 217 lines, 37 of its 70 notes on macros, never
  reached its last step within the lines a page shows. It draws as 36.
- A check of the series itself, as checkpatch on its patches, counts by
  its verdict, since what it prints is a report. A check of a file the
  series adds is set against a base that passed and printed nothing.
  Before this each read as a base left unchecked.
- A series whose `.config` differs from its base's says so and shows
  the two files' diff. One base had been configured from older fragments
  and was compared all the same.

### The pages

- A page reads the same with no script. Scripts only search and keep
  settings; the search box stays hidden until its script runs.
- The search reads text each row carries. A browser refuses a fetch from
  a page opened over `file://`, so an index to fetch would break a site
  read from disk.
- An output on a page stops at 50 lines, with the whole one link away.
  Four hundred lines pushed the next record several screens down.
- A series' output and its base's open in place, by checkboxes that the
  stylesheet reads. Reading either used to mean leaving the diff.
- The index lists each series once, newest first by the cover letter's
  date, 50 to a page, with a findings line per build. Bases have a page
  of their own, and one page holds every row for the search. Every row
  on every page had made a page 173 KB.
- A recipient is a mailing list when its series' `series.json` lists
  it. Taking every address without a display name listed maintainers as
  lists (`designs/format.md`).
- A whole row is a link. Safari positions no table row, so the link's
  box is each cell and not the row.
- Tables have a fixed layout with one set of column widths. A long row
  on one page otherwise moved every column on the next.
- A page's parts stack in block flow. WebKit does not always lay a
  grid's rows out again when a `details` element in one changes height,
  and drew one section over another.
- The charts are SVG, each mark a link to the series behind it. Build
  times are coloured by machine, since minutes compare only between like
  machines.
- About and Settings open as pop-ups by the `popover` attribute, which
  needs no script, and remain as pages for a browser without it.

### The report

- Each series gets its report as a plain-text reply to its cover
  letter, linked from its page. A series goes to a mailing list, so its
  results must be able to go back there. Nothing here mails it.
- The report's counts come from the function that fills the page's row,
  so the two cannot disagree. Two renders of the same tree are the same
  bytes.
- The report says how to get its findings again: the base, the way the
  series was applied, the command. Run by hand without them, a warning
  came back on another line of another commit.

### Rendering

- Rendering again into the same `--output` replaces a site whole, once
  the new one is written beside it. A site is marked by a `.render` file,
  and any other directory that is not empty is refused and left alone.
- The renderer renders once and exits. What keeps a site current and
  serves it is three systemd user units: a path unit on the tree starts
  the render, and Python's own file server serves what it wrote. Nothing
  of the renderer's runs for a while, scans the tree or polls it.
- The units serve the one tree `krecord` writes by default,
  `~/.local/state/krecords`. Named for a tree by its escaped path, they
  put `systemd-escape` in every command for a second tree that no one
  kept. Another tree is a drop-in.
- A path unit sees a change in the directory it watches and none below
  it, and records lie several levels down. Whatever writes a record
  touches the top of the tree afterwards, as `krecord` does, and that is
  the one event the unit waits for.

## Open

1. `commands.json`. The renderer tells a record under a build from one
   under the commit by its path, and does not read the list.
2. One producer's ways. Where kdevops-ng alone ran a series' records,
   the report repeats how that producer applies a series, and a
   producer's version links to its commit only for that name. A record
   has no field for either.
3. Where a line of code is shown. A `path:LINE` links to one published
   tree for each project, fixed in the renderer.
4. The older tests' records. Most of their trees predate fields the
   format now requires, and only one class of tests runs `validate` on
   what it renders.

[bodnuti]: https://github.com/moto-timo/bodnuti
[sashiko]: https://github.com/sashiko-dev/sashiko
