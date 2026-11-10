<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->

# The format: decisions

`format/README.md` is the format as it stands. No producer writes version
1 yet, so a decision changes it in place. Once one does, a change raises
the version (`format/CHANGES.md`).

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

## Open

1. A table of tools beside the naming rule. A record is named for the
   tool "as the tool names itself", and for some commands that takes a
   reading. `make W=1e init/main.o` reports the compiler's warnings and is
   filed as `clang/init/main.o.json`. A second producer can as well file
   it as `make/init/main.o.json`, for the command, or as
   `w1/init/main.o.json`, for the flag. Two producers that read the rule
   differently file one result in two places, and nothing compares the
   two. A table in the format, a line for each known command, turns the
   reading into a lookup:

   | Command                             | Record                        |
   | ----------------------------------- | ----------------------------- |
   | `make W=1e init/main.o`             | `clang/init/main.o.json`      |
   | `make C=2 init/main.o`              | `sparse/init/main.o.json`     |
   | `make C=2 CHECK=smatch init/main.o` | `smatch/init/main.o.json`     |
   | `make CLIPPY=1`                     | `clippy.json`                 |
   | `clang-tidy init/main.c`            | `clang-tidy/init/main.o.json` |
   | `scripts/checkpatch.pl --git RANGE` | `checkpatch.pl.json`          |

   A tool the table lacks gets its line before a producer files it.
