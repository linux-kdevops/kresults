#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Write the files of a tree of records, each kind by its subcommand (README.md).

    krecord [SUBCOMMAND] OPTION...

Without a subcommand the options are run's: the ExecStopPost= of a unit
names none. `krecord SUBCOMMAND --help` says what a subcommand writes and
the options it takes. Exit with the subcommand's status, or 2 for a word
that names none.
"""

import importlib
import sys
from collections.abc import Sequence

# Each subcommand, and the module whose main() takes its arguments.
SUBCOMMANDS = {
    "run": "record",
    "series": "series",
    "objects": "objects",
    "blktests": "blktests",
}


def main(argv: Sequence[str] | None = None) -> int:
    given = list(sys.argv[1:] if argv is None else argv)
    names = ", ".join(SUBCOMMANDS)
    if given[:1] in (["-h"], ["--help"]):
        sys.stdout.write(f"{__doc__}\nsubcommands: {names}\n")
        return 0
    name = "run" if not given or given[0].startswith("-") else given.pop(0)
    if name not in SUBCOMMANDS:
        sys.stderr.write(f"krecord: {name} is no subcommand of {names}\n")
        return 2
    # Only the module that runs is read: run starts as every tool exits.
    return importlib.import_module(SUBCOMMANDS[name]).main(given)


if __name__ == "__main__":
    sys.exit(main())
