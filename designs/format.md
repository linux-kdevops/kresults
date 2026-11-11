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
- Profiles are sorted by the code points of their names, as `LC_ALL=C
  sort` does. A locale's order differs between hosts, and two of them
  would name one build twice.
- The text describes every field of a subject and of `series.json`, so
  that a producer can write a record from it. The schemas stay the exact
  form.
- A series whose Message-ID holds a `/` is not recorded. No tool that
  sends patches makes one, and an escape would be a rule to write and to
  check for a case not yet met.
- `validate` holds every record to `commands.json`, and refuses one the
  list lacks. A table in the text alone was advice: `checkpatch.pl.json`
  under a build still passed.
- A build made with no `ARCH` set is filed under the one kbuild took for
  the host, which its `.config` names. The kernel picks its architecture
  by itself, and a format that asked a developer to set it would refuse
  the plainest build there is.
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
