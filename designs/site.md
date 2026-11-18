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
- A series shows the cover letter its `series.json` holds: its date and
  change-id in the header, and its text in one block above the patches,
  closed when it is longer than an output may be. One with a Message-ID
  links its thread as well; no thread holds the letter of a series not
  yet sent. Without a letter there, the page shows the headers of the
  mbox under the Message-ID.
- An entry names its tools on one line: each by name, the version it
  prints, and its build in parentheses, a link on a page where the tool
  has a `url`. A series' entry alone has the line, as it alone has the
  command. Without it "ran differently" points at tools no page names. A
  `url` is no part of what an entry ran, so an entry with one still
  matches a base's without.
- A section of a build's page whose records all show the same line of
  tools has it once, under its heading, and none under an entry; a
  section of one record does too. Where two records differ, in a `url`
  as well, each keeps the line under its command and the heading has
  none. A section of blktests has a hundred entries, and the same line
  under each says nothing after the first. The report and the page of a series that does not apply name the tools
  under each command: a section there is one entry.
- A build's page has a section per tool, in the order the tools first
  ran: each is placed by the earliest start of any invocation of its
  records there, the series' where it has any and else the base's. Two
  that started together come by name, and a tool with no record that
  could be read comes last. The page then reads as the work went:
  configure, build, checks, tests. By name, the configure comes after
  the tests that ran on its kernel, and a list of tools in the code
  needs a line for each new one. The findings line and the report keep
  the same order.
- A series' output and its base's open in place, by checkboxes that the
  stylesheet reads. Reading either used to mean leaving the diff.
- The index lists each series once, newest first by the cover letter's
  date, 50 to a page, with a findings line per build. Bases have a page
  of their own, and one page holds every row for the search. Every row
  on every page had made a page 173 KB.
- Every listing is of one project: its series, its bases, its search
  page and its stats, with the counts, the pager and the lists of that
  project alone. A record's path names one project and a viewer follows
  one, and a page of every project's rows shows an empty Series table
  where only another project, or only bases, have rows.
- The root pages are the default project's: `linux` where it has rows,
  else the first by name. Any other project's are the same names after
  its own and a hyphen, as `qemu-bases.html`, at the root too, so every
  link on a listing stays relative to the root. No project may be named
  as one of these files.
- The project select is the first control, before the list select, and
  holds the projects that have rows. A choice opens the same view of the
  project chosen: Series stays Series, Bases stays Bases. It is no field
  of the search, so an address carries no project. It needs the page's
  script; with none, the pages of the project shown still read whole.
- A project with bases and no series says so in one line that links its
  Bases, where the Series table would be. An empty table read as a site
  with nothing in it.
- A link out of the site is the project's own: the trees its tags are
  on, the tree a `path:LINE` shows in, the form of a commit's address. A
  QEMU base named `v11.1.0` is no release of the kernel's stable tree.
- A tree under no build directory is named by its configure's target
  list where it has one: of the `configure --target-list=LIST` that
  passed last, or with none passed that ran last. QEMU's make did build,
  so "no build" is wrong for it; a kernel commit's checkpatch keeps it.
  Only the name changes, and the page's path stays `-`.
- A recipient is a mailing list when its series' `series.json` lists
  it. Taking every address without a display name listed maintainers as
  lists (`designs/format.md`).
- A series no commit came from has its row and its page, with how far
  it got where an applied one has its findings: `fetched` where its only
  record is the fetch, `cannot apply` where the tool that applies
  refused it. A producer that watches a mailing list files more series
  than it builds, and a row was the only way to learn that one waits.
  Its page's Thread is the Message-ID a task takes.
- An entry whose runs measured numbers has a table under its run, a row
  for each metric: the base's samples and the series', each as their
  median, their range and their count, and the ratio of the medians. A
  row says `better` or `worse` only where the two ranges do not overlap
  and each side has three samples or more. Where they overlap it says
  within noise, and with fewer samples nothing: two runs of one kernel
  differ, and one sample a side would call that a change. The base's
  samples are those of its entry that ran the same job. No verdict comes
  of a number: a record's status says its benchmark ran, and the counts
  of a page are of statuses.
- A whole row is a link. Safari positions no table row, so the link's
  box is each cell and not the row.
- The series table has a fixed layout with one set of column widths. A
  long row on one page otherwise moved every column on the next.
- The bases table is one page, and sizes its columns by what they hold,
  the commit kept on one line. At a fixed 170 pixels the Producer column
  held about 19 characters and broke `kdevops-ng 571cb7d-dirty` in two,
  five producers into ten lines, beside a Builds column left mostly
  empty.
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
