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

## Open

1. How two outputs are compared. What a run reported is part of an
   entry's identity, and the order of its lines must not be: `make --jobs`
   prints two errors in either order, and one failing build would leave an
   entry for each. The options:
   - compare two outputs as counted lines, in any order, after dropping
     from each line what moves between two runs: a kernel log's time
     stamp, a code address, the time `diff` prints after a file's name.
     The output is kept as the first run printed it, and a real difference
     in what was reported still makes a new entry. A reader that sets a
     series against its base can match lines by the same key, with a
     file's line and column dropped as well, since a series moves code;
   - take the output out of the identity, and give each invocation its
     own verdict and output. A flaky test is then one entry whose
     invocations disagree;
   - have the producer print in a fixed order. `make --output-sync` keeps
     each target's lines together but not the targets in order, so only
     `--jobs=1` gives one.
2. What a producer does when its push is refused. A run writes whole each
   record it adds to, so two producers that push at once have changed the
   same files, and a merge conflicts in each. The options:
   - merge by hand. Each conflict resolves the same way: the entries of
     both sides, and for an entry on both, the invocations of both,
     oldest first. There is one conflict for each record the two runs
     share;
   - add again: fetch, add the run to the records as they now stand, and
     push. The producer did the same the first time, so it takes no rule
     of its own and no person;
   - a branch for each lab, merged by one job, which moves the same
     conflicts to that job.
3. A table of tools beside the naming rule. A record is named for the
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
4. The `.config` of an entry made before a build's last run. A build
   holds one `config` file, the `.config` of its latest run. A profile's
   fragments can change under one name, so a later run of the same commit
   and build can be configured differently: its entries carry the new
   hash, and `config` becomes the new `.config`. The earlier run's entries
   keep their hash, and the `.config` it names is gone. The options:
   - leave it so. The hash still tells that two entries were configured
     differently, which is all a comparison needs;
   - keep each `.config` under its hash, as `config/<sha256>`, at the
     price of a file of about 100 to 300 KiB for each change.
