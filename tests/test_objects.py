# SPDX-License-Identifier: copyleft-next-0.3.1
"""Test objects: a build becomes an objects.json validate accepts, or leaves none."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "format"))

import objects
import series
import validate

type Json = dict[str, Any]

CONFIG = "#\n# Linux/x86 7.3.0 Kernel Configuration\n#\nCONFIG_X86_64=y\n"
# The series' files: one the build compiles, one another object includes,
# one it does not compile, and those that are no object of the kernel.
FILES = (
    "block/built.c",
    "block/included.c",
    "block/unbuilt.c",
    "block/example.h",
    "tools/example.c",
    "scripts/example.c",
    "Documentation/example.c",
)
# The .cmd files kbuild left, each with the dependencies it lists.
COMMANDS = {
    ".vmlinux.o.cmd": "",
    "block/.built.o.cmd": "source_block/built.o := ../block/built.c\n",
    "block/.other.o.cmd": "deps_block/other.o := \\\n  ../block/not-included.c \\\n",
    "block/.includer.o.cmd": (
        "source_block/includer.o := ../block/includer.c\n\n"
        "deps_block/includer.o := \\\n"
        "  ../include/linux/compiler-version.h \\\n"
        "    $(wildcard include/config/CC_VERSION_TEXT) \\\n"
        "  ../block/included.c \\\n"
        "  ../block/example.h\n"
    ),
}


class ObjectsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.tree = self.root / "linux"
        self.records = self.root / "records"
        self.build = self.tree / "build"
        gitconfig = self.root / "gitconfig"
        gitconfig.write_text(
            "[user]\n\tname = User\n\temail = user@example.org\n"
            "[init]\n\tdefaultBranch = main\n"
        )
        environ = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": str(self.root),
        }
        self.enterContext(mock.patch.dict(os.environ, environ, clear=True))
        self.tree.mkdir()
        self.records.mkdir()
        self.git("init", "--quiet")
        self.commit("init: example", {".gitignore": "build/\n"})
        self.git("tag", "v1.2")
        self.head = self.commit("block: add files", dict.fromkeys(FILES, ""))
        self.name = f"v1.2-1-g{self.head[:12]}"
        for name, text in {".config": CONFIG, **COMMANDS}.items():
            (self.build / name).parent.mkdir(parents=True, exist_ok=True)
            (self.build / name).write_text(text)

    def git(self, *command: str) -> str:
        done = subprocess.run(  # noqa: S603
            ["git", "-C", str(self.tree), *command],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        return done.stdout.strip()

    def commit(self, message: str, files: dict[str, str]) -> str:
        """Commit files with a message, and return the commit."""
        for name, text in files.items():
            (self.tree / name).parent.mkdir(parents=True, exist_ok=True)
            (self.tree / name).write_text(text)
        self.git("add", "--all")
        self.git("commit", "--quiet", "--message", message)
        return self.git("rev-parse", "HEAD")

    def write(self, *options: str, status: int = 0, named: bool = True) -> str:
        """Run objects in the tree, after series if named, and return what it said."""
        with contextlib.chdir(self.tree), contextlib.redirect_stderr(io.StringIO()):
            given = [f"--records={self.records}", "--range=v1.2.."]
            self.assertEqual(series.main(given) if named else 0, 0)
        with (
            contextlib.chdir(self.tree),
            contextlib.redirect_stderr(io.StringIO()) as said,
        ):
            given = [f"--records={self.records}", "--config=build/.config", *options]
            self.assertEqual(objects.main(given), status)
        return said.getvalue()

    def test_a_build_of_a_series(self) -> None:
        # A file that is there is replaced, whatever order the profiles came in.
        place = self.records / "linux" / self.name / "block" / "nvme" / "x86_64" / "gcc"
        place.mkdir(parents=True)
        (place / "objects.json").write_text("{}\n")
        before = self.records.stat().st_mtime_ns
        said = self.write("--configuration=nvme+block")
        wanted = "object: block/built.o\nobject: block/includer.o\n"
        wanted += f"unbuilt: block/unbuilt.c\nobjects: {place / 'objects.json'}\n"
        self.assertEqual(said, wanted)
        self.assertEqual(list(validate.check(self.records)), [])
        build = {"configuration": ["block", "nvme"], "arch": "x86_64"}
        wanted = {
            "schema": 1,
            "commit": self.head,
            "build": build | {"compiler": "gcc"},
            "objects": ["block/built.o", "block/includer.o"],
            "unbuilt": ["block/unbuilt.c"],
        }
        self.assertEqual(json.loads((place / "objects.json").read_bytes()), wanted)
        self.assertGreaterEqual(self.records.stat().st_mtime_ns, before)

    def test_a_configuration_file_and_a_clang_build(self) -> None:
        (self.build / ".config").write_text(CONFIG + "CONFIG_CC_IS_CLANG=y\n")
        (self.build / ".config.name").write_text("defconfig\n")
        said = self.write("--configuration-file=build/.config.name")
        place = self.records / "linux" / self.name / "defconfig" / "x86_64" / "clang"
        self.assertTrue(said.endswith(f"objects: {place / 'objects.json'}\n"))

    def test_a_commit_without_a_series_is_refused(self) -> None:
        directory = self.records / "linux" / self.name
        said = self.write("--configuration=defconfig", status=1, named=False)
        file = directory / "series.json"
        wanted = f"no objects: {file} does not exist: kseries writes it\n"
        self.assertEqual(said, wanted)
        # krecord leaves a series.json that lists no commit: a base's.
        base: Json = {"commits": [], "sources": ["block/built.c"]}
        directory.mkdir(parents=True)
        file.write_text(json.dumps(base))
        said = self.write("--configuration=defconfig", status=1, named=False)
        wanted = f"no objects: {file} is a base's: kseries names a series\n"
        self.assertEqual(said, wanted)
        self.assertEqual(list(directory.iterdir()), [file])

    def test_a_build_that_is_not_finished_is_refused(self) -> None:
        (self.build / ".vmlinux.o.cmd").unlink()
        said = self.write("--configuration=defconfig", status=1)
        self.assertEqual(said, "no objects: build is not built\n")
        directory = self.records / "linux" / self.name
        self.assertEqual(list(directory.iterdir()), [directory / "series.json"])


if __name__ == "__main__":
    unittest.main()
