# SPDX-License-Identifier: copyleft-next-0.3.1
"""Test krecord: its first word names the module that takes the rest."""

import contextlib
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "format"))

import krecord

NAMES = "run, series, objects, blktests, lore, sample"


class KrecordTest(unittest.TestCase):
    def run_as(self, name: str, *argv: str) -> None:
        """Run krecord, which must hand a subcommand all but its name."""
        stub, others = mock.Mock(), mock.Mock()
        modules = dict.fromkeys(krecord.SUBCOMMANDS.values(), others)
        modules[krecord.SUBCOMMANDS[name]] = stub
        with mock.patch.dict(sys.modules, modules):
            self.assertIs(krecord.main(argv), stub.main.return_value)
        rest = argv[1:] if argv[:1] == (name,) else argv
        stub.main.assert_called_once_with(list(rest))
        others.main.assert_not_called()

    def test_subcommand_takes_the_rest(self) -> None:
        for name in krecord.SUBCOMMANDS:
            with self.subTest(name):
                self.run_as(name, name, "--option", "run")

    def test_no_subcommand_is_run(self) -> None:
        self.run_as("run", "--command", "make series")
        self.run_as("run")

    def test_parser_names_its_command_line(self) -> None:
        for name in krecord.SUBCOMMANDS:
            with (
                self.subTest(name),
                contextlib.redirect_stdout(io.StringIO()) as said,
                self.assertRaises(SystemExit) as exited,
            ):
                krecord.main([name, "--help"])
            self.assertEqual(exited.exception.code, 0)
            self.assertTrue(said.getvalue().startswith(f"usage: krecord {name} "))

    def test_help_names_the_subcommands(self) -> None:
        for option in ("--help", "-h"):
            with contextlib.redirect_stdout(io.StringIO()) as said:
                self.assertEqual(krecord.main([option, "series"]), 0)
            self.assertTrue(said.getvalue().endswith(f"\nsubcommands: {NAMES}\n"))

    def test_unknown_word_is_a_usage_error(self) -> None:
        done = subprocess.run(  # noqa: S603
            [sys.executable, str(krecord.__file__), "lint", "--range", "v1.."],
            check=False,
            capture_output=True,
            text=True,
        )
        said = f"krecord: lint is no subcommand of {NAMES}\n"
        self.assertEqual((done.returncode, done.stdout, done.stderr), (2, "", said))


if __name__ == "__main__":
    unittest.main()
