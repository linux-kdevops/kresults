<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->

# kresults

kresults is a dashboard of what deterministic tools report on the
[Linux kernel] and [QEMU], for a patch series from a mailing list or any
Git branch:

- builds of each configuration and architecture;
- test suites, such as [fstests] and [blktests];
- static analyzers, such as [sparse], [smatch] and [clang-tidy];
- checks, such as [checkpatch.pl] and [kernel-doc].

CI systems write their results as records in one format, and
[kdevops-ng] is the first that does. Each series is set against its base,
built the same way, so a finding the base already had is not blamed on
the series.

The site takes its look from [sashiko], and its first version took
its plain, file-based pages from [bodnuti]. kdevops' first dashboard,
with its results, is in [kdevops-results-archive].

[Linux kernel]: https://kernel.org
[QEMU]: https://www.qemu.org/
[fstests]: https://git.kernel.org/pub/scm/fs/xfs/xfstests-dev.git
[blktests]: https://github.com/linux-blktests/blktests
[sparse]: https://docs.kernel.org/dev-tools/sparse.html
[smatch]: https://github.com/error27/smatch
[clang-tidy]: https://clang.llvm.org/extra/clang-tidy/
[checkpatch.pl]: https://docs.kernel.org/dev-tools/checkpatch.html
[kernel-doc]: https://docs.kernel.org/doc-guide/kernel-doc.html
[kdevops-ng]: https://github.com/linux-kdevops/kdevops-ng
[sashiko]: https://github.com/sashiko-dev/sashiko
[bodnuti]: https://github.com/moto-timo/bodnuti
[kdevops-results-archive]: https://github.com/linux-kdevops/kdevops-results-archive

[format/README.md](format/README.md) is the format, and the JSON Schemas
beside it define each field. Any producer may write records in it:
`krecord` files one run of a command,
`nix run .#validate -- --records DIR` checks a tree of records, and
`nix run .#render -- --records DIR --output DIR` writes a site of them,
which the units in `units/user/` keep current and serve.
[designs/format.md](designs/format.md) holds the decisions on the format,
and [designs/site.md](designs/site.md) those on the site.

Patches go to <kdevops@lists.linux.dev>, written as kdevops-ng's
[designs/writing.md] asks. `AGENTS.md` is the map for agents.

kresults is written with coding assistants; a person reviews and signs off
every commit.

License: copyleft-next-0.3.1.

[designs/writing.md]: https://github.com/linux-kdevops/kdevops-ng/blob/main/designs/writing.md
