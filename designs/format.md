<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->

# The format: decisions

`format/README.md` is the format as it stands. No records of version 1
are published yet, so a decision changes it in place, as the level for
series and the thread left out of the records did. Once a tree is
public, a change raises the version (`format/CHANGES.md`).

## Decided

- A commit's directory keeps its `git describe` name. The tree has to
  read: `v7.3-2-g1da4e6e1d71d` says where a commit sits, and a hash says
  nothing. The price is that two clones with different tags can name one
  commit twice. `subject.commit`, the whole hash, tells that they are one.
- A record lies under a build only when its command ran in it. A tool
  that reads no build, as `checkpatch.pl`, would otherwise be filed, and
  run, once for each configuration, architecture and compiler. Its record
  lies under the commit.
- A record names its build in `subject.build` as well as in its path. The
  path is how a record is found, and the field lets one be read, and
  reported on, away from its directory.
- What writes records is a producer, the schema's word. kdevops-ng, one
  of them, calls what runs its workflows an engine.
- Two outputs are compared as counted lines, in any order, without what
  moves between two runs: a kernel log's time stamp, a code address, the
  time `diff` prints. `make --jobs` prints two errors in either order, and
  the raw text would file one failing build once for each. A reader that
  sets a series against its base uses the same rule.
- A producer whose push is refused fetches, adds its runs again and
  pushes. A merge would conflict in every record two runs share, and each
  conflict resolves the same way, by keeping both sides. That is the write
  the producer already does, so it writes again and no person merges.
- A build keeps only the `.config` of its latest run. An entry made under
  an earlier one keeps its hash, which still tells that the two were
  configured differently, and a comparison uses no more.
- The format lists each known command with its record and where the
  record lies. The naming rule takes a reading for some commands, as for
  `make W=1e` on an object, and two producers that read it differently
  file one result twice. A command lies under a build when it reads the
  `.config` or what the build made: kbuild's `no-dot-config-targets` and
  each script's source say which.
- Profiles are sorted by the code points of their names, as `LC_ALL=C
  sort` does. A locale's order differs between hosts, and two of them
  would name one build twice.
- The text describes every field of a subject and of `series.json`, so
  that a producer can write a record from it. The schemas stay the exact
  form.
- A tool may carry `url`, where the source its `build` names can be
  read. A `git describe` names a commit and no tree, and a reader of a
  record has no checkout to look it up in. It is optional: most tools
  come from a package or a store path, which name their own source.
- A series whose Message-ID holds a `/` is not recorded. No tool that
  sends patches makes one, and an escape would be a rule to write and to
  check for a case not yet met.
- `validate` holds every record to `commands.json`, and refuses one the
  list lacks. A table in the text alone was advice: `checkpatch.pl.json`
  under a build still passed.
- A build made with no `ARCH` set is filed under the machine its `.config`
  names, like every build. The kernel picks its machine by itself, and a
  format that asked a developer to set it would refuse the plainest build
  there is.
- A build's `ARCH` is the machine the kernel built for, which the `.config`
  names whatever the run set `ARCH` to. The `.config`'s header names the
  arch, and x86 builds two word sizes under one header: a tinyconfig is an
  i386 kernel built on an x86_64 host, and both headers say `Linux/x86`.
  Its symbols name the machine, as kbuild's `UTS_MACHINE` does; the `ARCH`
  the run set stays in `env`. Other arches that build two word sizes name
  their symbols when a producer meets them.
- A commit that has a record holds `series.json`, which says how the
  commit came, with or without a message. A build's `config` and
  `objects.json` are not required, so a tree stays valid while a run is
  still writing it.
- A `pass` has an exit status of 0 and a `skip` gives its reason. A `fail`
  may exit 0, since some tools exit 0 whatever they find and the producer
  reads the verdict from what they printed.
- A tree with changes not committed is filed under its commit's name with
  `-dirty`, which `git describe --dirty` gives. What ran was not the
  commit, and its records must not pass for the commit's.
- A series names its mailing lists in `series.json`, as the base's
  `MAINTAINERS` names them on `L:` lines. A reader told a list from a
  person by a table of known lists, and the only one at hand was
  sashiko's, under Apache-2.0. Each project names its own lists, QEMU's
  as well. A series that did not apply has no `series.json`, and so no
  lists.
- A series that came from no message keeps its cover letter in
  `series.json`, read from the commit `b4 prep` holds it in. No mail
  holds the letter of a series not yet sent, and its page would say
  none was recorded. The commit is no patch, so `commits` leaves it
  out. One with b4's tracking line that changes a file stays a commit.
- A series applied from a mailing list keeps its cover letter in
  `series.json` as well, read from the mail its producer fetched. A
  producer that applies the series itself records nothing under its
  Message-ID. The letter of a
  cover commit comes first: it is the tree's own, and the mail may be
  of an older revision. krecord series fetches nothing. It uses a thread
  only when each commit of the range has the subject of one of its
  patches, since a tree keeps the thread of the series it held before.
- A run that read commits as patches gives its range, on krecord's
  command line or in a file. The record holds the range
  whole, in `subject.range` and in the command, and lies under its last
  commit, with no `-dirty`: the tool read the commits, not the working
  tree. The range as typed, `HEAD~3..`, would name other commits once
  HEAD moves.
- A run that worked one file names it on krecord's command line. The
  record holds it in `subject.file`, and `{file}` fills it in the command and
  the record's name, so a producer gives krecord one line for every
  file it checks.
- A run that took a series gives its Message-ID in a file, as a run
  that read commits gives its range in one. The record holds it in
  `subject.message_id` and lies under the Message-ID, with no commit's
  `series.json`: the series is recorded before it applies to any commit,
  and one that did not apply has no commit to lie under.
- A project's series lie under one directory, `series`, and not beside
  its commits. One MAINTAINERS entry watched for a month filed 104
  series beside 3 commits, which a listing of the project lost among
  them. No commit takes the name, which no tag that starts with `v` and
  a digit has and no hash spells, so a path still tells the two apart,
  by its place and not by an `@` in a name. A project may have any name,
  `series` too: it is the first directory of a path, and the level the
  second.
- The thread of a series is no part of the records. What a reader shows
  of it is in the series' own `series.json`: the subject, author and
  date of its first message, its revision, how many patches it has, and
  its cover letter's text. The records are published as a Git tree, and
  104 threads took 9.3 MB of its 11.7 MB, about 100 MB a year for each
  entry watched; their 104 `series.json` take 174 kB, the longest 13 kB.
  The mails stay on the list, where the Message-ID finds them. The file
  has the fields of a commit's `cover` and more, under another schema: a
  commit's `series.json` requires a commit and a base, and a series not
  yet applied has neither. The subject is kept whole, since its prefixes
  say more than the revision: `RFC`, and the tree it is for.
- What fetches a thread writes the series' `series.json`: `krecord lore`
  from the maildir, and `krecord run` from the mbox `--mbox` names. A
  second subcommand would have a producer run two programs for one
  fetch, and a series with a record and no subject between them. The
  mbox is used only when it holds the message of the Message-ID: a tree
  keeps the thread of the series it fetched before, and a fetch that
  fails leaves that one in place.
- `krecord lore` files a series once, and a thread that gains mail adds
  no invocation. It filed a grown thread again to save its mbox whole,
  and no mbox is saved.
- A thread lorefetch fetched is filed as `b4/mbox`, with lorefetch as its
  entry's tool. A record is named for the tool that ran, and this one is
  not: the site and every reader take how a series was fetched from that
  one name, and a second name would have each of them look in two places.
  The entry still says what ran. Its command names the query and no
  maildir, which only places files, so `krecord lore` is given the query: the
  maildir keeps none.
- A benchmark's numbers are `metrics` of an invocation, and take no part
  in whether two runs are equal. No two runs measure the same number, so
  as a part of the entry, or of its output, each run would be an entry
  and none would have a second sample. The entry's invocations are the
  samples, and nothing stores their spread: a figure kept beside them
  could disagree with them. The producer extracts the numbers, since a
  format that read fio's output would follow each tool's own.
- A benchmark's verdict is whether it ran to the end. A threshold on a
  number is a choice of whoever reads it, and differs by machine.
- An entry names its job file by hash, as `job`, the way it names its
  `.config`. The file's name is in the command and its content is not:
  the numbers of two jobs under one name must not be read as samples of
  one.
- A record's name may name the compiler, filled from the `.config`.
  `W=1e` keeps the runs of two compilers apart, and a producer that
  named neither would write its line once per compiler.
- A command that takes one end of the range by itself names that commit
  whole, as the range names both. `scripts/check-uapi.sh` reads the base
  apart from the tip, and `{base}` fills the range's first commit.

## Open

1. A configuration that fails. A `make tinyconfig` that fails writes no
   `.config`, and an entry under a build has to carry the hash of one, so
   the failure cannot be filed. A producer leaves no record of it.
2. A `-dirty` directory against its commit. `validate` holds a directory
   named by a hash to `subject.commit`, and does not read the hash in a
   name that ends in `-dirty`.
3. Which compiler `krecord` names. The format says `clang` when `LLVM` is
   set. `krecord` reads `CONFIG_CC_IS_CLANG` of the `.config`, which also
   holds for a build made with `CC=clang`.
4. `krecord` and `commands.json`. `krecord` files any `--output` it is
   given, and only `validate` refuses a record the list lacks. It also
   applies the path rules to the output alone, and leaves `command` and
   `env` as given.
