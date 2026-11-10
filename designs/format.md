<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->

# The format: open decisions

`format/README.md` is the format as it stands. No engine writes version 1
yet, so a decision below changes it in place. Once one does, a change
raises the version (`format/CHANGES.md`).

1. What names a commit's directory. `git describe` names it, and that name
   depends on the tags in the writer's clone: two writers with different
   tags file one commit under two names, and `base_name` in `series.json`
   moves with it. The full hash, which `subject.commit` already holds, is
   the same for every writer. The describe name would then be a field for
   readers.
2. Where a record lies when its tool reads no build. Every record of a
   commit lies under a build, so `checkpatch.pl`, which reads no
   `.config`, is filed once for each configuration, architecture and
   compiler. A record whose entries have no `config` could lie directly
   under the commit.
3. Whether a record names its build. The configuration's name is in the
   path alone: a record holds `ARCH`, `LLVM` and the hash of the
   `.config`, but neither `tinyconfig` nor the profiles. With the build in
   `subject`, a record says where it belongs and its path follows from it.
4. Output whose order varies. What a run reported is part of an entry's
   identity, and `make --jobs` prints two errors in either order, so one
   failing build can leave an entry for each order.
5. Two writers on one branch. A run writes whole each record it adds to,
   so two engines that push at once change the same files. The format does
   not say what a writer does when its push is refused.
6. A list of tools beside the naming rule. "As the tool names itself"
   leaves `W=1` under `clang/` or `gcc/`, the analyzer that `clang-tidy`
   runs, and `CLIPPY=1` to each writer's reading.
7. The word for what writes records. The text says engine and the schema
   says `producer`. kdevops-ng, which is to write them, calls what runs
   its workflows an engine as well.
8. The `config` a build keeps. A build holds one `config` file, the last,
   while entries made under earlier ones stay in its records.
