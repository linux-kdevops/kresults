<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->

# The records

This is version 1 of the format of the records ([CHANGES.md]), which
this repository keeps on its `records` branch. Any producer may write
them: a CI system, or a developer's own scripts.
[record.schema.json], [series.schema.json] and [objects.schema.json]
define each field.

## What a record holds

A record is what tools reported on one subject: a commit of a project,
a file, object or test in it, or a series by its Message-ID. It holds
one entry per distinct run. An entry is the tools with their
versions and builds, the command, its environment and `.config`, and
what the run reported: the verdict, the exit status and the output. A
run equal to an entry in all of these is added to its `invocations`,
and any difference makes a new entry. Two entries that differ only in
what they reported are a flaky test or tool.

The producer that ran a command is not part of the entry. An invocation
names it, with the lab, the run, the time and the machine.

`subject` names the project and the whole commit, then what narrows it:

- `build`, for a command that ran in one (below);
- `file`, a file or an object, from the top of the tree;
- `test`, a test as its suite names it, such as `generic/001`;
- `range`, `BASE..COMMIT` with both whole, for a tool that read the
  commits as patches.

The subject of a series is the project and its `message_id`.

sparse on `init/main.o` of a series, run by two labs with the same
sparse, built two ways:

```json
{
  "schema": 1,
  "subject": {"project": "linux", "commit": "1da4e6e1d71d…",
              "build": {"configuration": ["tinyconfig"],
                        "arch": "x86_64", "compiler": "clang"},
              "file": "init/main.o"},
  "entries": [
    {
      "tools": [
        {"name": "sparse", "version": "0.6.4",
         "build": "/nix/store/…-sparse-0.6.4"},
        {"name": "clang", "version": "21.1.8",
         "build": "/nix/store/…-clang-21.1.8"}
      ],
      "command": "make -s C=2 CF=-Wsparse-error init/main.o",
      "env": {"ARCH": "x86_64", "LLVM": "1"},
      "config": "sha256:1b15d805…",
      "status": "fail", "exit": 2,
      "output": "init/main.c:15:2: warning: …",
      "invocations": [
        {"producer": {"name": "kdevops-ng", "version": "5c2e9a41d0b7…"},
         "origin": "lab-a", "run": "1042",
         "started": "2026-11-10T09:12:03Z", "seconds": 3,
         "machine": {"arch": "x86_64", "cpu": "AMD Ryzen 9 7950X",
                     "cpus": 32, "memory": 134217728000}}
      ]
    },
    {
      "tools": [
        {"name": "sparse", "version": "0.6.4", "build": "v0.6.4"},
        {"name": "clang", "version": "21.1.8", "build": "llvmorg-21.1.8"}
      ],
      "command": "make -s C=2 CF=-Wsparse-error init/main.o",
      "env": {"ARCH": "x86_64", "LLVM": "1"},
      "config": "sha256:1b15d805…",
      "status": "fail", "exit": 2,
      "output": "init/main.c:15:2: warning: …",
      "invocations": [
        {"producer": {"name": "other-ci", "version": "4.2"},
         "origin": "lab-b", "…": "…"}
      ]
    }
  ]
}
```

A producer adds a run to the entry it equals, or adds an entry, and
writes the file whole. One whose push is refused fetches, adds its runs
to the records as they now stand, and pushes again. An entry of a series
is set against the entry of its base with the same tools, command and
environment. A reader says when their `.config` differs.

## Tools

`tools` lists the tool the command runs first, then each tool whose
version changes what it reports: the compiler under sparse, the
filesystem's utilities under fstests. `version` is the version the tool
prints of itself, as `sparse --version` does, and is empty for a tool
that prints none. A patched tool prints the version it was patched
from, so `build` names the exact build, as one of:

- a Nix store path, `/nix/store/<hash>-sparse-0.6.4`;
- `git describe` of the source it was built from,
  `v0.6.4-12-g0123456789ab`;
- a distribution's package and its version, `debian:sparse 0.6.4-4`;
- `tree` for a tool in the tree under test, such as
  `scripts/checkpatch.pl`, which is built from the subject's commit.

Two runs whose builds differ are two entries, even when their versions
agree.

## Command and environment

`command` is the command as run at the top of the tree, its paths
relative to it: `make -s C=2 CF=-Wsparse-error init/main.o`. It leaves
out the options that only place files, such as make's `-C` and `O=`,
and the variables in `env`. `env` holds each environment variable that
changes what the command reports, as it was set. Running `command` with
`env`'s variables, at the top of a tree at the same commit and build,
repeats the run.

`env` lists at least these, each when set and not empty:

- the variables [commands.json] lists under the project's `env` for the
  command's tool, as `ARCH` and `LLVM` for make in a kernel tree;
- fstests: each variable of the section that `check -s` runs, as its
  configuration file sets it.

`config` is `sha256:` and the SHA-256 of the `.config` the command ran
under, absent when it reads none. A profile's fragments can change
under the same name, so two runs at the same path may differ in
`config`, and then they are two entries. A build keeps the `.config` of
its latest run as its `config` file, so the `.config` of an earlier entry
is known by its hash alone. A command that only prepares the `.config`,
such as copying a defconfig or editing it with `scripts/config`, prints
nothing and leaves no record: the `.config` it made is the `config` of
the command that runs next.

## Verdicts

- `pass`: the command exited 0, or the test passed.
- `fail`: the code failed: the command exited non-zero, the test failed,
  or the machine under test crashed or hung during the test.
- `skip`: the test did not run, with the reason as its output. A test
  run only to check its requirements leaves no record.
- `error`: the tool did not finish for a cause outside the code under
  test, such as the machine that ran the tool or the tool itself
  failing. A comparison counts it neither introduced nor fixed.

`exit` is the command's exit status, and a test's is 1 when it failed,
else 0. A `pass` has an exit status of 0. A `fail` may have one too, for
a tool that exits 0 whatever it finds, where the producer reads the
verdict from what the tool printed. A `skip` always has its reason as
`output`. A run stopped on purpose, as when its producer cancels it,
leaves nothing.

## Output

`output` holds the errors and warnings the tool printed: what it wrote
to standard error, and to standard output for a tool that reports
there, such as `checkpatch.pl`. For a test it is what the suite
reported of it: its failure, standard error and kernel log, or why it
skipped. Nothing parses it, so a run keeps progress out with the tool's
own options, as `make -s` does. `output` is absent when the tool printed
nothing.

Two outputs are the same when they hold the same lines, each as many
times, in any order, once every line has lost what moves from one run to
the next: a kernel log's time stamp, `[ 6405.509403]`, a code address,
`0x` and six or more hexadecimal digits, and the time `diff` prints after
a file's name. `make --jobs` prints two errors in either order, and they
are one output. `output` keeps what the first run printed. A reader that
sets a series against its base matches lines the same way, and drops a
file's line and column as well, since a series moves code.

The records may be public. `output`, `command` and `env`:

- give paths in the tree relative to it, and leave out the absolute
  paths of the build directory and of the producer;
- write the home directory as `~`;
- name no host.

## Invocations

Each invocation carries every field:

- `producer`: the name and version of what ran the command;
- `origin`: the lab, by the name it records under, in lowercase
  letters, digits and `-`;
- `run`: the producer's name for the run, such as its run id;
- `started`: when the command started, in UTC, `2026-11-10T09:12:03Z`;
- `seconds`: how long it ran;
- `machine`: what it ran on, never its name: `arch` as `uname --machine`
  gives it, `cpu` as lscpu's Model name, `cpus` as nproc counts them,
  and `memory` in bytes. For a test, the machine under test.

An invocation may also carry `resources`, what the run took, as the
kernel accounts for it in the cgroup of the command. Every figure is a
total or a peak over the whole run, and one the host cannot give is left
out:

- `cpu`: `seconds` of processor time, with `user_seconds` and
  `system_seconds` apart, and `stalled_seconds`, how long some process
  waited for a processor;
- `memory`: `peak` and `swap_peak` in bytes, `stalled_seconds` waiting
  for memory, `high_events` and `max_events`, the times the run met a
  limit, `oom_kills`, and `major_faults`, the pages read back from disk;
- `io`: `stalled_seconds` waiting for I/O, and `read_bytes`,
  `write_bytes`, `read_operations` and `write_operations` where the io
  controller is delegated to the user's service manager;
- `tasks`: the `peak` number of processes and threads.

`krecord` reads them as the command exits, from `cpu.stat`, the three
`pressure` files, `memory.peak`, `memory.swap.peak`, `memory.events`,
`memory.stat`, `pids.peak` and `io.stat`. Its own run is in the figures,
some tens of milliseconds. They take no part in whether two runs are
equal.

Invocations are kept oldest first.

## Where records go

A record's path names its subject, and the subject names it whole.
Under a project lie its commits and its series:

```text
linux/                                   the project
├── v7.3-2-g1da4e6e1d71d/                a commit
│   ├── series.json                      how it came, and its base
│   ├── checkpatch.pl.json               a tool that reads no build
│   ├── make/htmldocs/filesystems.json   a make target that reads none
│   ├── tinyconfig/x86_64/clang/         a build: configuration, ARCH, compiler
│   │   ├── config                       its .config
│   │   ├── objects.json                 what it compiles of the series
│   │   ├── make/
│   │   │   ├── tinyconfig.json          make tinyconfig
│   │   │   ├── all.json                 make, the build
│   │   │   ├── dir-pkg.json             make dir-pkg
│   │   │   └── checkstack.json
│   │   ├── clang-tidy/init/main.o.json
│   │   └── sparse/init/main.o.json      make C=2 init/main.o
│   ├── block-layer/nvm-express-driver/x86_64/clang/
│   │   └── make/all.json
│   └── fstests/xfs-filesystem/x86_64/clang/
│       └── fstests/xfs_bs4k_ss512/generic/001.json
└── <message-id>/                        a series
    └── b4/
        ├── mbox.json                    b4 mbox --single-message
        ├── mbox.mbx                     the message it saved
        └── shazam.json                  b4 shazam
qemu/                                    another project
└── v11.1.0-12-g0123456789ab/            a commit
    ├── series.json
    ├── configure.json                   configure --target-list=LIST
    └── make/all.json                    make, the build
```

- A commit is named as `git describe --tags --match 'v[0-9]*'
  --abbrev=12 --always --dirty` names it in a clone with the project's
  release tags. A tree with changes not committed is thus filed apart,
  under a name that ends in `-dirty`, and `subject.commit` is the commit
  the changes lie on.
- A series is named by the Message-ID of its first message, without
  `<>`: the message posted, or the first of a local mbox. A Message-ID
  always holds an `@` and a commit's name never does. One that holds a
  `/` would be two directories, and its series is not recorded.
- A build is its configuration, kbuild's `ARCH` and the compiler, each
  a directory. The compiler is `clang` when `LLVM` is set and not empty,
  else `gcc`. The `ARCH` is the one the run set, or with none the one
  kbuild took for the host, which the `.config` names in its first
  lines: `x86` on an x86_64 host. The configuration is the make target
  that wrote the `.config`, such as `tinyconfig`, or the profiles of
  fragments merged into it, one directory each, in the order of
  `LC_ALL=C sort`: `block-layer/nvm-express-driver/`. A profile is named
  after the `MAINTAINERS` entry whose code it configures: the entry's
  title in lower case, each run of other characters a hyphen,
  `block-layer` for BLOCK LAYER. A profile for no entry's code is named
  for what it configures, `fstests` for the guest fstests runs in. The
  configuration ends at the first directory named for an `ARCH` with one
  named for a compiler below it, so no profile takes either name, nor a
  tool's. A record under a build names it again in `subject.build`.
- A record lies under the build its command ran in, and each of its
  entries has that build's `config`. A record of a tool that reads no
  build lies directly under the commit, and its entries have none:
  `checkpatch.pl` reads the commits alone, so one run serves every build.
- A QEMU commit has no build level: its records lie directly under the
  commit, and configure run with another `--target-list` is another
  entry of `configure.json`.
- Under a commit, a build or a series, a record is named for the tool
  that was run, as the tool names itself, then for what it was run on:
  - a make target under `make/`, `make/htmldocs/filesystems.json` for
    `make SPHINXDIRS=filesystems htmldocs`, and `make/all.json` for
    make with no target, whose default goal is `all`;
  - a checker that make runs on an object, with `C=2` or `W=`, or that
    a launcher in the tree runs, by the checker's name, since the target
    there is the object:
    `sparse/init/main.o.json`, `clang/init/main.o.json` under `W=1e`,
    `clang-tidy/init/main.o.json` from `run-clang-tools.py`;
  - a program or a script run directly by its own name:
    `checkpatch.pl.json`, `kernel-doc/init/main.c.json`; a program run
    with a subcommand by the program, then the subcommand,
    `b4/shazam.json`;
  - a test suite by its name, then the section that ran the test:
    `fstests/xfs_bs4k_ss512/generic/001.json`.

  The same tool run with other flags is another entry of the same
  record, as `checkpatch.pl` is with and without `--strict`.
- A directory or file name is at most 255 bytes, the limit of Linux
  filesystems.

## Known commands

[commands.json] lists, under `projects`, each project by the name of its
directory. A project has `env`, the variables to record for each tool,
and `commands`: each command a producer runs in its tree, with the name
of its record and whether the record lies under a build, the commit or a
series. In a record's name, `*` stands for one name, or part of one, and
`**` for a path. `validate` holds a record to the commands of the project
it lies under and refuses one the list lacks, so a project and a command
get their lines there before a producer files a record.

A line may also give `argv`, the command as a record holds it, with `*`
standing for the same text as in the record's name: `make tinyconfig`
matches `make *config` and is filed as `make/tinyconfig.json`. `krecord`
names a record by it.

A command lies under a build when it reads the `.config` or what the
build made, and under the commit when it reads neither. kbuild's
`no-dot-config-targets` names the make targets that read no `.config`.
`checkstack` is one of them and still lies under a build, since it reads
`vmlinux`. The same command may lie elsewhere in another project: make
lies under a build in `linux` and under the commit in `qemu`.

## A commit and its series

Each commit that has a record holds `series.json`, which says how the
commit came and what its records are set against. A commit that came from
no message has one as well, with an empty `message_id`:

- `commit`: the commit, whole;
- `message_id`: the series it was applied from, whose records lie under
  `<project>/<message-id>/`; empty for a commit that came from no
  message;
- `lists`: the series' mailing lists, the addresses of its cover
  letter's To and Cc, or of its first patch's without one, that the
  base's `MAINTAINERS` names on an `L:` line, lowercased and sorted;
  empty for a commit that came from no message;
- `ref`: the ref the run was given, such as `xfs-fix`, `for-next` or
  `v7.3`; empty for a series taken by its Message-ID;
- `trees`: the public trees with a branch that holds the commit, the
  first one to link it to; empty for a local branch;
- `base`, `base_name` and `base_trees`: the commit the series starts
  from, the directory its records lie under, and the public trees with
  a branch that holds it;
- `commits`: the commits the base lacks, oldest first, each with its
  subject, author and last `Link:` trailer. A `series.json` that lists
  none is a base's;
- `sources`, `docs` and `added`: the C files and headers the commits
  change, the directories of Documentation with an `index.rst` they
  change, and the files they add.

Each build of a series holds `objects.json`: the objects it compiles
from the series' C files, and the C files outside `tools/`, `scripts/`
and `Documentation/` it does not compile. It names its commit and its
build, as a record does. No tool prints these lists: the producer reads
them from the `.cmd` file kbuild writes for each object.

A series' own records lie under its Message-ID: `b4/mbox.json` for
fetching the message posted, `b4/mbox.mbx` for the message saved, and
`b4/shazam.json` for applying the series, its output what b4 printed.
A reader takes the series' subject, author and date from `mbox.mbx`. A
series that does not apply has these records alone.

## Versions

Each file names the version it follows as `schema`, one of
[CHANGES.md].

[CHANGES.md]: https://github.com/linux-kdevops/kresults/blob/main/format/CHANGES.md
[commands.json]: https://github.com/linux-kdevops/kresults/blob/main/format/commands.json
[objects.schema.json]: https://github.com/linux-kdevops/kresults/blob/main/format/objects.schema.json
[record.schema.json]: https://github.com/linux-kdevops/kresults/blob/main/format/record.schema.json
[series.schema.json]: https://github.com/linux-kdevops/kresults/blob/main/format/series.schema.json
