<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->

# The format: decisions

`format/README.md` is the format as it stands. No engine writes version 1
yet, so a decision changes it in place. Once one does, a change raises the
version (`format/CHANGES.md`).

## Decided

- A commit's directory keeps its `git describe` name. The tree has to
  read: `v7.3-2-g1da4e6e1d71d` says where a commit sits, and a hash says
  nothing. The price is that two clones with different tags can name one
  commit twice. `subject.commit`, the whole hash, tells that they are one.
- A record lies under a build only when its command ran in it. A tool
  that reads no build, as `checkpatch.pl`, would otherwise be filed, and
  run, once for each configuration, architecture and compiler. Its record
  lies under the commit.

## Open

1. Whether a record names its build. The configuration's name is in the
   path alone: a record holds `ARCH`, `LLVM` and the hash of the
   `.config`, but neither `tinyconfig` nor the profiles. With the build in
   `subject`, a record says where it belongs and its path follows from it.
2. Output whose order varies. What a run reported is part of an entry's
   identity, and `make --jobs` prints two errors in either order, so one
   failing build can leave an entry for each order.
3. Two writers on one branch. A run writes whole each record it adds to,
   so two engines that push at once change the same files. The format does
   not say what a writer does when its push is refused.
4. A list of tools beside the naming rule. "As the tool names itself"
   leaves `W=1` under `clang/` or `gcc/`, the analyzer that `clang-tidy`
   runs, and `CLIPPY=1` to each writer's reading.
5. The word for what writes records. The text says engine and the schema
   says `producer`. kdevops-ng, which is to write them, calls what runs
   its workflows an engine as well.
6. The `config` a build keeps. A build holds one `config` file, the last,
   while entries made under earlier ones stay in its records.
