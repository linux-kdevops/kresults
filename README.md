<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->

# kresults

kresults is a format for what tools report on a source tree. A record
is one JSON file for one tool on one subject, filed by commit and build:

```text
linux/v7.3-2-g1da4e6e1d71d/tinyconfig/x86_64/clang/sparse/init/main.o.json
```

It holds each run's tools and their builds, the command, its environment
and what the tool printed, so a result can be set against the same run
on another commit, whoever ran it.

[format/README.md](format/README.md) is the format, and the JSON Schemas
beside it define each field. Any producer may write records in it.
[designs/format.md](designs/format.md) lists the decisions still open.

Patches go to <kdevops@lists.linux.dev>, written as kdevops-ng's
[designs/writing.md] asks. `AGENTS.md` is the map for agents.

kresults is written with coding assistants; a person reviews and signs off
every commit.

License: copyleft-next-0.3.1.

[designs/writing.md]: https://github.com/linux-kdevops/kdevops-ng/blob/main/designs/writing.md
