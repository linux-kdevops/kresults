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
- The format lists each known command with its record and where the
  record lies. The naming rule takes a reading for some commands, as for
  `make W=1e` on an object, and two producers that read it differently
  file one result twice. A command lies under a build when it reads the
  `.config` or what the build made: kbuild's `no-dot-config-targets` and
  each script's source say which.

## Open

1. Whether `validate` holds a record to the known commands. It checks
   neither a record's name nor its level against the list, so
   `checkpatch.pl.json` under a build passes.
2. The directory of a native build. With no `ARCH` set, kbuild takes `x86`
   on an x86_64 host. The format does not say which name files that build.
3. The order of profiles. "Sorted by name" gives no collation. `validate`
   compares code points, where `block-layer` comes before `blockdev`, and
   a locale's order puts it after.
4. What must be there. Nothing requires a commit's `series.json`, a
   build's `config` or the `objects.json` of a series' build, and the hash
   of `config` is compared with no entry.
5. A verdict against its exit status. `pass` with an exit status of 2
   passes, and so does `skip` with no output.
6. What the text leaves to the schemas alone: `subject.test`,
   `subject.range` and the `commit` of `series.json`.
7. A Message-ID that holds a `/`. A mail may carry one, and the schema
   refuses it, since it would be two directories.
