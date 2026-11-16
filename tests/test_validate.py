# SPDX-License-Identifier: copyleft-next-0.3.1
"""Test validate: a tree in the format passes, and each break of it is named."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft7Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "format"))

import validate

type Json = dict[str, Any]
# A tree of records: each file's path, with its JSON or its text.
type Files = dict[str, Json | str]

TIP = "0123456789ab" + 28 * "c"
OTHER = "fedcba987654" + 28 * "d"
BASE = 40 * "b"
COMMIT = "linux/v1.2-3-g0123456789ab"
SERIES = f"{COMMIT}/series.json"
# The base's directory is a tag, which tells nothing of its commit.
BASE_SERIES = "linux/v1.2/series.json"
CHECKPATCH = f"{COMMIT}/checkpatch.pl.json"
HTMLDOCS = f"{COMMIT}/make/htmldocs/filesystems.json"
KERNEL_DOC = f"{COMMIT}/kernel-doc/init/main.c.json"
TINY = "tinyconfig/x86_64/clang"
OBJECTS = f"{COMMIT}/{TINY}/objects.json"
MAKE = f"{COMMIT}/{TINY}/make/all.json"
SPARSE = f"{COMMIT}/{TINY}/sparse/init/main.o.json"
PROFILES = "block-layer/nvm-express-driver/x86_64/gcc"
NESTED = f"{COMMIT}/{PROFILES}/make/all.json"
GUEST = "fstests/xfs-filesystem/x86_64/clang"
TEST = f"{COMMIT}/{GUEST}/fstests/xfs_4k/generic/001.json"
APPLIED = "20260102-topic-v1-0-0123456789ab@example.org"
UNAPPLIED = "20260103030405.1234-1-dev@example.org"
SHAZAM = f"linux/{UNAPPLIED}/b4/shazam.json"
CONFIG = "sha256:" + 64 * "1"
OTHER_CONFIG = "sha256:" + 64 * "2"
CLANG = {"ARCH": "x86_64", "LLVM": "1"}
GCC = {"ARCH": "x86_64"}
SAME_RUN = [(SPARSE, "entry 2 is the run of entry 1; add to its invocations")]
UNKNOWN = "is of no known command; add its line to commands.json"
NO_PROJECT = "is of no known project; add its commands to commands.json"
OTHER_BUILD = "entry 1 sets an LLVM its build does not name"


def invocation() -> Json:
    return {
        "producer": {"name": "example-ci", "version": "1.0"},
        "origin": "lab-a",
        "run": "run-1",
        "started": "2026-01-02T03:04:05Z",
        "seconds": 3,
        "machine": {"arch": "x86_64", "cpu": "Example", "cpus": 4, "memory": 2**33},
    }


def entry(
    command: str, env: dict[str, str] | None = None, /, **outcome: object
) -> Json:
    """Return the entry of a run that passed, unless outcome says otherwise."""
    return {
        "tools": [{"name": "tool", "version": "1.0", "build": "v1.0"}],
        "command": command,
        "env": env or {},
        "status": "pass",
        "exit": 0,
        **outcome,
        "invocations": [invocation()],
    }


def record(subject: Json, *entries: Json) -> Json:
    subject = {"project": "linux", **subject}
    return {"schema": 1, "subject": subject, "entries": list(entries)}


def build(path: str) -> Json:
    """Return the build that lies at path, as a file names it."""
    *configuration, arch, compiler = path.split("/")
    return {"configuration": configuration, "arch": arch, "compiler": compiler}


def built(path: str, *entries: Json, **subject: str) -> Json:
    """Return a record of the tip in the build that lies at path."""
    return record({"commit": TIP, "build": build(path), **subject}, *entries)


def series(commit: str, message_id: str, *commits: str) -> Json:
    """Return the series.json of a commit that came as commits, or of a base."""
    author = "A Developer <dev@example.org>"
    return {
        "schema": 1,
        "commit": commit,
        "message_id": message_id,
        "ref": "",
        "trees": [],
        "base": BASE,
        "base_name": "v1.2",
        "base_trees": [],
        "commits": [
            {"commit": sha, "subject": "init: example", "author": author, "link": ""}
            for sha in commits
        ],
        "sources": ["init/main.c"] if commits else [],
        "docs": [],
        "added": [],
        "lists": ["linux-kernel@vger.kernel.org"] if message_id else [],
    }


def posted(message_id: str, **shazam: object) -> Files:
    """Return the files of a series, whose b4 shazam passed unless shazam says."""
    subject = {"message_id": message_id}
    fetch = f"b4 mbox --single-message {message_id}"
    return {
        f"linux/{message_id}/b4/mbox.json": record(subject, entry(fetch)),
        f"linux/{message_id}/b4/mbox.mbx": "From: A Developer <dev@example.org>\n\n",
        f"linux/{message_id}/b4/shazam.json": record(
            subject, entry(f"b4 shazam {message_id}", **shazam)
        ),
    }


def good() -> Files:
    """Return a tree in the format, with a file in each place one can lie."""
    warning = "init/main.c:15:2: warning: symbol 'x' was not declared\n"
    objects = {"objects": ["init/main.o"], "unbuilt": []}
    make = [entry("make -s", CLANG, config=config) for config in (CONFIG, OTHER_CONFIG)]
    sparse = entry("make -s C=2 init/main.o", CLANG, config=CONFIG, output=warning)
    check = entry("./check -s xfs_4k generic/001", {"FSTYP": "xfs"}, config=CONFIG)
    return {
        BASE_SERIES: series(BASE, ""),
        SERIES: series(TIP, APPLIED, TIP),
        CHECKPATCH: record(
            {"commit": TIP, "range": f"{BASE}..{TIP}"},
            entry(f"scripts/checkpatch.pl --git {BASE}..{TIP}"),
        ),
        HTMLDOCS: record(
            {"commit": TIP}, entry("make -s SPHINXDIRS=filesystems htmldocs")
        ),
        KERNEL_DOC: record(
            {"commit": TIP, "file": "init/main.c"},
            entry("scripts/kernel-doc -none init/main.c"),
        ),
        f"{COMMIT}/{TINY}/config": "CONFIG_EXAMPLE=y\n",
        OBJECTS: {"schema": 1, "commit": TIP, "build": build(TINY), **objects},
        MAKE: built(TINY, *make),
        SPARSE: built(TINY, sparse, file="init/main.o"),
        NESTED: built(PROFILES, entry("make -s", GCC, config=CONFIG)),
        TEST: built(GUEST, check, test="generic/001"),
        **posted(APPLIED),
        **posted(UNAPPLIED, status="fail", exit=1, output="error: patch failed\n"),
    }


@contextlib.contextmanager
def tree(files: Files) -> Generator[Path]:
    """Write files under a directory that lasts as long as the context."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for path, data in files.items():
            file = root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(data if isinstance(data, str) else json.dumps(data))
        yield root


def problems(files: Files) -> list[tuple[str, str]]:
    with tree(files) as root:
        return list(validate.check(root))


def at(files: Files, path: str) -> Json:
    return cast("Json", files[path])


def first_entry(files: Files, path: str) -> Json:
    return at(files, path)["entries"][0]


def moved(path: str, to: str) -> Files:
    """Return the good tree with the file at path moved to another."""
    files = good()
    files[to] = files.pop(path)
    return files


def copied(path: str, to: str) -> Files:
    """Return the good tree with the file at path at another as well."""
    files = good()
    files[to] = files[path]
    return files


def refiled(directory: str, commit: str) -> tuple[Files, str]:
    """Return the good tree with a record moved to a directory, and its path.

    The directory holds the series.json of a commit that came from no message.
    """
    path = f"{directory}/checkpatch.pl.json"
    files = moved(CHECKPATCH, path)
    files[f"{directory}/series.json"] = series(commit, "", commit)
    return files, path


def outputs(first: str, second: str) -> list[tuple[str, str]]:
    """Return the problems of two entries that differ in their output alone."""
    files = good()
    data = at(files, SPARSE)
    run = data["entries"][0]
    data["entries"] = [{**run, "output": output} for output in (first, second)]
    return problems(files)


class Validate(unittest.TestCase):
    def assert_problem(self, files: Files, path: str, problem: str) -> None:
        """Assert that one file breaks the format, and in one way."""
        found = problems(files)
        self.assertEqual([where for where, _ in found], [path], found)
        self.assertIn(problem, found[0][1])

    def test_good(self) -> None:
        self.assertEqual(problems(good()), [])

    def test_schemas(self) -> None:
        for kind, schema in validate.SCHEMAS.items():
            with self.subTest(kind=kind):
                Draft7Validator.check_schema(schema.schema)

    # A record under the commit, of a tool that reads no build.

    def test_commit_record_names_build(self) -> None:
        files = good()
        at(files, CHECKPATCH)["subject"]["build"] = build(TINY)
        self.assert_problem(files, CHECKPATCH, f"lies outside its build, {TINY}/")

    def test_range_ends_at_another_commit(self) -> None:
        files = good()
        at(files, CHECKPATCH)["subject"]["range"] = f"{TIP}..{BASE}"
        self.assert_problem(files, CHECKPATCH, "does not end at its commit")

    def test_commit_record_has_config(self) -> None:
        files = good()
        first_entry(files, HTMLDOCS)["config"] = CONFIG
        self.assert_problem(files, HTMLDOCS, "entry 1 has a config")

    def test_series_record_has_config(self) -> None:
        files = good()
        first_entry(files, SHAZAM)["config"] = CONFIG
        self.assert_problem(files, SHAZAM, "entry 1 has a config")

    # A record under a build.

    def test_build_record_names_no_build(self) -> None:
        files = good()
        del at(files, SPARSE)["subject"]["build"]
        self.assert_problem(files, SPARSE, "a build that its subject does not name")

    def test_build_record_names_other_build(self) -> None:
        files = good()
        at(files, SPARSE)["subject"]["build"]["compiler"] = "gcc"
        named = "tinyconfig/x86_64/gcc"
        self.assert_problem(files, SPARSE, f"lies outside its build, {named}/")

    def test_build_record_has_no_config(self) -> None:
        files = good()
        del first_entry(files, TEST)["config"]
        self.assert_problem(files, TEST, "entry 1 has no config")

    def test_configuration_order(self) -> None:
        files = good()
        profiles = "nvm-express-driver/block-layer/x86_64/gcc"
        path = NESTED.replace(PROFILES, profiles)
        del files[NESTED]
        files[path] = built(profiles, entry("make -s", GCC, config=CONFIG))
        self.assert_problem(files, path, "names its configuration out of order")

    def test_arch(self) -> None:
        # uname calls riscv64 the machine that kbuild builds as riscv.
        files = good()
        path = NESTED.replace("x86_64", "riscv64")
        del files[NESTED]
        files[path] = built(PROFILES.replace("x86_64", "riscv64"), entry("make -s"))
        # A directory of no ARCH is no build, so the record lies under the commit.
        named = "names riscv64, which is no ARCH of kbuild's"
        self.assertEqual(problems(files), [(path, UNKNOWN), (path, named)])

    def test_arch_alias(self) -> None:
        files = good()
        path = NESTED.replace("x86_64", "sparc64")
        del files[NESTED]
        sparc = entry("make -s", {"ARCH": "sparc64"}, config=CONFIG)
        files[path] = built(PROFILES.replace("x86_64", "sparc64"), sparc)
        self.assertEqual(problems(files), [])

    def test_env_arch(self) -> None:
        # The .config names the machine, so the ARCH an entry sets, of make
        # or of any other tool, names no build.
        files = good()
        first_entry(files, SPARSE)["env"] = {"ARCH": "arm64", "LLVM": "1"}
        first_entry(files, TEST)["env"] = {"ARCH": "arm64", "FSTYP": "xfs"}
        self.assertEqual(problems(files), [])

    def test_env_llvm(self) -> None:
        files = good()
        first_entry(files, NESTED)["env"] = CLANG
        self.assert_problem(files, NESTED, OTHER_BUILD)

    def test_env_without_llvm_names_no_compiler(self) -> None:
        files = good()
        first_entry(files, SPARSE)["env"] = GCC
        self.assertEqual(problems(files), [])

    def test_env_without_arch(self) -> None:
        # A run that sets no ARCH builds as its .config says: a tinyconfig
        # of an x86_64 host is i386.
        native = "tinyconfig/i386/gcc"
        path = f"{COMMIT}/{native}/make/all.json"
        for env in ({}, {"ARCH": ""}):
            with self.subTest(env=env):
                files = good()
                files[path] = built(native, entry("make -s", env, config=CONFIG))
                self.assertEqual(problems(files), [])

    def test_env_llvm_suffix(self) -> None:
        files = good()
        first_entry(files, SPARSE)["env"] = {"ARCH": "x86_64", "LLVM": "-21"}
        self.assertEqual(problems(files), [])

    def test_record_name(self) -> None:
        for path, key, target in (
            (SPARSE, "file", "init/calibrate.o"),
            (SPARSE, "file", "it/main.o"),
            (KERNEL_DOC, "file", "init/main.h"),
            (TEST, "test", "generic/002"),
        ):
            with self.subTest(target=target):
                files = good()
                at(files, path)["subject"][key] = target
                self.assert_problem(files, path, f"is not named for {target}")

    # A build's objects.json.

    def test_objects_commit(self) -> None:
        files = good()
        at(files, OBJECTS)["commit"] = BASE
        self.assert_problem(files, OBJECTS, f"which does not name {BASE}")

    def test_objects_build(self) -> None:
        files = good()
        at(files, OBJECTS)["build"]["arch"] = "arm64"
        named = "tinyconfig/arm64/clang"
        self.assert_problem(files, OBJECTS, f"lies outside its build, {named}/")

    def test_objects_names_no_build(self) -> None:
        files = good()
        del at(files, OBJECTS)["build"]
        self.assert_problem(files, OBJECTS, "'build' is a required property")

    # Two entries of one run.

    def test_same_run_twice(self) -> None:
        self.assertEqual(outputs("a.c:1: x\n", "a.c:1: x\n"), SAME_RUN)

    def test_same_run_by_another_lab(self) -> None:
        files = good()
        entries = at(files, SPARSE)["entries"]
        entries.append({**entries[0], "invocations": [invocation()]})
        entries[1]["invocations"][0]["origin"] = "lab-b"
        self.assertEqual(problems(files), SAME_RUN)

    def test_same_run_in_other_order(self) -> None:
        found = outputs("a.c:1: x\nb.c:2: y\n", "b.c:2: y\na.c:1: x\n")
        self.assertEqual(found, SAME_RUN)

    def test_same_run_at_other_time(self) -> None:
        for first, second in (
            ("[ 6405.509403] BUG: x\n", "[   12.000001] BUG: x\n"),
            ("pc at 0xffffffff81000000\n", "pc at 0xffffffff82345678\n"),
            ("--- a/x.out\t2026-01-02 03:04:05\n", "--- a/x.out\t2026-02-03\n"),
            ("+++ b/x y.out\t2026-01-02 03:04:05\n", "+++ b/x y.out\t2026-02-03\n"),
        ):
            with self.subTest(first=first):
                self.assertEqual(outputs(first, second), SAME_RUN)

    def test_other_output_is_another_run(self) -> None:
        for first, second in (
            ("a.c:1: x\nb.c:2: y\n", "a.c:1: x\nb.c:2: z\n"),
            ("a.c:1: x\n", "a.c:1: x\na.c:1: x\n"),
            ("mask 0x1f\n", "mask 0x2f\n"),
            ("--- a/x.out\t2026-01-02\n", "--- a/y.out\t2026-01-02\n"),
            ("a.c:1: --- x\ty\n", "a.c:1: --- x\tz\n"),
            ("BUG: [ 6405.509403] x\n", "BUG: [   12.000001] x\n"),
        ):
            with self.subTest(first=first):
                self.assertEqual(outputs(first, second), [])

    def test_other_field_is_another_run(self) -> None:
        changes: Json = {
            "tools": [{"name": "tool", "version": "1.0", "build": "v1.1"}],
            "command": "make -s C=2 CF=-Wsparse-error init/main.o",
            "env": {**CLANG, "KCFLAGS": "-O3"},
            "config": OTHER_CONFIG,
            "status": "error",
            "exit": 2,
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                files = good()
                entries = at(files, SPARSE)["entries"]
                # A pass has one exit status, so the run is of sparse failing.
                entries[0]["status"] = "fail"
                entries.append({**entries[0], field: value})
                self.assertEqual(problems(files), [])

    # Where a record lies.

    def test_project(self) -> None:
        files = good()
        at(files, CHECKPATCH)["subject"]["project"] = "other"
        self.assert_problem(files, CHECKPATCH, "lies under linux/, not other/")

    def test_series_under_commit(self) -> None:
        files = moved(SHAZAM, CHECKPATCH)
        self.assert_problem(files, CHECKPATCH, "its subject is a series")

    def test_commit_under_series(self) -> None:
        files = moved(CHECKPATCH, SHAZAM)
        self.assert_problem(files, SHAZAM, "its subject is a commit")

    def test_other_message_id(self) -> None:
        files = good()
        at(files, SHAZAM)["subject"]["message_id"] = APPLIED
        self.assert_problem(files, SHAZAM, f"lies under {UNAPPLIED}/, not {APPLIED}/")

    def test_described_directory(self) -> None:
        files, path = refiled("linux/v1.2-3-gfedcba987654", OTHER)
        self.assert_problem(files, path, f"does not name {TIP}")

    def test_abbreviated_directory(self) -> None:
        files, _ = refiled("linux/0123456789ab", TIP)
        self.assertEqual(problems(files), [])
        files, path = refiled("linux/fedcba987654", OTHER)
        self.assert_problem(files, path, f"does not name {TIP}")

    def test_tag_directory(self) -> None:
        files, _ = refiled("linux/v1.3", TIP)
        self.assertEqual(problems(files), [])

    def test_file_above_a_commit(self) -> None:
        for path in ("README", "linux/README", "linux/series.json"):
            with self.subTest(path=path):
                files = good()
                files[path] = "{}"
                self.assert_problem(files, path, "lies under no commit or series")

    def test_file_in_another_place(self) -> None:
        for file, path in (
            (SERIES, f"{COMMIT}/{TINY}/series.json"),
            (SERIES, f"linux/{APPLIED}/series.json"),
            (OBJECTS, f"{COMMIT}/objects.json"),
            (OBJECTS, f"linux/{APPLIED}/{TINY}/objects.json"),
        ):
            with self.subTest(path=path):
                found = problems(copied(file, path))
                self.assertEqual({where for where, _ in found}, {path})

    # A commit's series.json.

    def test_series_directory(self) -> None:
        path = SERIES.replace("g0123456789ab", "gfedcba987654")
        self.assert_problem(copied(SERIES, path), path, f"does not name {TIP}")

    def test_commit_without_series(self) -> None:
        files = good()
        del files[SERIES]
        self.assert_problem(files, SERIES, "is missing; each commit that has a record")

    def test_build_without_series(self) -> None:
        files: Files = {MAKE: good()[MAKE]}
        self.assert_problem(files, SERIES, "is missing; each commit that has a record")

    def test_commit_without_records(self) -> None:
        tiny = f"{COMMIT}/{TINY}/"
        files = {path: data for path, data in good().items() if path.startswith(tiny)}
        del files[MAKE], files[SPARSE]
        self.assertEqual(problems(files), [])

    def test_commit_from_no_message(self) -> None:
        files = good()
        files[SERIES] = series(TIP, "", TIP)
        self.assertEqual(problems(files), [])

    def test_series_last_commit(self) -> None:
        files = good()
        commits = at(files, SERIES)["commits"]
        commits.append({**commits[0], "commit": BASE})
        self.assert_problem(files, SERIES, "neither its last one nor")

    def test_series_of_a_base(self) -> None:
        files = good()
        at(files, BASE_SERIES)["commit"] = TIP
        self.assert_problem(files, BASE_SERIES, "neither its last one nor")

    def test_series_without_lists(self) -> None:
        files = good()
        del at(files, SERIES)["lists"]
        self.assert_problem(files, SERIES, "'lists' is a required property")

    def test_series_list_not_lowercase(self) -> None:
        files = good()
        at(files, SERIES)["lists"] = ["Linux-Kernel@vger.kernel.org"]
        self.assert_problem(files, SERIES, "lists/0: 'Linux-Kernel@vger.kernel.org'")

    # A record's command (commands.json).

    def test_commands(self) -> None:
        listed: Json = json.loads((validate.HERE / "commands.json").read_text("utf-8"))
        self.assertEqual(set(listed), {"schema", "projects"})
        self.assertTrue(listed["projects"])
        for project, of in listed["projects"].items():
            self.assertEqual(set(of), {"env", "commands"})
            self.assertTrue(of["commands"])
            for names in of["env"].values():
                self.assertTrue(all(isinstance(name, str) for name in names))
            for command in of["commands"]:
                with self.subTest(project=project, command=command):
                    self.check_command(command)

    def check_command(self, command: Json) -> None:
        self.assertEqual(set(command) - {"argv"}, {"command", "record", "under"})
        self.assertIsInstance(command.get("argv", ""), str)
        self.assertIsInstance(command["command"], str)
        self.assertIn(command["under"], ("build", "commit", "series"))
        self.assertIsInstance(command["record"], str)
        self.assertRegex(command["record"], r"\A[^/]+(/[^/]+)*\.json\Z")

    def test_unknown_command(self) -> None:
        for path, to in (
            (CHECKPATCH, f"{COMMIT}/checkpatch.json"),
            (HTMLDOCS, f"{COMMIT}/make/allconfig/filesystems.json"),
            (MAKE, f"{COMMIT}/{TINY}/make/modules.json"),
            (SPARSE, f"{COMMIT}/{TINY}/lint/init/main.o.json"),
            (SHAZAM, f"linux/{UNAPPLIED}/b4/am.json"),
        ):
            with self.subTest(to=to):
                self.assertEqual(problems(moved(path, to)), [(to, UNKNOWN)])

    def test_project_without_builds(self) -> None:
        files = good()
        commit = {"project": "qemu", "commit": TIP}
        configure = entry("configure --target-list=x86_64-softmmu", {"CC": "clang"})
        files[f"qemu/{TIP}/series.json"] = series(TIP, "", TIP)
        files[f"qemu/{TIP}/configure.json"] = record(commit, configure)
        # An ARCH names no build where there is none.
        files[f"qemu/{TIP}/make/all.json"] = record(commit, entry("make", GCC))
        self.assertEqual(problems(files), [])
        # Each project has its own commands.
        path = f"qemu/{TIP}/sparse/init/main.o.json"
        files[path] = record(commit, entry("make -s C=2 init/main.o"))
        self.assertEqual(problems(files), [(path, UNKNOWN)])

    def test_unknown_project(self) -> None:
        files = good()
        path = f"xen/{TIP}/make/all.json"
        files[f"xen/{TIP}/series.json"] = series(TIP, "", TIP)
        files[path] = record({"project": "xen", "commit": TIP}, entry("make"))
        self.assertEqual(problems(files), [(path, NO_PROJECT)])

    def test_command_named_in_part(self) -> None:
        path = f"{COMMIT}/{TINY}/make/tinyconfig.json"
        files = good()
        files[path] = built(TINY, entry("make -s tinyconfig", CLANG, config=CONFIG))
        self.assertEqual(problems(files), [])

    def test_command_of_another_level(self) -> None:
        checkpatch = f"scripts/checkpatch.pl --git {BASE}..{TIP}"
        patch = built(TINY, entry(checkpatch, config=CONFIG))
        sent = record({"message_id": APPLIED}, entry(checkpatch))
        make = record({"commit": TIP}, entry("make -s", GCC))
        shazam = record({"commit": TIP}, entry(f"b4 shazam {APPLIED}"))
        cases: tuple[tuple[str, Json, str, str], ...] = (
            (f"{COMMIT}/{TINY}/checkpatch.pl.json", patch, "a build", "the commit"),
            (f"linux/{APPLIED}/checkpatch.pl.json", sent, "a series", "the commit"),
            (f"{COMMIT}/make/all.json", make, "the commit", "a build"),
            (f"{COMMIT}/b4/shazam.json", shazam, "the commit", "a series"),
        )
        for path, data, here, there in cases:
            with self.subTest(path=path):
                files = good()
                files[path] = data
                problem = f"lies under {here}, and commands.json has it under {there}"
                self.assert_problem(files, path, problem)

    # An entry's verdict.

    def test_pass_with_exit(self) -> None:
        files = good()
        first_entry(files, CHECKPATCH)["exit"] = 2
        problem = "entry 1 is a pass, and its exit status is 2"
        self.assert_problem(files, CHECKPATCH, problem)

    def test_fail_with_exit_0(self) -> None:
        files = good()
        first_entry(files, CHECKPATCH).update(status="fail", output="WARNING: x\n")
        self.assertEqual(problems(files), [])

    def test_skip_without_output(self) -> None:
        files = good()
        first_entry(files, TEST)["status"] = "skip"
        self.assert_problem(files, TEST, "entry 1 is a skip, and has no output")

    def test_skip_with_its_reason(self) -> None:
        files = good()
        first_entry(files, TEST).update(status="skip", output="no scratch device\n")
        self.assertEqual(problems(files), [])

    # What a record may hold.

    def test_home(self) -> None:
        leaks: Json = {
            "command": "make -s -C /home/dev/linux C=2 init/main.o",
            "output": "/Users/dev/linux/init/main.c:15:2: warning: x\n",
            "env": {**CLANG, "KCFLAGS": "-I/home/dev/include"},
        }
        for field, leak in leaks.items():
            with self.subTest(field=field):
                files = good()
                first_entry(files, SPARSE)[field] = leak
                self.assert_problem(files, SPARSE, "entry 1 names a home directory")

    def test_home_where_a_path_starts(self) -> None:
        for output in (
            "cannot open \N{LEFT SINGLE QUOTATION MARK}/home/dev/main.s\n",
            "make KCFLAGS=-I/home/dev/include\n",
            "-/home/dev/linux/init/main.c\n",
            "see file:///Users/dev/linux/README\n",
        ):
            with self.subTest(output=output):
                files = good()
                first_entry(files, SPARSE)["output"] = output
                self.assert_problem(files, SPARSE, "entry 1 names a home directory")

    def test_home_inside_a_path(self) -> None:
        files = good()
        first_entry(files, SPARSE)["output"] = "~/linux/fs/home/dev.c:1: warning: x\n"
        self.assertEqual(problems(files), [])

    def test_unknown_version(self) -> None:
        files = good()
        at(files, SPARSE)["schema"] = 2
        self.assert_problem(files, SPARSE, "schema 2 is not a version")

    def test_no_version(self) -> None:
        files = good()
        del at(files, SERIES)["schema"]
        self.assert_problem(files, SERIES, "has no integer schema")

    def test_not_json(self) -> None:
        files = good()
        files[SPARSE] = "{"
        self.assert_problem(files, SPARSE, "is not JSON")

    def test_not_an_object(self) -> None:
        files = good()
        files[SPARSE] = "[]"
        self.assert_problem(files, SPARSE, "is not a JSON object")

    def test_host_name(self) -> None:
        files = good()
        first_entry(files, TEST)["invocations"][0]["machine"]["hostname"] = "host-1"
        self.assert_problem(files, TEST, "'hostname' was unexpected")

    def test_invocation_field(self) -> None:
        files = good()
        del first_entry(files, TEST)["invocations"][0]["run"]
        self.assert_problem(files, TEST, "'run' is a required property")

    def test_subject_field(self) -> None:
        files = good()
        del at(files, SPARSE)["subject"]["build"]["compiler"]
        self.assert_problem(files, SPARSE, "subject/build: 'compiler' is a required")

    def test_line_break_after_commit(self) -> None:
        files = good()
        at(files, CHECKPATCH)["subject"]["commit"] = TIP + "\n"
        self.assert_problem(files, CHECKPATCH, "subject/commit: ")

    # What a tree may hold.

    def test_stray_file(self) -> None:
        for path in (
            f"{COMMIT}/cover.mbx",
            f"{COMMIT}/config",
            f"{COMMIT}/{TINY}/make/config",
            f"{COMMIT}/{TINY}/.config",
            f"{COMMIT}/{TINY}/b4/mbox.mbx",
            f"linux/{APPLIED}/mbox.mbx",
            f"linux/{APPLIED}/config",
            f"linux/{APPLIED}/{TINY}/config",
        ):
            with self.subTest(path=path):
                files = good()
                files[path] = "CONFIG_EXAMPLE=y\n"
                self.assert_problem(files, path, "is not a file the format names")

    def test_symbolic_link(self) -> None:
        with tree(good()) as root:
            (root / COMMIT / "sparse.json").symlink_to(root / SPARSE)
            (root / "linux/v1.3").symlink_to(root / COMMIT, target_is_directory=True)
            found = list(validate.check(root))
        links = [f"{COMMIT}/sparse.json", "linux/v1.3"]
        self.assertEqual(found, [(link, "is a symbolic link") for link in links])

    def test_hidden_directory(self) -> None:
        files = good()
        files[".git/HEAD"] = "ref: refs/heads/records\n"
        files[f"{COMMIT}/.cache/stray.json"] = "{"
        self.assertEqual(problems(files), [])

    def test_linked_worktree(self) -> None:
        files = good()
        files[".git"] = "gitdir: ../.git/worktrees/records\n"
        self.assertEqual(problems(files), [])

    # The command.

    def run_main(self, files: Files) -> tuple[int, str]:
        printed = io.StringIO()
        with tree(files) as root, contextlib.redirect_stdout(printed):
            status = validate.main(["--records", str(root)])
        return status, printed.getvalue()

    def test_main(self) -> None:
        self.assertEqual(self.run_main(good()), (0, ""))

    def test_main_prints_problems(self) -> None:
        path = f"{COMMIT}/cover.mbx"
        files = good()
        files[path] = "From: A Developer <dev@example.org>\n\n"
        printed = f"{path}: is not a file the format names\n"
        self.assertEqual(self.run_main(files), (1, printed))

    def test_main_without_records(self) -> None:
        printed = io.StringIO()
        with (
            tree({}) as root,
            contextlib.redirect_stderr(printed),
            self.assertRaises(SystemExit) as raised,
        ):
            validate.main(["--records", str(root / "none")])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("is not a directory", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
