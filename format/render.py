#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1 AND MIT
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Render the records under --records as a static site in --output.

The site is plain HTML and CSS, every recorded string escaped. Its scripts
only search and keep settings, and every page reads the same without them.
A commit whose series.json lists commits is a series, set against its
base's records of the same build, and any other commit is a base.

    render.py --records ../records --output public
"""

import argparse
import dataclasses
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from email import policy
from email.errors import MessageError
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, parseaddr, parsedate_to_datetime
from enum import Enum
from html import escape
from itertools import groupby, pairwise, takewhile
from pathlib import Path
from typing import TypeIs
from urllib.parse import quote, unquote, urlsplit

from record import ARCHES, COMPILERS, unmoved

SCHEMA = 1
# A record's first directory is the tool that ran; make's, the build, comes
# first, any other by name.
MAKE = "make"
# The build itself: make with no target.
BUILD = "make/all"
# Where an earlier commit's build of a series shows on the series' page.
BISECT = "bisect"
# Positions, which move when a series moves code, dropped only to match a
# series' lines with its base's: :LINE[:COL[-COL]] after a file name, a
# compiler's source gutter ("  162 | "), a line named in prose ("jumps to
# line 4090") and the hash of a Nix store path, which moves when what it
# holds changes.
POSITION = re.compile(r"(\.[A-Za-z0-9]+):\d+(?::\d+(?:-\d+)?)?")
GUTTER = re.compile(r"^\s*\d+ \|")
LINE = re.compile(r"\bline \d+")
STORE = re.compile(r"/nix/store/[0-9a-z]{32}-")
# The head of a finding in a compiler's form, path:LINE[:COL]: and error,
# warning or fatal error, as clang, gcc and sparse print them; the lines up to
# the next head (notes, source) belong to it.
DIAGNOSTIC = re.compile(r"\S+:\d+(?::\d+)?: (?:error|warning|fatal error):")
# What a diff draws of a finding: its head and the source quoted under it,
# then its notes but those that only name a macro the line expands, as
# clang and gcc word them, and no source quoted under a note.
NOTE = re.compile(r"\S+:\d+(?::\d+)?: note:")
MACRO = re.compile(r"\S+: note: (?:expanded from|in (?:expansion|definition) of) macro")
QUOTED = re.compile(r"\s*\d* \|")
# The count clang prints ahead of its findings, which a diff leaves out.
GENERATED = re.compile(
    r"\d+ (?:warnings?|errors?)(?: and \d+ (?:warnings?|errors?))? generated\."
)
# Output lines a page shows; a longer output is linked whole beside it.
CUT = 50
# Lines of a series' output shown either side of what differs from its base's.
CONTEXT = 3
# A tree that holds a project's recorded commits, by the project's name,
# which a path:LINE in a record links to.
CODE = {
    "linux": "https://github.com/linux-kdevops/linux",
    "qemu": "https://gitlab.com/qemu-project/qemu",
}
# A project's name in the controls bar; any other shows as its directory.
PROJECTS = {"linux": "Linux kernel", "qemu": "QEMU"}
# The project whose listings are the site's root pages, when it has rows;
# without it the first by name is.
DEFAULT = "linux"
# The tree a base without base_trees is on, by its project and name: of
# linux a release or release candidate on torvalds', a stable release on
# stable, a linux-next tag on linux-next; any other name on CODE.
KERNEL_ORG = "https://git.kernel.org/pub/scm/linux/kernel/git/"
TAG_TREES = {
    "linux": (
        (
            re.compile(r"v\d+\.\d+(?:-rc\d+)?"),
            "torvalds",
            f"{KERNEL_ORG}torvalds/linux.git",
        ),
        (re.compile(r"v\d+\.\d+\.\d+"), "stable", f"{KERNEL_ORG}stable/linux.git"),
        (re.compile(r"next-\d{8}"), "linux-next", f"{KERNEL_ORG}next/linux-next.git"),
    )
}
# A file of the tree and a line of it as a tool prints them: the path from
# the tree, or from "../" under a build directory in it, then :LINE.
CITED = re.compile(r"(?<![\w/.-])(?:\.\.?/)?((?:[\w.+-]+/)*[\w+-][\w.+-]*\.\w+):(\d+)")
# A commit's name that no tag gives whole: git describe's hash after -g.
DESCRIBED = re.compile(r"-g([0-9a-f]{7,40})(?:-dirty)?$")
# What ends the name of a tree with changes not committed (README.md).
DIRTY = "-dirty"
# The directory, under a commit's on the site, of the page of its records
# that lie under no build. A configuration's name starts with a letter or a
# digit, so no build has it.
NO_BUILD_DIR = "-"
# What that page is called where a build is named.
NO_BUILD = "no build"
# The targets a configure of a tree with no build level was given.
TARGET_LIST = re.compile(r"configure\b.* --target-list=(\S+)")
# What an anchor of a record's key keeps; anything else becomes "-".
ANCHOR = re.compile(r"[^A-Za-z0-9._-]")
# The C0 controls but tab and newline, and DEL, as their Unicode control
# pictures, so that a lone carriage return cannot break a line on a page.
PICTURES = {code: 0x2400 + code for code in range(0x20) if code not in {0x09, 0x0A}}
PICTURES[0x7F] = 0x2421
# A Select Graphic Rendition sequence, ESC [ codes m, which a page draws as a
# terminal would, in the colour it sets; any other ESC shows as its picture.
SGR = re.compile(r"\x1b\[([0-9;]*)m")
# The SGR codes that set the foreground colour, "" and "0" resetting it, and
# the style.css class of each colour b4 prints; any other draws as plain text.
FOREGROUND = frozenset({"", "0", *map(str, range(30, 40)), *map(str, range(90, 98))})
COLOURS = {"31": "fail", "32": "pass", "33": "warn", "90": "none"}
# Codes that pick a colour by number: the parameters after them are its number.
EXTENDED = frozenset({"38", "48", "58"})
# The site's own files at its root, which a project must not be named, nor
# index-N.html (PAGED), nor either after another project's name and "-", as
# that project's listings are.
RESERVED = frozenset(
    {
        "index.html",
        "bases.html",
        "search.html",
        "about.html",
        "settings.html",
        "stats.html",
        "style.css",
        "theme.js",
        "search.js",
        "settings.js",
        "stats.js",
    }
)
# A build's .config, beside its page.
CONFIG = "config.txt"
# The index's series per page, as sashiko's; page N > 1 is index-N.html.
PER_PAGE = 50
PAGED = re.compile(r"index-\d+\.html")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
LORE = "https://lore.kernel.org/all/"
# Where a producer publishes its own commits, by its name: its version, when
# REVISION reads it as one, links there. Any other producer's shows as text.
KDEVOPS_NG_NAME = "kdevops-ng"
PRODUCER_COMMITS = {
    KDEVOPS_NG_NAME: "https://github.com/linux-kdevops/kdevops-ng/commit/"
}
REVISION = re.compile(r"([0-9a-f]{40})(-dirty)?")
# The project, as the site names and links itself.
NAME = "kresults"
HOME = "https://github.com/linux-kdevops/kresults"
CSP = (
    "default-src 'none'; style-src 'self'; script-src 'self'; base-uri 'none';"
    " form-action 'self'"
)

# The colour tokens, light and dark. The system's preference picks one unless
# settings.html set data-theme on <html>; data-colorblind swaps the verdict
# colours for sashiko's colorblind ones (Okabe-Ito), darker on light.
LIGHT = """
  --bg: #f5f5f5; --border: #ccc; --border-light: #eee; --text: #24292f;
  --text-muted: #666; --text-dim: #999; --link: #007bff; --hover: #e0e0e0;
  --code-bg: #f6f8fa; --code-border: #d0d7de; --card-bg: #fff; --th-bg: #fafafa;
  --status-error: #0366d6; --status-inreview: #d67f05;
  --status-reviewed: #22863a; --status-failed: #d73a49;
  --tag-bg: #e0f7fa; --tag-text: #006064;
  --diff-add-bg: #e6ffec; --diff-rm-bg: #ffebe9;
  --warn-bg: #fff3cd; --warn-text: #856404;
  --lineage-bg: #f1f5f9; --lineage-text: #334155; --lineage-border: #cbd5e1;
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a;
"""
DARK = """
  color-scheme: dark;
  --bg: #0d1117; --border: #30363d; --border-light: #21262d; --text: #c9d1d9;
  --text-muted: #8b949e; --text-dim: #6e7681; --link: #58a6ff; --hover: #21262d;
  --code-bg: #161b22; --code-border: #30363d; --card-bg: #161b22; --th-bg: #161b22;
  --status-error: #58a6ff; --status-inreview: #d29922;
  --status-reviewed: #3fb950; --status-failed: #f85149;
  --tag-bg: #032f62; --tag-text: #79c0ff;
  --diff-add-bg: rgba(46, 160, 67, .15); --diff-rm-bg: rgba(248, 81, 73, .15);
  --warn-bg: #3a2a00; --warn-text: #e5b300;
  --lineage-bg: rgba(148, 163, 184, .12); --lineage-text: #cbd5e1;
  --lineage-border: #475569;
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
"""
COLORBLIND_LIGHT = """
  --status-error: #336C8B; --status-inreview: #8A5F00;
  --status-reviewed: #004D73; --status-failed: #803800;
"""
COLORBLIND_DARK = """
  --status-error: #56B4E9; --status-inreview: #E69F00;
  --status-reviewed: #0072B2; --status-failed: #D55E00;
"""
SYSTEM_DARK = "@media (prefers-color-scheme: dark)"
TOKENS = (
    f":root {{ color-scheme: light;{LIGHT}}}\n"
    f"{SYSTEM_DARK} {{ :root:not([data-theme=light]) {{{DARK}}} }}\n"
    f":root[data-theme=dark] {{{DARK}}}\n"
    f":root[data-colorblind] {{{COLORBLIND_LIGHT}}}\n"
    f"{SYSTEM_DARK} {{ :root[data-colorblind]:not([data-theme=light]) {{"
    f"{COLORBLIND_DARK}}} }}\n"
    f":root[data-colorblind][data-theme=dark] {{{COLORBLIND_DARK}}}\n"
)

CSS = """
body { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
  monospace; font-size: 13px; line-height: 1.4; max-width: 1200px; margin: 0 auto;
  padding: 12px 16px; background: var(--bg); color: var(--text); }
pre, code { font-family: inherit; font-size: inherit; }
.wrap > * + * { margin-top: 16px; }
header { padding-bottom: 8px; border-bottom: 1px solid var(--border-light); }
header a { font-weight: 600; color: var(--text); }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 2em; margin: 0 0 16px; padding-bottom: 12px;
  border-bottom: 1px solid var(--border); overflow-wrap: anywhere; }
h2 { font-size: 1.1em; margin: 0 0 8px; }
p { margin: 0; }
.muted { color: var(--text-muted); }
.text-muted-sm { color: var(--text-muted); font-size: .85em; }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 6px 8px; vertical-align: top;
  border-bottom: 1px solid var(--border-light); }
th { font-weight: 600; background: var(--th-bg); }
tr:hover { background: var(--hover); }
/* A row of a table.rows is one link, as sashiko's rows are. Each cell is
   the containing block of its own cover: the main link's ::after in its
   cell, a row-cover to the same page in every other. Safari positions no
   tr, so nothing here may. The row's other links, and what names itself on
   hover, sit above the covers. */
table.rows tbody tr { cursor: pointer; }
table.rows tbody td { position: relative; }
a.row-link { color: inherit; text-decoration: none; }
a.row-link:hover { text-decoration: none; }
a.row-link::after { content: ""; position: absolute; inset: 0; }
a.row-cover { position: absolute; inset: 0; }
a.row-link:focus-visible { outline: none; }
a.row-link:focus-visible::after { outline: 2px solid var(--link);
  outline-offset: -2px; }
table.rows tbody tr:focus-within { background: var(--hover); }
table.rows a:not(.row-link, .row-cover), table.rows [title] {
  position: relative; z-index: 1; }
td.num, th.num { text-align: right; }
.tag { display: inline-block; background: var(--tag-bg); color: var(--tag-text);
  padding: 1px 4px; border-radius: 3px; font-size: .75em; margin-right: 4px;
  border: 1px solid var(--tag-bg); vertical-align: middle; }
.lists { margin-top: 2px; }
.badge { display: inline-block; font-size: .85em; padding: 0 4px;
  border-radius: 3px; border: 1px solid currentColor; white-space: nowrap; }
.pass { color: var(--status-reviewed); } .fail { color: var(--status-failed); }
.warn { color: var(--status-inreview); } .none { color: var(--text-dim); }
.error { color: var(--status-error); }
.suite { white-space: nowrap; margin-right: 8px; }
dl { display: grid; grid-template-columns: 120px 1fr; gap: 4px 8px; margin: 0; }
dt { font-weight: 600; color: var(--text-muted); }
dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
details { background: var(--card-bg); border: 1px solid var(--border-light);
  border-radius: 4px; }
details + details { margin-top: 4px; }
details:target { border-color: var(--link); box-shadow: 0 0 0 1px var(--link); }
details:target > summary { background: var(--hover); }
summary { cursor: pointer; padding: 4px 8px; display: flex; flex-wrap: wrap;
  gap: 4px 12px; align-items: baseline; }
.note, .step { padding: 4px 8px; overflow-wrap: anywhere; }
.step, summary + .note { border-top: 1px solid var(--border-light); }
.toggle { position: absolute; opacity: 0; pointer-events: none; }
.toggles label, .toggles a { display: inline-block; padding: 0 6px; margin-right: 6px;
  border: 1px solid var(--border); border-radius: 3px; cursor: pointer; }
.toggles label:hover { background: var(--hover); }
.out { display: none; }
.toggle.series:checked ~ .out.series, .toggle.base:checked ~ .out.base {
  display: block; }
.toggle.series:checked ~ .toggles .series, .toggle.base:checked ~ .toggles .base {
  background: var(--hover); border-color: var(--link); }
.toggle.series:focus-visible ~ .toggles .series,
.toggle.base:focus-visible ~ .toggles .base { outline: 2px solid var(--link); }
pre { margin: 0; padding: 8px; background: var(--code-bg); overflow-x: auto;
  border: 1px solid var(--code-border); }
pre .added, pre .gone { display: inline-block; min-width: 100%; }
pre .added { background: var(--diff-add-bg); }
pre .gone { background: var(--diff-rm-bg); }
pre .added::before, pre .gone::before, pre .same::before, pre .skip::before {
  user-select: none; color: var(--text-muted); }
pre .added::before { content: "+ "; }
pre .gone::before { content: "- "; }
pre .same::before { content: "  "; }
pre .skip::before { content: "\\2026"; }
.controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
  margin-top: 8px; position: relative; z-index: 2; }
.controls input { flex: 1; min-width: 140px; max-width: 360px; padding: 4px 8px;
  font: inherit; color: var(--text); background: var(--card-bg);
  border: 1px solid var(--border); border-radius: 3px; }
.views { display: flex; gap: 8px; margin-left: auto; }
.button { display: inline-block; padding: 4px 10px; font: inherit; color: var(--text);
  background: var(--card-bg); border: 1px solid var(--border); border-radius: 3px;
  cursor: pointer; }
a.button:hover, button.button:hover { background: var(--hover);
  text-decoration: none; }
.button.active { background: var(--border); font-weight: bold; }
.button.disabled { opacity: .5; cursor: not-allowed; }
.pagination { display: flex; justify-content: center; align-items: center; gap: 8px;
  margin-top: 12px; font-size: .9em; position: relative; z-index: 2; }
footer { color: var(--text-muted); font-size: .85em;
  border-top: 1px solid var(--border-light); padding-top: 8px; }
table.fixed { table-layout: fixed; min-width: 1000px; }
table.fixed td { overflow-wrap: anywhere; }
col.subject { width: auto; }
col.author { width: 180px; } col.date { width: 110px; } col.parts { width: 50px; }
col.base { width: 200px; } col.findings { width: 300px; }
/* The bases' columns are as wide as what they hold: the commit and when it was
   recorded on one line, the builds and the producers sharing the rest. */
table.bases { min-width: 800px; }
table.bases td:not(:nth-child(2), :last-child) { white-space: nowrap; }
.versions { margin-top: 2px; }
.lineage-badge { display: inline-block; margin-right: 6px; padding: 0 7px;
  border-radius: 10px; border: 1px solid var(--lineage-border);
  background: var(--lineage-bg); color: var(--lineage-text); font-size: .75em;
  line-height: 1.6; text-decoration: none; vertical-align: middle;
  white-space: nowrap; }
a.lineage-badge:hover { border-color: var(--lineage-text); text-decoration: none; }
.lineage-badge.current { border-color: var(--lineage-text); font-weight: 600; }
.stats-container { display: flex; align-items: center; flex-wrap: wrap; gap: 12px;
  font-size: .9em; color: var(--text-muted); border-radius: 4px; padding: 8px 12px;
  margin-top: 20px; }
.stats-group { display: flex; align-items: center; gap: 6px; }
.stats-sep { width: 1px; height: 16px; background: var(--border-light);
  margin: 0 4px; }
.stats-label { font-weight: 600; color: var(--text); }
.stats-val { font-weight: bold; color: var(--text); }
.stats-sub { font-size: .85em; opacity: .7; }
.stats-link { font-weight: 500; }
.stat-card-grid { display: grid; gap: 16px;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
.stat-card { background: var(--card-bg); border: 1px solid var(--border);
  padding: 12px; border-radius: 4px; }
.stat-card h3 { margin: 0 0 8px; font-size: .9em; text-transform: uppercase;
  color: var(--text-muted); letter-spacing: .5px; }
.stat-card .value { font-size: 1.8em; font-weight: bold; }
.chart-card { background: var(--card-bg); border: 1px solid var(--border-light);
  border-radius: 4px; padding: 12px; }
svg.chart { display: block; width: 100%; height: auto; }
svg.chart text { fill: var(--text-muted); font-size: 11px; font-family: inherit; }
svg.chart text.label { fill: var(--text); }
svg.chart .grid { stroke: var(--border-light); stroke-width: 1; }
svg.chart .axis { stroke: var(--border); stroke-width: 1; }
svg.chart .ring { stroke: var(--card-bg); stroke-width: 2; }
svg.chart .hit { fill: transparent; }
svg.chart a.mark { cursor: pointer; }
svg.chart a.mark:focus-visible { outline: 2px solid var(--link); }
svg.chart .marks:hover a.mark:not(:hover) { opacity: .35; }
.s1 { fill: var(--series-1); } .s2 { fill: var(--series-2); }
.s3 { fill: var(--series-3); }
.o-introduced { fill: var(--status-failed); } .o-clean { fill: var(--status-reviewed); }
.o-unapplied { fill: var(--text-dim); }
.legend { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 0 0 8px;
  padding: 0; list-style: none; color: var(--text-muted); font-size: .9em; }
.legend svg { width: 10px; height: 10px; margin-right: 6px; vertical-align: -1px; }
.table-view { margin-top: 8px; border: none; background: none; }
.table-view summary { padding: 0; color: var(--text-muted); }
.chart-tip { position: fixed; z-index: 3; pointer-events: none; padding: 4px 8px;
  background: var(--card-bg); color: var(--text); border: 1px solid var(--border);
  border-radius: 4px; font-size: .9em; white-space: pre; }
.settings { display: grid; grid-template-columns: max-content max-content;
  gap: 12px 24px; align-items: center; }
/* About and Settings open as sashiko's modals, as popovers. A browser
   without them keeps the .modal hidden and, by @supports, the link to the
   page in place of the button; with no CSS, the button stays hidden. */
button.stats-link { padding: 0; font: inherit; color: var(--link);
  background: none; border: none; cursor: pointer; }
button.stats-link:hover { text-decoration: underline; }
@supports selector(:popover-open) {
  button.stats-link[hidden] { display: inline; }
  a.stats-link.page { display: none; }
}
.modal { display: none; }
.modal:popover-open { display: block; position: fixed; inset: 0; margin: auto;
  max-width: 600px; width: 90%; height: fit-content; max-height: 90vh;
  overflow-y: auto; padding: 32px; border-radius: 8px; color: var(--text);
  background: var(--bg); border: 1px solid var(--border-light);
  box-shadow: 0 4px 12px rgba(0, 0, 0, .15); }
.modal::backdrop { background: rgba(0, 0, 0, .5); }
.stack > * + * { margin-top: 12px; }
.modal-title { margin: 0; font-size: 1.4em; padding-bottom: 12px;
  border-bottom: 1px solid var(--border); }
.modal-close { position: absolute; top: 16px; right: 16px; padding: 4px;
  font: inherit; font-size: 1.5em; line-height: 1; color: var(--text-muted);
  background: none; border: none; cursor: pointer; }
.modal-close:hover { color: var(--text); }
.github { text-align: center; margin-top: 24px; }
.github a { display: inline-flex; align-items: center; gap: 8px; font-weight: bold;
  color: var(--text); padding: 8px 16px; border: 1px solid var(--border);
  border-radius: 6px; background: var(--card-bg);
  box-shadow: 0 1px 2px rgba(0, 0, 0, .05); }
.github a:hover { background: var(--hover); text-decoration: none; }
.github svg { width: 20px; height: 20px; fill: currentColor; }
"""
STYLE = TOKENS + CSS

# Every page's first script, in its head: the settings saved by settings.html,
# as data-theme and data-colorblind on <html>. Without it, or without the
# browser's storage, the site follows the system's theme.
THEME = """\
"use strict";
try {
  const theme = localStorage.getItem("kresults.theme");
  if (theme === "light" || theme === "dark") {
    document.documentElement.dataset.theme = theme;
  }
  if (localStorage.getItem("kresults.colorblind") === "1") {
    document.documentElement.dataset.colorblind = "";
  }
} catch {
  // No storage: the system's theme.
}
// The controls bar's project opens the page chosen, the same view of that
// project, and leaves this page's own selected for when it is shown again.
// Its list submits its search at once, as sashiko's does.
document.addEventListener("change", (event) => {
  if (event.target.id === "project") {
    const page = event.target.value;
    event.target.querySelector("[selected]").selected = true;
    location.assign(page);
  }
  if (event.target.id === "list") event.target.form.submit();
});
"""

# Every root page's script: it shows the settings, wherever settings_html put
# them (the Settings popover, or settings.html), saves each change and applies
# it at once, as THEME does on every page. A block keeps its names its own.
SETTINGS = """\
"use strict";
{
  const root = document.documentElement;
  const theme = document.getElementById("theme");
  const colorblind = document.getElementById("colorblind");
  const save = (key, value) => {
    try {
      localStorage.setItem(`kresults.${key}`, value);
    } catch {
      // No storage: the change lasts until the page is left.
    }
  };
  if (theme && colorblind) {
    theme.value = root.dataset.theme || "auto";
    colorblind.checked = "colorblind" in root.dataset;
    theme.addEventListener("change", () => {
      save("theme", theme.value);
      if (theme.value === "auto") delete root.dataset.theme;
      else root.dataset.theme = theme.value;
    });
    colorblind.addEventListener("change", () => {
      save("colorblind", colorblind.checked ? "1" : "0");
      if (colorblind.checked) root.dataset.colorblind = "";
      else delete root.dataset.colorblind;
    });
    document.getElementById("settings-form").hidden = false;
    document.getElementById("settings-nojs").hidden = true;
  }
}
"""

# stats.html's script: one tooltip for every chart mark, from the mark's own
# <title>, which it takes over so the browser's own does not show twice.
# Without it the <title> is the tooltip.
STATS = """\
"use strict";
const tip = document.createElement("div");
tip.className = "chart-tip";
tip.hidden = true;
document.body.append(tip);
function show(mark, x, y) {
  tip.textContent = mark.dataset.tip;
  tip.hidden = false;
  tip.style.left = `${Math.min(x + 12, innerWidth - tip.offsetWidth - 8)}px`;
  tip.style.top = `${y + 12}px`;
}
for (const mark of document.querySelectorAll("svg.chart a.mark")) {
  const title = mark.querySelector("title");
  mark.dataset.tip = title.textContent;
  title.remove();
  mark.addEventListener("pointermove", (event) => {
    show(mark, event.clientX, event.clientY);
  });
  mark.addEventListener("focus", () => {
    const box = mark.getBoundingClientRect();
    show(mark, box.right, box.top);
  });
  for (const name of ["pointerleave", "blur"]) {
    mark.addEventListener(name, () => {
      tip.hidden = true;
    });
  }
}
"""

# search.html's script: it hides the rows whose data-search lacks a word
# typed, or given as ?q=, and a table none of whose rows match, and keeps ?q=
# in the address, so a search can be linked. A filter is of series alone.
# Without it every row shows.
SEARCH = """\
"use strict";
const box = document.getElementById("search");
const list = document.getElementById("list");
const rows = document.querySelectorAll("tr[data-search]");
const bases = document.getElementById("bases");
const series = document.getElementById("series");
const note = document.getElementById("filters");
// Each filter of the address and the row attribute that must hold its value.
const FILTERS = { list: "lists", week: "week", outcome: "outcome", check: "checks" };
const query = new URLSearchParams(location.search);
const chosen = {};
for (const name of Object.keys(FILTERS)) chosen[name] = query.get(name) || "";
function holds(row, name) {
  const value = row.dataset[FILTERS[name]];
  return value !== undefined && value.split(" ").includes(chosen[name]);
}
function filter() {
  chosen.list = list.value;
  const words = box.value.toLowerCase().split(/\\s+/).filter(Boolean);
  const narrowed = Object.keys(FILTERS).filter((name) => chosen[name]);
  for (const row of rows) {
    const found = words.every((word) => row.dataset.search.includes(word));
    row.hidden = !found || !narrowed.every((name) => holds(row, name));
  }
  const shown = series.querySelector("tr[data-search]:not([hidden])");
  const sought = words.length > 0;
  series.hidden = (sought || narrowed.length > 0) && !shown;
  const named = narrowed.filter((name) => name !== "list");
  const label = named.map((name) => `${name} ${chosen[name]}`).join(", ");
  note.textContent = `Filtered by ${label}. Clear shows every series.`;
  note.hidden = named.length === 0;
  const base = bases.querySelector("tr[data-search]:not([hidden])");
  bases.hidden = narrowed.length > 0 || (sought && !base);
  const address = new URL(location.href);
  address.searchParams.set("q", box.value);
  for (const name of Object.keys(FILTERS)) {
    if (chosen[name]) address.searchParams.set(name, chosen[name]);
    else address.searchParams.delete(name);
  }
  try {
    history.replaceState(null, "", address);
  } catch {
    // A file:// page may not change its address; the search still works.
  }
}
box.value = query.get("q") || "";
list.value = chosen.list;
if (list.value !== chosen.list) list.value = "";
box.addEventListener("input", filter);
filter();
"""


# Load: the records tree as typed values; nothing here renders.


class Status(Enum):
    PASSED = "pass"
    FAILED = "fail"
    # A test that did not run, with the reason as its output.
    SKIPPED = "skip"
    # The tool did not finish for a cause outside the code: no verdict.
    ERROR = "error"


class RecordError(ValueError):
    """Why a JSON file under a tree cannot be read as a record."""


class RenderError(Exception):
    """Why render writes nothing."""


@dataclass(frozen=True, slots=True)
class Host:
    """The machine a command ran on, as an invocation names it.

    machine is its arch, cpus how many CPUs the command could use, memory
    in bytes.
    """

    machine: str
    cpu: str
    cpus: int
    memory: int

    @property
    def label(self) -> str:
        """Its machine type: the CPU, how many, and the memory to a GiB."""
        return f"{self.cpu}, {self.cpus} CPUs, {round(self.memory / 2**30)} GiB"


@dataclass(frozen=True, slots=True)
class Tool:
    """A tool a command ran: the version it prints, and its exact build."""

    name: str
    version: str
    build: str
    # Where the source its build names can be read, or "". Two entries that
    # differ in it alone ran the same.
    url: str = field(default="", compare=False)

    @property
    def named(self) -> str:
        """Its name, then the version it prints when it prints one."""
        return f"{self.name} {self.version}".rstrip()


@dataclass(frozen=True, slots=True, order=True)
class Producer:
    """What ran a command, as an invocation names it."""

    name: str
    version: str

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}"


class Measure(Enum):
    """What a figure of an invocation's resources counts."""

    SECONDS = "seconds"
    BYTES = "bytes"
    COUNT = "count"


# An invocation's resources as record.schema.json names them: each group
# with what a page calls it, then each of its members the same way.
RESOURCES: Mapping[str, tuple[str, Mapping[str, tuple[str, Measure]]]] = {
    "cpu": (
        "CPU",
        {
            "seconds": ("time", Measure.SECONDS),
            "user_seconds": ("user", Measure.SECONDS),
            "system_seconds": ("system", Measure.SECONDS),
            "stalled_seconds": ("stalled", Measure.SECONDS),
        },
    ),
    "memory": (
        "memory",
        {
            "peak": ("peak", Measure.BYTES),
            "swap_peak": ("swap peak", Measure.BYTES),
            "stalled_seconds": ("stalled", Measure.SECONDS),
            "high_events": ("high events", Measure.COUNT),
            "max_events": ("max events", Measure.COUNT),
            "oom_kills": ("OOM kills", Measure.COUNT),
            "major_faults": ("major faults", Measure.COUNT),
        },
    ),
    "io": (
        "I/O",
        {
            "stalled_seconds": ("stalled", Measure.SECONDS),
            "read_bytes": ("read", Measure.BYTES),
            "write_bytes": ("written", Measure.BYTES),
            "read_operations": ("reads", Measure.COUNT),
            "write_operations": ("writes", Measure.COUNT),
        },
    ),
    "tasks": ("tasks", {"peak": ("peak", Measure.COUNT)}),
}
BYTE_UNITS = ("KiB", "MiB", "GiB", "TiB")


def bytes_text(count: int) -> str:
    """A number of bytes in the largest binary unit it fills: 2.0 GiB."""
    size, unit = float(count), "B"
    for larger in BYTE_UNITS:
        if size < 2**10:
            break
        size, unit = size / 2**10, larger
    return f"{count} B" if unit == "B" else f"{size:.1f} {unit}"


@dataclass(frozen=True, slots=True)
class Figure:
    """One figure of what a run took: its group, its name and its value."""

    group: str
    name: str
    measure: Measure
    value: float

    @property
    def text(self) -> str:
        """Its name and value: seconds to the millisecond, bytes in a unit."""
        if self.measure is Measure.SECONDS:
            return f"{self.name} {self.value:.3f} s"
        if self.measure is Measure.BYTES:
            return f"{self.name} {bytes_text(int(self.value))}"
        return f"{self.name} {int(self.value)}"


def resources_text(figures: Sequence[Figure]) -> str:
    """Every figure a run recorded, by group: "CPU time 2.000 s; memory peak 1 B"."""
    return "; ".join(
        f"{group} {', '.join(figure.text for figure in found)}"
        for group, found in groupby(figures, key=lambda figure: figure.group)
    )


@dataclass(frozen=True, slots=True)
class Invocation:
    """Who ran an entry's command, where, when, for how long and on what."""

    producer: Producer
    # The lab, by the name it records under, and the producer's name of the run.
    origin: str
    run: str
    started: str
    seconds: int
    host: Host
    # What the run took, as its resources give it, in the schema's order.
    resources: tuple[Figure, ...] = ()


@dataclass(frozen=True, slots=True)
class Record:
    """One entry of a record file: a run of a command and what it reported.

    Its seconds, producer and machine are those of its latest invocation.
    """

    status: Status
    exit: int
    output: str
    # The command as run at the top of the tree, less the variables in env.
    command: str
    env: tuple[tuple[str, str], ...] = ()
    tools: tuple[Tool, ...] = ()
    # "sha256:" and the hash of the .config the command ran under, or None.
    config: str | None = None
    seconds: int = 0
    # When its latest invocation started, in UTC: 2026-11-10T09:12:03Z.
    started: str = ""
    producer: Producer | None = None
    host: Host | None = None
    # The lab of its latest invocation, the producer's name of that run, and
    # what the run took.
    origin: str = ""
    run: str = ""
    resources: tuple[Figure, ...] = ()
    # The file or object its subject names, for a check of one, and the test.
    file: str | None = None
    test: str | None = None
    # Whether it checks the series itself, which no base has: its subject
    # names a range of commits, or it is the build of an earlier commit.
    series_check: bool = False

    @property
    def ran(self) -> tuple[tuple[Tool, ...], str, tuple[tuple[str, str], ...]]:
        """What it ran, which a base's entry must equal to be set against it."""
        return self.tools, self.command, tuple(sorted(self.env))

    @property
    def command_line(self) -> str:
        """The command as a developer types it: env's assignments, then it."""
        assignments = (f"{name}={shlex.quote(value)}" for name, value in self.env)
        return " ".join([*assignments, self.command])


@dataclass(frozen=True, slots=True)
class Unreadable:
    """A JSON file under a tree that render cannot read, and why."""

    path: str
    reason: str


type Entry = Record | Unreadable
# A tree by its project, commit and build.
type Key = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class Commit:
    commit: str
    subject: str
    author: str
    # The commit's last Link: trailer, or "" when it has none.
    link: str = ""


@dataclass(frozen=True, slots=True)
class BaseTree:
    """A public tree with a branch that holds a commit: its remote, its URL."""

    remote: str
    url: str


@dataclass(frozen=True, slots=True)
class Series:
    """A commit's series.json, with what its build compiles of the series."""

    base: str
    base_name: str
    commits: tuple[Commit, ...]
    # The series' C files the build does not compile, and the objects it
    # compiles of them, from the build's objects.json; None without one.
    unbuilt: tuple[str, ...]
    objects: tuple[str, ...] | None = None
    # The C files and headers it changes, the Documentation directories it
    # changes, and the files it adds.
    sources: tuple[str, ...] = ()
    docs: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    # The public trees whose branches hold base; empty when only a tag does.
    base_trees: tuple[BaseTree, ...] = ()
    # The tree's commit, whole; the series it came from by its Message-ID,
    # "" for none; the ref the run was given, "" for none; and the public
    # trees whose branches hold the commit.
    commit: str = ""
    message_id: str = ""
    ref: str = ""
    trees: tuple[BaseTree, ...] = ()
    # Its mailing lists, by short name.
    lists: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Cover:
    """The headers of a series' cover letter that its page shows, as given."""

    subject: str
    author: str
    date: str
    message_id: str
    # b4's change-id line of the body, or "" when it has none.
    change_id: str = ""
    # The text of a cover letter that series.json holds; None of a mailed one.
    text: str | None = None


@dataclass(frozen=True, slots=True)
class Recorded:
    """The last commit of the records that touched a tree."""

    when: datetime
    subject: str


@dataclass(frozen=True, slots=True)
class Head:
    commit: str
    when: datetime


@dataclass(frozen=True, slots=True)
class Tree:
    """The records of one project's commit built one way, or under no build."""

    project: str
    # The commit's directory: its name, which ends in DIRTY for a tree with
    # changes not committed.
    commit: str
    # Its configuration's directories, ARCH and compiler, as a path; NO_BUILD
    # for the commit's records of tools that read no build.
    build: str
    records: Mapping[str, Entry]
    series: Series | Unreadable | None
    # The .config of the build's last record, as the producer kept it.
    config: bytes | None
    recorded: Recorded | None
    cover: Cover | Unreadable | None = None
    b4_log: str | Unreadable | None = None
    # Every entry of each record that could be read, its representative
    # among them.
    runs: Mapping[str, tuple[Record, ...]] = dataclasses.field(
        default_factory=dict[str, tuple[Record, ...]]
    )

    @property
    def key(self) -> Key:
        return (self.project, self.commit, self.build)

    @property
    def parts(self) -> tuple[str, ...]:
        """Its directories under the site, and but for NO_BUILD under the records."""
        build = (NO_BUILD_DIR,) if self.build == NO_BUILD else self.build.split("/")
        return (self.project, self.commit, *build)

    @property
    def named(self) -> str:
        """Its build as a page names it: under no build, its configure's targets.

        Those of the configure that passed last, or failing one, that ran last.
        """
        found = [
            (entry.status is Status.PASSED, entry.started, match[1])
            for entry in self.runs.get("configure", ())
            if (match := TARGET_LIST.match(entry.command))
        ]
        return max(found)[2] if self.build == NO_BUILD and found else self.build


class Applier(Enum):
    """The tool that applies a series by its Message-ID."""

    B4 = "b4"


# The record of applying a series, under its Message-ID; without one, the
# record of fetching it, which did not get as far (commands.json).
APPLIERS = (
    (Applier.B4, "b4/shazam"),
    (Applier.B4, "b4/mbox"),
)
# The message b4 saved of a series, under its Message-ID.
MBOX = "b4/mbox.mbx"
# A commit's series.json, and a build's objects.json, which are no records.
SERIES_JSON = "series.json"
OBJECTS = "objects"


@dataclass(frozen=True, slots=True)
class Unapplied:
    """A series that did not apply, by its Message-ID, and its last record."""

    project: str
    applier: Applier
    name: str
    # Its record's path under the Message-ID, without .json.
    key: str
    record: Entry
    cover: Cover | Unreadable | None = None
    recorded: Recorded | None = None

    @property
    def parts(self) -> tuple[str, ...]:
        return (self.project, self.name)


def bare_id(message_id: str) -> str:
    """A Message-Id without its <> and the space around it."""
    return message_id.strip().removeprefix("<").removesuffix(">")


def is_object(data: object) -> TypeIs[dict[str, object]]:
    # JSON object keys are strings.
    return isinstance(data, dict)


def is_list(data: object) -> TypeIs[list[object]]:
    return isinstance(data, list)


def json_object(data: object, reason: str) -> Mapping[str, object]:
    if not is_object(data):
        raise RecordError(reason)
    return data


def member(data: Mapping[str, object], name: str) -> object:
    if name not in data:
        raise RecordError(f"no {name}")
    return data[name]


def want[T](data: Mapping[str, object], name: str, kind: type[T], what: str) -> T:
    # No field is a boolean, and JSON's true must not read as the integer 1.
    value = member(data, name)
    if isinstance(value, bool) or not isinstance(value, kind):
        raise RecordError(f"{name} is not {what}")
    return value


def optional[T](
    data: Mapping[str, object], name: str, kind: type[T], what: str
) -> T | None:
    return want(data, name, kind, what) if name in data else None


def items(data: Mapping[str, object], name: str, *, empty: bool = True) -> list[object]:
    value = member(data, name)
    if not is_list(value):
        raise RecordError(f"{name} is not a list")
    if not value and not empty:
        raise RecordError(f"{name} is empty")
    return value


def strings(data: Mapping[str, object], name: str) -> tuple[str, ...]:
    found = items(data, name)
    texts = tuple(item for item in found if isinstance(item, str))
    if len(texts) != len(found):
        raise RecordError(f"{name} is not a list of strings")
    return texts


def schema(data: object) -> Mapping[str, object]:
    fields = json_object(data, "not a JSON object")
    if want(fields, "schema", int, "an integer") != SCHEMA:
        raise RecordError(f"schema is not {SCHEMA}")
    return fields


def parse_tool(data: object) -> Tool:
    fields = json_object(data, "a tool is not a JSON object")
    return Tool(
        name=want(fields, "name", str, "a string"),
        version=want(fields, "version", str, "a string"),
        build=want(fields, "build", str, "a string"),
        url=optional(fields, "url", str, "a string") or "",
    )


def parse_env(data: object) -> tuple[tuple[str, str], ...]:
    fields = json_object(data, "env is not a JSON object")
    found = tuple((k, v) for k, v in fields.items() if isinstance(v, str))
    if len(found) != len(fields):
        raise RecordError("env is not a JSON object of strings")
    return found


def parse_figure(group: str, name: str, value: object) -> Figure:
    """One member of a group of resources, or RecordError for one unknown."""
    label, members = RESOURCES[group]
    if name not in members:
        raise RecordError(f"resources: {group} has no {name}")
    word, measure = members[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecordError(f"resources: {group} {name} is not a number")
    # Seconds may have a fraction; bytes and counts are whole.
    if isinstance(value, float) and measure is not Measure.SECONDS:
        raise RecordError(f"resources: {group} {name} is not an integer")
    if value < 0:
        raise RecordError(f"resources: {group} {name} is negative")
    return Figure(label, word, measure, value)


def parse_resources(data: object) -> tuple[Figure, ...]:
    """What a run took, each figure it gives, in the schema's order."""
    fields = json_object(data, "resources is not a JSON object")
    if unknown := sorted(fields.keys() - RESOURCES.keys()):
        raise RecordError(f"resources has no {unknown[0]}")
    found: list[Figure] = []
    for group, (_, members) in RESOURCES.items():
        if group not in fields:
            continue
        given = json_object(fields[group], f"resources: {group} is not a JSON object")
        if unknown := sorted(given.keys() - members.keys()):
            raise RecordError(f"resources: {group} has no {unknown[0]}")
        found += [
            parse_figure(group, name, given[name]) for name in members if name in given
        ]
    return tuple(found)


def parse_invocation(data: object) -> Invocation:
    fields = json_object(data, "an invocation is not a JSON object")
    producer = json_object(member(fields, "producer"), "producer is not a JSON object")
    machine = json_object(member(fields, "machine"), "machine is not a JSON object")
    return Invocation(
        producer=Producer(
            name=want(producer, "name", str, "a string"),
            version=want(producer, "version", str, "a string"),
        ),
        origin=want(fields, "origin", str, "a string"),
        run=want(fields, "run", str, "a string"),
        started=want(fields, "started", str, "a string"),
        seconds=want(fields, "seconds", int, "an integer"),
        host=Host(
            machine=want(machine, "arch", str, "a string"),
            cpu=want(machine, "cpu", str, "a string"),
            cpus=want(machine, "cpus", int, "an integer"),
            memory=want(machine, "memory", int, "an integer"),
        ),
        resources=parse_resources(fields["resources"]) if "resources" in fields else (),
    )


def latest[T](found: Sequence[T], when: Callable[[T], str]) -> T:
    """The item that started last; of those that started together, the last."""
    return max(reversed(found), key=when)


# A Message-ID as a directory names it: an "@", and nothing that reads as a
# path (README.md).
MESSAGE_ID = re.compile(r"[^<>\s/]+@[^<>\s/]+")


@dataclass(frozen=True, slots=True)
class Subject:
    """What a record is of: a commit, or what narrows it, or a series."""

    project: str
    # The commit, whole; "" for a series, which message_id names.
    commit: str = ""
    message_id: str = ""
    # The build its command ran in, as its directories; "" for none.
    build: str = ""
    file: str | None = None
    test: str | None = None
    # BASE..COMMIT, for a tool that read the commits as patches.
    range: str | None = None


def parse_build(data: object) -> str:
    """A build as a subject or an objects.json names it, as its directories."""
    fields = json_object(data, "build is not a JSON object")
    configuration = strings(fields, "configuration")
    if not configuration:
        raise RecordError("configuration is empty")
    arch = want(fields, "arch", str, "a string")
    compiler = want(fields, "compiler", str, "a string")
    if compiler not in COMPILERS:
        raise RecordError("compiler is not clang or gcc")
    return "/".join((*configuration, arch, compiler))


def parse_subject(data: object) -> Subject:
    """A record's subject: a series' when it names a Message-ID, else a commit's."""
    fields = json_object(data, "subject is not a JSON object")
    project = want(fields, "project", str, "a string")
    if "message_id" in fields:
        message_id = want(fields, "message_id", str, "a string")
        if not MESSAGE_ID.fullmatch(message_id):
            raise RecordError("message_id is not a Message-ID")
        return Subject(project, message_id=message_id)
    return Subject(
        project,
        commit=want(fields, "commit", str, "a string"),
        build=parse_build(fields["build"]) if "build" in fields else "",
        file=optional(fields, "file", str, "a string"),
        test=optional(fields, "test", str, "a string"),
        range=optional(fields, "range", str, "a string"),
    )


def parse_entry(data: object, subject: Subject | None = None) -> Record:
    """An entry of a record of subject, with its latest invocation."""
    fields = json_object(data, "an entry is not a JSON object")
    try:
        status = Status(want(fields, "status", str, "a string"))
    except ValueError as error:
        raise RecordError("status is not pass, fail, skip or error") from error
    seen = items(fields, "invocations", empty=False)
    last = latest(list(map(parse_invocation, seen)), lambda invoked: invoked.started)
    return Record(
        status=status,
        exit=want(fields, "exit", int, "an integer"),
        output=optional(fields, "output", str, "a string") or "",
        command=want(fields, "command", str, "a string"),
        env=parse_env(member(fields, "env")),
        tools=tuple(map(parse_tool, items(fields, "tools", empty=False))),
        config=optional(fields, "config", str, "a string"),
        seconds=last.seconds,
        started=last.started,
        producer=last.producer,
        host=last.host,
        origin=last.origin,
        run=last.run,
        resources=last.resources,
        file=subject.file if subject else None,
        test=subject.test if subject else None,
        series_check=subject is not None and subject.range is not None,
    )


def parse_entries(data: object) -> tuple[Record, ...]:
    """Every entry of a record file from its decoded JSON, or RecordError."""
    fields = schema(data)
    subject = parse_subject(member(fields, "subject"))
    found: list[Record] = []
    for number, entry in enumerate(items(fields, "entries", empty=False), 1):
        try:
            found.append(parse_entry(entry, subject))
        except RecordError as error:
            raise RecordError(f"entry {number}: {error}") from error
    return tuple(found)


def representative(entries: Sequence[Record]) -> Record:
    """The entry a page shows of its record: the one run last."""
    return latest(entries, lambda entry: entry.started)


def parse_record(data: object) -> Record:
    """A record file's representative entry, or RecordError naming what is wrong."""
    return representative(parse_entries(data))


def parse_commit(data: object) -> Commit:
    fields = json_object(data, "a commit is not a JSON object")
    return Commit(
        commit=want(fields, "commit", str, "a string"),
        subject=want(fields, "subject", str, "a string"),
        author=want(fields, "author", str, "a string"),
        link=want(fields, "link", str, "a string"),
    )


def parse_base_tree(data: object) -> BaseTree:
    fields = json_object(data, "a tree is not a JSON object")
    return BaseTree(
        remote=want(fields, "remote", str, "a string"),
        url=want(fields, "url", str, "a string"),
    )


def parse_series(data: object) -> Series:
    fields = schema(data)
    message_id = want(fields, "message_id", str, "a string")
    if message_id and not MESSAGE_ID.fullmatch(message_id):
        raise RecordError("message_id is not a Message-ID")
    return Series(
        base=want(fields, "base", str, "a string"),
        base_name=want(fields, "base_name", str, "a string"),
        commits=tuple(map(parse_commit, items(fields, "commits"))),
        unbuilt=(),
        sources=strings(fields, "sources"),
        docs=strings(fields, "docs"),
        added=strings(fields, "added"),
        base_trees=tuple(map(parse_base_tree, items(fields, "base_trees"))),
        commit=want(fields, "commit", str, "a string"),
        message_id=message_id,
        ref=want(fields, "ref", str, "a string"),
        trees=tuple(map(parse_base_tree, items(fields, "trees"))),
        lists=by_list_name(strings(fields, "lists")),
    )


def parse_objects(data: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """A build's objects.json: the objects it compiles, and the C files not.

    It names its commit and its build as well, which its path already gives.
    """
    fields = schema(data)
    want(fields, "commit", str, "a string")
    parse_build(member(fields, "build"))
    return strings(fields, "objects"), strings(fields, "unbuilt")


def header(message: EmailMessage, name: str) -> str:
    value = message.get(name)
    return "" if value is None else str(value)


def parse_cover(data: bytes) -> Cover:
    """The headers of a cover letter from its mbox, or RecordError."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(data, headersonly=True)
        return Cover(
            subject=header(message, "Subject"),
            author=header(message, "From"),
            date=header(message, "Date"),
            message_id=header(message, "Message-Id"),
            change_id=change_id_of(data),
        )
    except (MessageError, ValueError, IndexError) as error:
        raise RecordError("its headers cannot be read") from error


BODY = re.compile(rb"\r?\n\r?\n")
CHANGE_ID = re.compile(rb"^change-id: *(\S+)[ \t]*\r?$", re.MULTILINE)


def change_id_of(data: bytes) -> str:
    """The change-id b4 ends a cover letter's body with, or ""."""
    head = BODY.search(data)
    found = CHANGE_ID.search(data, head.end()) if head else None
    return found[1].decode("ascii", "replace") if found else ""


def local_cover(data: object) -> Cover | None:
    """The cover letter a series.json holds, its date as a mail gives one."""
    if "cover" not in (series := json_object(data, "not a JSON object")):
        return None
    fields = json_object(series["cover"], "cover is not a JSON object")
    try:
        date = want(fields, "date", str, "a string")
        when = datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise RecordError("the cover's date is no time in UTC") from error
    return Cover(
        subject=want(fields, "subject", str, "a string"),
        author=want(fields, "author", str, "a string"),
        date=format_datetime(when),
        message_id="",
        change_id=want(fields, "change_id", str, "a string"),
        text=want(fields, "text", str, "a string"),
    )


def load_cover(records: Path, file: Path | None) -> Cover | Unreadable | None:
    if file is None or not regular(file):
        return None
    try:
        return parse_cover(file.read_bytes())
    except OSError as error:
        reason = error.strerror or "cannot be read"
    except RecordError as error:
        reason = str(error)
    return Unreadable(file.relative_to(records).as_posix(), reason)


def read_json(file: Path) -> object:
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise RecordError(f"not UTF-8 at byte {error.start}") from error
    except json.JSONDecodeError as error:
        where = f"line {error.lineno} column {error.colno}"
        raise RecordError(f"not JSON: {error.msg} at {where}") from error
    except ValueError as error:
        raise RecordError("an integer too long to read") from error
    except RecursionError as error:
        raise RecordError("nested too deeply to read") from error
    except OSError as error:
        raise RecordError(error.strerror or "cannot be read") from error


def regular(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def inside(top: Path, relative: str) -> Path | None:
    """The path relative under top, when nothing on the way is a symbolic link."""
    path = top
    for name in relative.split("/"):
        path /= name
        if path.is_symlink():
            return None
    return path


def json_files(root: Path) -> list[Path]:
    """The regular .json files under root; symbolic links are skipped."""
    found = [
        file
        for directory, _, names in root.walk()
        for name in names
        if name.endswith(".json") and regular(file := directory / name)
    ]
    return sorted(found, key=lambda file: file.relative_to(root).parts)


def read_config(file: Path) -> bytes | None:
    if not regular(file):
        return None
    try:
        return file.read_bytes()
    except OSError:
        return None


def subdirs(path: Path) -> list[Path]:
    return sorted(p for p in path.iterdir() if p.is_dir() and not p.is_symlink())


def build_end(parts: Sequence[str]) -> int | None:
    """Where a build ends among the directories below its commit, or None.

    Its configuration runs to the first ARCH directory with a compiler's
    below it, which end the build.
    """
    for at in range(1, len(parts) - 1):
        if parts[at] in ARCHES and parts[at + 1] in COMPILERS:
            return at + 2
    return None


def builds_of(commit: Path, below: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """The builds under a commit's directory, each as its directories."""
    found: list[tuple[str, ...]] = []
    for path in subdirs(commit.joinpath(*below)):
        parts = (*below, path.name)
        if build_end(parts) == len(parts):
            found.append(parts)
        else:
            found += builds_of(commit, parts)
    return found


def load_entry(records: Path, file: Path) -> Entry:
    try:
        return parse_record(read_json(file))
    except RecordError as error:
        return Unreadable(file.relative_to(records).as_posix(), str(error))


def load_log(records: Path, file: Path | None) -> str | Unreadable | None:
    """What b4 printed applying a series: its shazam.json's output."""
    if file is None or not regular(file):
        return None
    entry = load_entry(records, file)
    return entry if isinstance(entry, Unreadable) else entry.output


@dataclass(frozen=True, slots=True)
class Came:
    """How a commit came, which each of its builds shares.

    That is its series.json, and the cover letter of the series it came from
    and what b4 printed applying it.
    """

    series: Series | Unreadable | None = None
    cover: Cover | Unreadable | None = None
    b4_log: str | Unreadable | None = None


def load_came(records: Path, commit: Path) -> Came:
    file = commit / SERIES_JSON
    if not regular(file):
        return Came()
    try:
        series = parse_series(data := read_json(file))
        cover = local_cover(data)
    except RecordError as error:
        return Came(Unreadable(file.relative_to(records).as_posix(), str(error)))
    if not series.message_id:
        return Came(series, cover)
    mbox = inside(commit.parent, f"{series.message_id}/{MBOX}")
    shazam = inside(commit.parent, f"{series.message_id}/b4/shazam.json")
    # The letter series.json holds has its text, which the mbox's headers lack.
    return Came(series, cover or load_cover(records, mbox), load_log(records, shazam))


def commit_files(commit: Path) -> list[Path]:
    """The records under a commit that lie under no build; series.json is none."""
    found: list[Path] = []
    for file in json_files(commit):
        parts = file.relative_to(commit).parts
        if build_end(parts) is None and parts != (SERIES_JSON,):
            found.append(file)
    return found


def load_tree(
    records: Path, key: Key, history: Mapping[tuple[str, ...], Recorded], came: Came
) -> Tree:
    """A build's records, its objects.json read into its series'.

    Of NO_BUILD, the commit's records that lie under no build.
    """
    project, commit, build = key
    under = () if build == NO_BUILD else tuple(build.split("/"))
    path = records.joinpath(project, commit, *under)
    entries: dict[str, Entry] = {}
    runs: dict[str, tuple[Record, ...]] = {}
    series = came.series
    for file in json_files(path) if under else commit_files(path):
        name = file.relative_to(path).with_suffix("").as_posix()
        try:
            data = read_json(file)
            if name != OBJECTS or not under:
                runs[name] = parse_entries(data)
                entries[name] = representative(runs[name])
            elif isinstance(series, Series):
                objects, unbuilt = parse_objects(data)
                series = dataclasses.replace(series, objects=objects, unbuilt=unbuilt)
        except RecordError as error:
            entries[name] = Unreadable(file.relative_to(records).as_posix(), str(error))
    return Tree(
        project,
        commit,
        build,
        records=entries,
        series=series,
        config=read_config(path / "config") if under else None,
        recorded=history.get((project, commit, *under)),
        cover=came.cover,
        b4_log=came.b4_log,
        runs=runs,
    )


def projects(records: Path) -> list[Path]:
    return [path for path in subdirs(records) if not path.name.startswith(".")]


def load(records: Path, history: Mapping[tuple[str, ...], Recorded]) -> list[Tree]:
    """Every commit's records, under no build first, then of each of its builds.

    A directory named by a Message-ID is no commit.
    """
    trees: list[Tree] = []
    for project in projects(records):
        for commit in subdirs(project):
            if "@" in commit.name:
                continue
            builds = ["/".join(build) for build in builds_of(commit)]
            if commit_files(commit):
                builds.insert(0, NO_BUILD)
            if not builds:
                continue
            came = load_came(records, commit)
            trees.extend(
                load_tree(records, (project.name, commit.name, build), history, came)
                for build in builds
            )
    return trees


def fold_earlier(trees: list[Tree]) -> list[Tree]:
    """The trees, the build of each series' earlier commits folded into its tree.

    An earlier commit's tree is one whose series.json lists no commits, names
    that commit and is no series' base; a tree with changes not committed is
    not its commit's.
    """
    earlier: dict[Key, Key] = {}
    bases: set[Key] = set()
    for tree in trees:
        if isinstance(series := tree.series, Series) and series.commits:
            bases.add((tree.project, series.base_name, tree.build))
            for commit in series.commits[:-1]:
                earlier[tree.project, tree.build, commit.commit] = tree.key
    folded: dict[Key, dict[str, Entry]] = {}
    kept: list[Tree] = []
    for tree in trees:
        came = tree.series
        own = isinstance(came, Series) and not came.commits
        clean = not tree.commit.endswith(DIRTY)
        commit = came.commit if isinstance(came, Series) else ""
        key = earlier.get((tree.project, tree.build, commit)) if own and clean else None
        build = tree.records.get(BUILD)
        if key is None or tree.key in bases or not build:
            kept.append(tree)
            continue
        if isinstance(build, Record):
            build = dataclasses.replace(build, series_check=True)
        folded.setdefault(key, {})[f"{BISECT}/{commit}"] = build
    return [
        (
            dataclasses.replace(tree, records={**tree.records, **folded[tree.key]})
            if tree.key in folded
            else tree
        )
        for tree in kept
    ]


def load_unapplied(
    records: Path,
    history: Mapping[tuple[str, ...], Recorded],
    trees: Iterable[Tree] = (),
) -> list[Unapplied]:
    """Each series by its Message-ID that did not apply.

    No commit of trees came from it, and its last record, of APPLIERS, did
    not pass.
    """
    named = {
        (tree.project, series.message_id)
        for tree in trees
        if isinstance(series := tree.series, Series)
    }
    found: list[Unapplied] = []
    for project in projects(records):
        for directory in subdirs(project):
            if "@" not in directory.name or (project.name, directory.name) in named:
                continue
            for applier, key in APPLIERS:
                file = inside(directory, f"{key}.json")
                if file is None or not regular(file):
                    continue
                entry = load_entry(records, file)
                if isinstance(entry, Unreadable) or entry.status is not Status.PASSED:
                    cover = load_cover(records, inside(directory, MBOX))
                    recorded = history.get((project.name, directory.name))
                    found.append(
                        Unapplied(
                            project.name,
                            applier,
                            directory.name,
                            key,
                            entry,
                            cover,
                            recorded,
                        )
                    )
                break
    return found


def run_git(git: str, records: Path, *args: str) -> bytes | None:
    # git is the absolute path shutil.which found, args are this file's own
    # literals, and no shell runs.
    done = subprocess.run(  # noqa: S603
        [git, "-C", str(records), *args],
        capture_output=True,
        check=False,
    )
    return done.stdout if done.returncode == 0 else None


def git_of(records: Path) -> str | None:
    """The path of git when records is the top of its own checkout, else None."""
    git = shutil.which("git")
    if git is None:
        return None
    top = run_git(git, records, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    if Path(os.fsdecode(top.rstrip(b"\n"))).resolve() != records.resolve():
        return None
    return git


def head_of(git: str, records: Path) -> Head | None:
    out = run_git(
        git, records, "log", "--max-count=1", "--abbrev=12", "--format=%h%x09%cI"
    )
    if not out:
        return None
    commit, _, when = out.decode().strip().partition("\t")
    return Head(commit, datetime.fromisoformat(when).astimezone(UTC))


def tree_of(parts: Sequence[str]) -> tuple[str, ...] | None:
    """The directories of the tree a file of the records lies in, or None.

    A series is its project and Message-ID; a build its project, commit and
    build; the records of a commit under no build its project and commit.
    """
    match parts:
        case [project, name, _, *_] if "@" in name:
            return (project, name)
        case [project, commit, *below] if below and below != [SERIES_JSON]:
            end = build_end(below)
            if end is None:
                return (project, commit) if below[-1].endswith(".json") else None
            return (project, commit, *below[:end]) if end < len(below) else None
        case _:
            return None


def history_of(git: str, records: Path) -> dict[tuple[str, ...], Recorded]:
    """When each tree's records were last committed, and by which run."""
    out = run_git(
        git,
        records,
        "log",
        "--no-renames",
        "--name-only",
        "--format=%x01%cI%x09%s",
        "-z",
    )
    history: dict[tuple[str, ...], Recorded] = {}
    current = None
    for token in (out or b"").split(b"\0"):
        if token.startswith(b"\x01"):
            when, _, subject = token[1:].decode("utf-8", "replace").partition("\t")
            current = Recorded(datetime.fromisoformat(when).astimezone(UTC), subject)
            continue
        found = tree_of(os.fsdecode(token.removeprefix(b"\n")).split("/"))
        if current is not None and found is not None:
            history.setdefault(found, current)
    return history


# Compare: how a series' records stand against its base's.


class Change(Enum):
    """How a series' record compares with its base's."""

    PASSES_ON_BOTH = "passes on both"
    FIXED = "fixed"
    INTRODUCED = "introduced"
    NEW_LINES = "fails on both, with new lines"
    NOTHING_NEW = "fails on both, nothing new"
    SKIPS_ON_BOTH = "skips on both"
    SKIPS = "skips, runs on the base"
    PASSES_BASE_SKIPS = "passes, skips on the base"
    FAILS_BASE_SKIPS = "fails, skips on the base"
    NO_BASE = "no base record"
    NO_SERIES = "no series record"
    UNREADABLE = "unreadable record"
    UNREADABLE_BASE = "unreadable base record"
    ERRORS = "error, no verdict"
    BASE_ERRORS = "error on the base, no verdict"

    @property
    def kind(self) -> str:
        return KIND[self]


KIND = {
    Change.PASSES_ON_BOTH: "pass",
    Change.FIXED: "pass",
    Change.INTRODUCED: "fail",
    Change.NEW_LINES: "fail",
    Change.NOTHING_NEW: "warn",
    Change.SKIPS_ON_BOTH: "none",
    Change.SKIPS: "warn",
    Change.PASSES_BASE_SKIPS: "pass",
    Change.FAILS_BASE_SKIPS: "fail",
    Change.NO_BASE: "none",
    Change.NO_SERIES: "none",
    Change.UNREADABLE: "warn",
    Change.UNREADABLE_BASE: "warn",
    Change.ERRORS: "error",
    Change.BASE_ERRORS: "error",
}
# What a series' own record shows, beside its own, when it changed.
OWN_CHANGES = frozenset({Change.INTRODUCED, Change.ERRORS})
# What a comparison says when the base's run it is set against differs.
CONFIGURED_DIFFERENTLY = "configured differently"
RAN_DIFFERENTLY = "ran differently"


class Own(Enum):
    """Why a series' record has no base record, though none is missing."""

    SERIES_ONLY = "series only"
    NEW_IN_SERIES = "new in the series"


# What a check of a file the series adds is set against: a base record that
# passed and printed nothing, so its failure is introduced and each line new.
NOTHING = Record(Status.PASSED, exit=0, output="", command="")


class Mark(Enum):
    """A line of a diff: in both outputs, new in the series', or gone from it."""

    SAME = "same"
    ADDED = "added"
    GONE = "gone"


@dataclass(frozen=True, slots=True)
class Row:
    """A line of a diff, as the output that holds it printed it."""

    mark: Mark
    text: str


class Unit(Enum):
    """What a comparison counts: output lines, or a compiler's findings."""

    LINE = "line"
    FINDING = "finding"


@dataclass(frozen=True, slots=True)
class Comparison:
    change: Change
    # When both are records: how many of the series' output lines, or
    # findings, the base's lacks and of the base's the series' lacks,
    # counted, and the diff.
    new: int = 0
    gone: int = 0
    rows: tuple[Row, ...] = ()
    # Whether both records name the .config they ran under, and differ.
    reconfigured: bool = False
    # Why the series' record was set against NOTHING, or None.
    own: Own | None = None
    unit: Unit = Unit.LINE
    # Whether no entry of the base's record ran the tools, command and
    # environment the series' did, so it is set against the base's
    # representative instead.
    ran_differently: bool = False

    @property
    def kind(self) -> str:
        """Its badge's colour: a series' own record's is plain unless it changed."""
        if self.own is not None and self.change not in OWN_CHANGES:
            return "none"
        return self.change.kind

    @property
    def notes(self) -> list[str]:
        """How the base's run differs from the series': its .config, or what ran."""
        found = [(CONFIGURED_DIFFERENTLY, self.reconfigured)]
        found.append((RAN_DIFFERENTLY, self.ran_differently))
        return [note for note, differs in found if differs]

    @property
    def context(self) -> int:
        """The rows a diff draws either side of a change: no other finding."""
        return 0 if self.unit is Unit.FINDING else CONTEXT

    def counts(self, *, both: bool = True) -> str:
        """The counts new and gone, a 0 only for lines if both, findings named."""
        lines = self.unit is Unit.LINE
        shown = [
            f"{sign}{count}"
            for sign, count in (("+", self.new), ("\N{MINUS SIGN}", self.gone))
            if count or (both and lines)
        ]
        if not lines:
            shown.append("finding" if self.new + self.gone == 1 else "findings")
        return " ".join(shown)

    @property
    def label(self) -> str:
        if self.own is not None:
            found = [self.own.value]
            if self.change in OWN_CHANGES:
                found.append(self.change.value)
            if self.new:
                found.append(self.counts(both=False))
            return ", ".join(found)
        if self.change is Change.NEW_LINES:
            return f"fails on both, {self.counts()}"
        if self.change is Change.NOTHING_NEW and self.gone:
            return f"{self.change.value}, {self.counts(both=False)}"
        changed = self.new or self.gone
        if changed and self.change in {
            Change.PASSES_ON_BOTH,
            Change.FIXED,
            Change.INTRODUCED,
        }:
            return f"{self.change.value}, {self.counts()}"
        return self.change.value


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def lines(text: str) -> list[str]:
    """The lines of text, split at newlines only; a final newline ends the last."""
    # Not record.lines(): a page draws the lines in order, a lone CR as a picture.
    return text.removesuffix("\n").split("\n") if text else []


def unplaced(line: str) -> str:
    """The line without what moves when a series moves code or a test runs again."""
    # Not record.unmoved() alone: a series moves code, so positions go as well.
    line = GUTTER.sub("|", POSITION.sub(r"\1", unmoved(line)))
    return STORE.sub("/nix/store/-", LINE.sub("line", line))


def added(now: str, then: str) -> frozenset[int]:
    """The indices of the lines of now that then lacks, counted, positions aside."""
    have = Counter(unplaced(line) for line in lines(then))
    new: set[int] = set()
    for index, line in enumerate(lines(now)):
        key = unplaced(line)
        if have[key]:
            have[key] -= 1
        else:
            new.add(index)
    return frozenset(new)


def matched[T](
    old: Sequence[T], new: Sequence[T], key: Callable[[T], Hashable]
) -> list[tuple[Mark, T]]:
    """The items of old set against those of new by their keys, each marked.

    An item left unmatched on both sides only moved, and shows as new holds it.
    """
    old_keys = [key(item) for item in old]
    new_keys = [key(item) for item in new]
    blocks = SequenceMatcher(None, old_keys, new_keys, autojunk=False).get_opcodes()
    unmatched = [block for block in blocks if block[0] != "equal"]
    moved = Counter(old_keys[i] for _, i1, i2, _, _ in unmatched for i in range(i1, i2))
    moved &= Counter(
        new_keys[j] for _, _, _, j1, j2 in unmatched for j in range(j1, j2)
    )
    old_moved, new_moved = moved.copy(), moved.copy()
    marks: list[tuple[Mark, T]] = []
    for op, i1, i2, j1, j2 in blocks:
        if op == "equal":
            marks.extend((Mark.SAME, item) for item in new[j1:j2])
            continue
        for match, item in zip(old_keys[i1:i2], old[i1:i2], strict=True):
            if old_moved[match]:
                old_moved[match] -= 1
            else:
                marks.append((Mark.GONE, item))
        for match, item in zip(new_keys[j1:j2], new[j1:j2], strict=True):
            if new_moved[match]:
                new_moved[match] -= 1
                marks.append((Mark.SAME, item))
            else:
                marks.append((Mark.ADDED, item))
    return marks


def diff(then: str, now: str, key: Callable[[str], str] = unplaced) -> tuple[Row, ...]:
    """The base's output (then) set against the series' (now) by line keys."""
    return tuple(
        Row(mark, line) for mark, line in matched(lines(then), lines(now), key)
    )


@dataclass(frozen=True, slots=True)
class Chunk:
    """A finding, its head and the lines under it, or a line before the first."""

    lines: tuple[str, ...]
    finding: bool

    @property
    def key(self) -> tuple[bool, str]:
        return self.finding, unplaced(self.lines[0])

    @property
    def drawn(self) -> tuple[str, ...]:
        """The lines a diff draws: a finding's head, quote and notes, else all."""
        if not self.finding:
            return self.lines
        head, *rest = self.lines
        quote = list(takewhile(QUOTED.match, rest))
        notes = [
            line
            for line in rest[len(quote) :]
            if NOTE.match(line) and not MACRO.match(line)
        ]
        return (head, *quote, *notes)


def chunks(text: str) -> list[Chunk]:
    """Each line before the first finding alone, then each finding whole."""
    rows = lines(text)
    heads = [index for index, line in enumerate(rows) if DIAGNOSTIC.match(line)]
    first = heads[0] if heads else len(rows)
    alone = [Chunk((line,), finding=False) for line in rows[:first]]
    ends = pairwise([*heads, len(rows)])
    return alone + [Chunk(tuple(rows[start:end]), finding=True) for start, end in ends]


def by_findings(then: str, now: str) -> tuple[int, int, tuple[Row, ...]] | None:
    """The findings new and gone and the diff, or None when neither has one.

    A finding is matched by its head, positions aside, and marked whole; one
    in both shows as the series printed it. The lines before the first diff
    as lines, but for clang's count, and count for nothing.
    """
    old, new = (
        [c for c in chunks(text) if c.finding or not GENERATED.fullmatch(c.lines[0])]
        for text in (then, now)
    )
    if not any(chunk.finding for chunk in (*old, *new)):
        return None
    marks = matched(old, new, lambda chunk: chunk.key)
    counted = Counter(mark for mark, chunk in marks if chunk.finding)
    rows = tuple(Row(mark, line) for mark, chunk in marks for line in chunk.drawn)
    return counted[Mark.ADDED], counted[Mark.GONE], rows


def skip_change(now: Status, then: Status) -> Change:
    """How a test that skipped on the series or on the base compares."""
    if now is Status.SKIPPED:
        return Change.SKIPS_ON_BOTH if then is Status.SKIPPED else Change.SKIPS
    if now is Status.FAILED:
        return Change.FAILS_BASE_SKIPS
    return Change.PASSES_BASE_SKIPS


def compare_records(
    now: Record, then: Record, own: Own | None = None, *, ran_differently: bool = False
) -> Comparison:
    """How now stands against then; ran_differently: then ran something else."""
    if Status.ERROR in (now.status, then.status):
        change = Change.ERRORS if now.status is Status.ERROR else Change.BASE_ERRORS
        return Comparison(change, own=own, ran_differently=ran_differently)
    if Status.SKIPPED in (now.status, then.status):
        change = skip_change(now.status, then.status)
        return Comparison(change, own=own, ran_differently=ran_differently)
    if (found := by_findings(then.output, now.output)) is not None:
        unit = Unit.FINDING
        new, gone, rows = found
    else:
        unit = Unit.LINE
        new = len(added(now.output, then.output))
        gone = len(added(then.output, now.output))
        rows = diff(then.output, now.output)
    if now.status is Status.PASSED:
        both = then.status is Status.PASSED
        change = Change.PASSES_ON_BOTH if both else Change.FIXED
    elif then.status is Status.PASSED:
        change = Change.INTRODUCED
    else:
        change = Change.NEW_LINES if new else Change.NOTHING_NEW
    both = now.config is not None and then.config is not None
    reconfigured = both and now.config != then.config
    return Comparison(change, new, gone, rows, reconfigured, own, unit, ran_differently)


def compare_alone(now: Record, *, new_file: bool) -> Comparison:
    """A series' record with no base record; new_file: it checks a file it adds.

    A check of the series itself counts by its verdict alone, since what it
    prints, such as get_maintainer's maintainers, is a report.
    """
    if now.series_check:
        change = Change.PASSES_ON_BOTH
        if now.status is Status.FAILED:
            change = Change.INTRODUCED
        elif now.status is Status.ERROR:
            change = Change.ERRORS
        return Comparison(change, own=Own.SERIES_ONLY)
    if new_file:
        return compare_records(now, NOTHING, Own.NEW_IN_SERIES)
    return Comparison(Change.NO_BASE)


def compare_entry(
    now: Entry | None,
    then: Entry | None,
    *,
    new_file: bool = False,
    runs: Sequence[Record] = (),
) -> Comparison:
    """The series' entry now set against then, its base's representative.

    Of runs, the base's entries (then alone when none are given), the one
    that ran what now ran stands in for then, the one with now's .config
    first, then the latest; without one, then does, and the comparison says
    so.
    """
    if isinstance(now, Unreadable):
        return Comparison(Change.UNREADABLE)
    if now is None:
        return Comparison(Change.NO_SERIES)
    if then is None:
        return compare_alone(now, new_file=new_file)
    if isinstance(then, Unreadable):
        return Comparison(Change.UNREADABLE_BASE)
    same = [run for run in runs or (then,) if run.ran == now.ran]
    if not same:
        return compare_records(now, then, ran_differently=True)
    return compare_records(
        now, max(same, key=lambda run: (run.config == now.config, run.started))
    )


# make's target that builds the Documentation directory after it, which the
# series' docs name.
HTMLDOCS = "htmldocs"


def checked_path(key: str, entry: Entry | None) -> str | None:
    """The path a per-file check looked at, else None.

    It is the file its subject names, or the Documentation directory of
    make/htmldocs/<directory>.
    """
    match key.split("/", 2):
        case [first, second, path] if (first, second) == (MAKE, HTMLDOCS):
            return path
        case _:
            return entry.file if isinstance(entry, Record) else None


def adds_file(series: Series | None, key: str, entry: Entry | None) -> bool:
    """Whether entry checks a file the series adds, an object by its C file."""
    path = checked_path(key, entry)
    if series is None or path is None:
        return False
    if path.endswith(".o"):
        path = path.removesuffix(".o") + ".c"
    return path in series.added


def covers(series: Series | None, key: str, entry: Entry | None) -> bool:
    """Whether the series was checked on what its base's entry of key checks.

    A per-file check's path must be among the series' objects, sources or
    docs; any other record is.
    """
    path = checked_path(key, entry)
    if series is None or path is None:
        return True
    lists = [p for p in (series.objects, series.sources, series.docs) if p is not None]
    return any(path in paths for paths in lists)


def compare(
    tree: Tree, base: Tree | None, series: Series | None = None
) -> dict[str, Comparison]:
    """Every record path of tree and of base, the series' set against the base's.

    A base's per-file check of a path the series was not checked on is left
    out: it belongs to another series on the same base.
    """
    mine = tree.records
    theirs: Mapping[str, Entry] = base.records if base else {}
    runs: Mapping[str, tuple[Record, ...]] = base.runs if base else {}
    keys = mine.keys() | {k for k, e in theirs.items() if covers(series, k, e)}
    return {
        key: compare_entry(
            mine.get(key),
            theirs.get(key),
            new_file=adds_file(series, key, mine.get(key)),
            runs=runs.get(key, ()),
        )
        for key in sorted(keys, key=lambda key: key.split("/"))
    }


class Configured(Enum):
    """How a series build's .config stands against its base build's."""

    SAME = "configured as its base"
    DIFFERENT = "configured differently from its base"
    UNKNOWN = "base configuration not recorded"


@dataclass(frozen=True, slots=True)
class Page:
    """A tree with, for a series, its base and how they compare."""

    tree: Tree
    series: Series | None
    base: Tree | None
    comparisons: Mapping[str, Comparison]
    # For a series build with a .config whose base build exists: how the two
    # .config files stand, and their diff when they differ.
    configured: Configured | None = None
    config_rows: tuple[Row, ...] = ()


def config_text(config: bytes) -> str:
    return config.decode("utf-8", "replace")


def configured(
    tree: Tree, base: Tree | None
) -> tuple[Configured | None, tuple[Row, ...]]:
    """How tree's .config stands against base's, compared line for line."""
    if base is None or tree.config is None:
        return None, ()
    if base.config is None:
        return Configured.UNKNOWN, ()
    if base.config == tree.config:
        return Configured.SAME, ()
    rows = diff(config_text(base.config), config_text(tree.config), key=str)
    return Configured.DIFFERENT, rows


def pages_of(trees: list[Tree]) -> list[Page]:
    by_key = {tree.key: tree for tree in trees}
    pages: list[Page] = []
    for tree in trees:
        series = tree.series
        if not isinstance(series, Series) or not series.commits:
            pages.append(Page(tree, None, None, {}))
            continue
        base = by_key.get((tree.project, series.base_name, tree.build))
        how, rows = configured(tree, base)
        pages.append(Page(tree, series, base, compare(tree, base, series), how, rows))
    return pages


@dataclass(frozen=True, slots=True)
class Overview:
    """A project's commit, with the page of each of its builds."""

    project: str
    commit: str
    builds: tuple[Page, ...]

    @property
    def parts(self) -> tuple[str, str]:
        return (self.project, self.commit)

    @property
    def series(self) -> Series | None:
        """The series of its first build that is one, or None for a base."""
        return next((page.series for page in self.builds if page.series), None)

    @property
    def cover(self) -> Cover | Unreadable | None:
        return self.builds[0].tree.cover

    @property
    def b4_log(self) -> str | Unreadable | None:
        return self.builds[0].tree.b4_log


def overviews_of(pages: list[Page]) -> list[Overview]:
    """The pages, in load order, grouped by commit; a commit without builds has none."""
    return [
        Overview(project, commit, tuple(group))
        for (project, commit), group in groupby(
            pages, key=lambda page: page.tree.parts[:2]
        )
    ]


# Render: HTML from the values above; nothing here reads or writes a file.

# What a findings line counts in a suite: the changes, the colour of the count
# and what its title says it is.
FINDINGS = (
    ((Change.INTRODUCED, Change.NEW_LINES), "fail", "introduced"),
    ((Change.FAILS_BASE_SKIPS,), "fail", "failed where the base skips"),
    ((Change.SKIPS,), "warn", "skipped where the base runs"),
    ((Change.UNREADABLE, Change.UNREADABLE_BASE), "warn", "unreadable"),
    ((Change.ERRORS, Change.BASE_ERRORS), "error", "with no verdict"),
    ((Change.NO_BASE,), "none", "without a base record"),
    ((Change.NO_SERIES,), "none", "without a series record"),
)
CLEAN = "nothing introduced or new, and every record a base can have set against one"
# The short name sashiko gives a list it knows by more than its local part.
LIST_NAMES = {"linux-kernel@vger.kernel.org": "LKML"}


def url(root: str, *parts: str) -> str:
    """A relative link to parts, percent-encoded so no name reads as a scheme."""
    return (root or "./") + "/".join(quote(part, safe="") for part in parts)


def output_file(key: str) -> tuple[str, ...]:
    """Where a record's whole output goes, relative to its tree's page."""
    *dirs, name = key.split("/")
    return ("output", *dirs, f"{name}.txt")


def suite_of(key: str) -> str:
    return key.partition("/")[0]


def suites(keys: Iterable[str]) -> list[str]:
    """The tools of keys, make first, then by name."""
    names = {suite_of(key) for key in keys}
    return sorted(names, key=lambda name: (name != MAKE, name))


def stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%d %H:%M")


def badge(kind: str, text: str) -> str:
    return f'<span class="badge {kind}">{escape(text)}</span>'


def small(text: str) -> str:
    return f'<div class="text-muted-sm">{escape(text)}</div>' if text else ""


def mono(text: str) -> str:
    return f'<span class="mono">{escape(text)}</span>'


def html_page(
    title: str, root: str, body: str, footer: str, *, scripts: Sequence[str] = ()
) -> str:
    js = f'\n<script src="{url(root, "theme.js")}"></script>'
    js += "".join(
        f'\n<script src="{url(root, script)}" defer></script>' for script in scripts
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<link rel="stylesheet" href="{url(root, "style.css")}">{js}
</head>
<body>
<div class="wrap">
<header><a href="{url(root, "index.html")}">{NAME}</a></header>
{body}
<footer>{escape(footer)}</footer>
</div>
</body>
</html>
"""


# The class of a row's main link, which the whole row of a table.rows follows.
ROW_LINK = "row-link"
CELL = re.compile(r"<td(?:\s[^>]*)?>")


def covered(row: str, href: str) -> str:
    """The row with a row-cover to href in each cell after its main link's first.

    The cells are render's own markup, and recorded text is escaped, so every
    <td is a cell.
    """
    cover = f'<a class="row-cover" href="{href}" tabindex="-1" aria-hidden="true"></a>'
    cells = list(CELL.finditer(row))
    for cell in reversed(cells[1:]):
        row = row[: cell.end()] + cover + row[cell.end() :]
    return row


def tree_link(root: str, tree: Tree, text: str, *, main: bool = False) -> str:
    href = url(root, *tree.parts, "index.html")
    kind = f"mono {ROW_LINK}" if main else "mono"
    return f'<a href="{escape(href)}" class="{kind}">{escape(text)}</a>'


def overview_link(
    root: str, overview: Overview, text: str, *, main: bool = False
) -> str:
    href = url(root, *overview.parts, "index.html")
    kind = f"mono {ROW_LINK}" if main else "mono"
    return f'<a href="{escape(href)}" class="{kind}">{escape(text)}</a>'


def muted(text: str) -> str:
    return f'<span class="muted">{escape(text)}</span>'


def title_of(cover: Cover | Unreadable | None, series: Series) -> str:
    """The cover letter's subject, else the first commit's."""
    if isinstance(cover, Cover) and cover.subject:
        return cover.subject
    return series.commits[0].subject


def index_title(cover: Cover | Unreadable | None, series: Series) -> str:
    """The cover letter's subject, else the first commit's and how many follow."""
    if isinstance(cover, Cover) and cover.subject:
        return cover.subject
    more = len(series.commits) - 1
    return series.commits[0].subject + (f" and {more} more" if more else "")


def author_of(cover: Cover | Unreadable | None, series: Series) -> str:
    if isinstance(cover, Cover) and cover.author:
        return cover.author
    return series.commits[0].author


def author_parts(cover: Cover | Unreadable | None, series: Series) -> tuple[str, str]:
    """The name and address of the cover's From, else of the first commit's author."""
    given = series.commits[0].author
    if isinstance(cover, Cover) and cover.author:
        given = cover.author
    return person(given)


def person(given: str) -> tuple[str, str]:
    """The name and address of an author, the whole as the name if neither."""
    name, address = parseaddr(given)
    if name:
        return name, address
    return (address, "") if address else (given, "")


def date_html(cover: Cover | Unreadable | None) -> str:
    """The cover's Date as YYYY-MM-DD over its time and offset, else as given."""
    if cover is None:
        return muted("not recorded")
    if isinstance(cover, Unreadable):
        return muted("unreadable")
    if not cover.date:
        return muted("not given")
    try:
        when = parsedate_to_datetime(cover.date)
    except ValueError:
        return escape(cover.date)
    return escape(when.strftime("%Y-%m-%d")) + small(when.strftime("%H:%M %z").strip())


def web_link(value: str) -> str | None:
    """The value if it is an http or https URL with a host, else None."""
    parts = urlsplit(value)
    if value.strip() != value or parts.scheme not in {"http", "https"}:
        return None
    return value if parts.netloc else None


def thread_url(message_id: str) -> str | None:
    """The thread on lore of a Message-Id, given with or without its <>."""
    bare = bare_id(message_id)
    return f"{LORE}{quote(bare, safe='@+=')}/" if bare else None


def producers(tree: Tree) -> list[Producer]:
    """Each producer that ran the representative of a record of tree."""
    found = {
        entry.producer
        for entry in tree.records.values()
        if isinstance(entry, Record) and entry.producer is not None
    }
    return sorted(found)


def all_producers(trees: Iterable[Tree]) -> list[Producer]:
    return sorted({value for tree in trees for value in producers(tree)})


def revision_html(value: str, commits: str) -> str:
    """A revision linked to its commit, "-dirty" shown but left out of the URL."""
    if match := REVISION.fullmatch(value):
        sha, dirty = match.group(1), match.group(2) or ""
        href = escape(commits + sha)
        return f'<a href="{href}" class="mono">{escape(sha[:12] + dirty)}</a>'
    return mono(value)


def producer_html(producer: Producer) -> str:
    """A producer's name and version, linked where PRODUCER_COMMITS has it."""
    commits = PRODUCER_COMMITS.get(producer.name)
    version = revision_html(producer.version, commits) if commits else None
    return f"{mono(producer.name)} {version or mono(producer.version)}"


def producers_html(values: Iterable[Producer]) -> str:
    return ", ".join(map(producer_html, values)) or muted("not recorded")


def list_name(address: str) -> str:
    """The short name sashiko shows for a list: LKML, else the local part."""
    return LIST_NAMES.get(address.lower()) or address.rpartition("@")[0] or address


def by_list_name(addresses: Iterable[str]) -> tuple[str, ...]:
    """The lists once each, by short name, as the site shows them."""
    found = set(addresses)
    return tuple(sorted(found, key=lambda address: (list_name(address), address)))


def list_tag(address: str) -> str:
    name = escape(list_name(address))
    return f'<span class="tag" title="{escape(address)}">{name}</span>'


def lists_html(cover: Cover | Unreadable | None, lists: Sequence[str]) -> str:
    """A series' lists; without any, whether its cover letter is missing."""
    if lists:
        return "".join(map(list_tag, lists))
    if isinstance(cover, Unreadable):
        return badge("warn", "unreadable cover letter")
    if cover is None:
        return '<span class="text-muted-sm">no cover letter</span>'
    return muted("none")


def base_html(page: Page, series: Series, root: str) -> str:
    """The base's build page, linked when it has records of this build."""
    if page.base:
        return tree_link(root, page.base, series.base_name)
    return f"{mono(series.base_name)} {muted('(no records of this build)')}"


def base_trees_of(project: str, series: Series) -> list[BaseTree]:
    """The trees to link the base in: base_trees, else one by its name."""
    if series.base_trees:
        return list(series.base_trees)
    for pattern, remote, tree in TAG_TREES.get(project, ()):
        if pattern.fullmatch(series.base_name):
            return [BaseTree(remote, tree)]
    host = CODE.get(project)
    # Named as who holds it there: linux-kdevops, qemu-project.
    return [BaseTree(urlsplit(host).path.split("/")[1], host)] if host else []


def commit_url(tree: str, sha: str) -> str | None:
    """A commit on a tree's web page: git.kernel.org's cgit, GitHub's, GitLab's."""
    if web_link(tree) is None:
        return None
    commit = quote(sha, safe="")
    match urlsplit(tree).hostname:
        case "git.kernel.org":
            return f"{tree}/commit/?id={commit}"
        case "github.com":
            return f"{tree.removesuffix('.git')}/commit/{commit}"
        case "gitlab.com":
            return f"{tree.removesuffix('.git')}/-/commit/{commit}"
        case _:
            return None


def base_commit_html(project: str, series: Series) -> str:
    """The base's SHA, linked to it in the first tree that can show it."""
    text = escape(series.base[:12])
    for tree in base_trees_of(project, series):
        if href := commit_url(tree.url, series.base):
            title = escape(tree.remote)
            return f'<a href="{escape(href)}" class="mono" title="{title}">{text}</a>'
    return f'<span class="mono muted">{text}</span>'


def commit_html(overview: Overview, series: Series) -> str:
    """The commit's name, linked to it in the first of its trees that can show it."""
    text = escape(overview.commit)
    for tree in series.trees:
        if href := commit_url(tree.url, series.commit):
            title = escape(tree.remote)
            return f'<a href="{escape(href)}" class="mono" title="{title}">{text}</a>'
    return text


def ref_items(series: Series) -> list[tuple[str, str]]:
    """The ref the run was given, when it was given one."""
    return [("Ref", mono(series.ref))] if series.ref else []


def base_link(root: str, series: Series, base: Overview | None) -> str:
    """The base's overview page, linked when it has records."""
    if base:
        return overview_link(root, base, series.base_name)
    return f"{mono(series.base_name)} {muted('(no records)')}"


def recorded_of(trees: Iterable[Tree]) -> Recorded | None:
    """The latest commit of the records among trees."""
    found = [tree.recorded for tree in trees if tree.recorded]
    return max(found, key=lambda recorded: recorded.when, default=None)


def when_html(recorded: Recorded | None) -> str:
    return escape(stamp(recorded.when)) if recorded else muted("not committed")


def recorded_td(recorded: Recorded | None, *, dated: bool) -> str:
    return f"<td>{when_html(recorded)}</td>" if dated else ""


def quiet(text: str, meaning: str) -> str:
    return f'<span class="muted" title="{escape(meaning)}">{escape(text)}</span>'


def count_html(kind: str, total: int, meaning: str) -> str:
    return f'<b class="{kind}" title="{escape(f"{total} {meaning}")}">{total}</b>'


def amount(counts: Counter[Unit]) -> str:
    """The lines and findings counted, in words: "2 lines and 1 finding"."""
    return " and ".join(
        plural(counts[unit], unit.value) for unit in Unit if counts[unit]
    )


def findings_of(comparisons: Iterable[Comparison]) -> list[tuple[str, int, str]]:
    """Each group of FINDINGS among comparisons: its colour, count and meaning."""
    counts = Counter(comparison.change for comparison in comparisons)
    return [
        (kind, total, meaning)
        for changes, kind, meaning in FINDINGS
        if (total := sum(counts[change] for change in changes))
    ]


def suite_findings(page: Page) -> str:
    """Per suite, a check mark when clean, else its counts with their meaning.

    Its new and gone sum the lines and findings, which the title tells apart.
    """
    if not page.series:
        return muted("not set against a base")
    reconfigured = page.configured is Configured.DIFFERENT
    found = [badge("warn", Configured.DIFFERENT.value)] if reconfigured else []
    for name in suites(page.comparisons):
        mine = [c for key, c in page.comparisons.items() if suite_of(key) == name]
        counts = Counter(comparison.change for comparison in mine)
        new, gone = Counter[Unit](), Counter[Unit]()
        for comparison in mine:
            new[comparison.unit] += comparison.new
            gone[comparison.unit] += comparison.gone
        shown = [
            count_html(kind, total, meaning)
            for kind, total, meaning in findings_of(mine)
        ]
        if not shown and not new.total() and not reconfigured:
            shown.append(f'<span class="pass" title="{CLEAN}">\N{CHECK MARK}</span>')
        elif fixed := counts[Change.FIXED]:
            shown.append(count_html("pass", fixed, "fixed"))
        if more := new.total():
            shown.append(quiet(f"+{more}", f"{amount(new)} new"))
        if less := gone.total():
            shown.append(quiet(f"\N{MINUS SIGN}{less}", f"{amount(gone)} gone"))
        found.append(f'<span class="suite">{" ".join([escape(name), *shown])}</span>')
    return " ".join(found) or muted("no records")


def failures(tree: Tree) -> tuple[int, int, int]:
    """How many of tree's records failed, erred, and could not be read."""
    entries = tree.records.values()
    statuses = Counter(e.status for e in entries if isinstance(e, Record))
    unreadable = sum(isinstance(e, Unreadable) for e in entries)
    unreadable += isinstance(tree.series, Unreadable)
    return statuses[Status.FAILED], statuses[Status.ERROR], unreadable


def failing_html(tree: Tree) -> str:
    fails, errors, unreadable = failures(tree)
    found = [badge("fail", f"{fails} failing")] if fails else []
    if errors:
        found.append(badge("error", f"{errors} with no verdict"))
    if unreadable:
        found.append(badge("warn", f"{unreadable} unreadable"))
    return " ".join(found)


def findings_line(page: Page, *, dated: bool) -> str:
    """A build linked, its findings per suite and, muted, when it was recorded."""
    tree = page.tree
    when = ""
    if dated and tree.recorded:
        when = (
            f' <span class="text-muted-sm">{escape(stamp(tree.recorded.when))}</span>'
        )
    return f"<div>{tree_link('', tree, tree.named)} {suite_findings(page)}{when}</div>"


# The version in a subject's bracketed prefix, as [PATCH v3] or [PATCH RFC v2].
PREFIX = re.compile(r"^(?:\s*\[[^\]]*\])+\s*")
VERSION = re.compile(r"\bv(\d+)\b", re.IGNORECASE)
RFC = re.compile(r"\bRFC\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Version:
    """A series among the versions of one, as its strip shows it."""

    parts: tuple[str, ...]
    label: str
    title: str


@dataclass(frozen=True, slots=True)
class Marks:
    """What a series' row and page show beside its records: its versions."""

    versions: tuple[Version, ...] = ()


def version_of(subject: str) -> tuple[int, bool]:
    """A subject's version, 1 when it names none, and whether it is an RFC."""
    prefix = PREFIX.match(subject)
    tags = prefix[0] if prefix else ""
    found = VERSION.search(tags)
    return (int(found[1]) if found else 1), RFC.search(tags) is not None


def version_label(number: int, *, rfc: bool) -> str:
    """Sashiko's label: v2, or RFC and RFC v2."""
    if rfc:
        return f"RFC v{number}" if number > 1 else "RFC"
    return f"v{number}"


def version_key(project: str, cover: Cover) -> tuple[str, ...] | None:
    """What links versions: b4's change-id, else the subject and the author."""
    if cover.change_id:
        return (project, "change-id", cover.change_id)
    subject = PREFIX.sub("", cover.subject).strip().lower()
    address = parseaddr(cover.author)[1].lower()
    return (project, "subject", subject, address) if subject and address else None


def version_groups(
    members: Iterable[tuple[tuple[str, ...], str, Cover | Unreadable | None]],
) -> dict[tuple[str, ...], tuple[Version, ...]]:
    """Each series of a group of two or more versions, with its group.

    A group is ordered by version, then by date; a resend of a version is
    marked as one.
    """
    groups: dict[tuple[str, ...], list[tuple[tuple[str, ...], Cover]]] = {}
    for parts, project, cover in members:
        if isinstance(cover, Cover) and (key := version_key(project, cover)):
            groups.setdefault(key, []).append((parts, cover))
    found: dict[tuple[str, ...], tuple[Version, ...]] = {}
    for group in groups.values():
        if len(group) < 2:  # noqa: PLR2004
            continue

        def order(
            member: tuple[tuple[str, ...], Cover],
        ) -> tuple[int, tuple[bool, datetime], tuple[str, ...]]:
            parts, cover = member
            when = posted(cover)
            return version_of(cover.subject)[0], (when is None, when or EPOCH), parts

        versions: list[Version] = []
        last = 0
        for parts, cover in sorted(group, key=order):
            number, rfc = version_of(cover.subject)
            label = version_label(number, rfc=rfc)
            if number == last:
                label += " resend"
            last = number
            versions.append(Version(parts, label, f"{cover.subject} ({cover.date})"))
        for version in versions:
            found[version.parts] = tuple(versions)
    return found


def version_pill(version: Version, current: tuple[str, ...], root: str) -> str:
    label, title = escape(version.label), escape(version.title)
    if version.parts == current:
        return f'<span class="lineage-badge current" title="{title}">{label}</span>'
    href = escape(url(root, *version.parts, "index.html"))
    return f'<a class="lineage-badge" href="{href}" title="{title}">{label}</a>'


def versions_html(
    versions: Sequence[Version], current: tuple[str, ...], root: str
) -> str:
    """The versions as sashiko's pills, the current one bold and unlinked."""
    if not versions:
        return ""
    pills = "".join(version_pill(version, current, root) for version in versions)
    return f'<div class="versions">{pills}</div>'


def version_items(
    marks: Marks, current: tuple[str, ...], root: str
) -> list[tuple[str, str]]:
    found = versions_html(marks.versions, current, root)
    return [("Versions", found)] if found else []


class Outcome(Enum):
    """Where a series row stands, as stats.html counts it and search.html filters."""

    INTRODUCED = "introduced"
    CLEAN = "clean"
    UNAPPLIED = "unapplied"

    @property
    def label(self) -> str:
        return OUTCOME_LABELS[self]


OUTCOME_LABELS = {
    Outcome.INTRODUCED: "Introduced something",
    Outcome.CLEAN: "Nothing introduced",
    Outcome.UNAPPLIED: "Could not apply",
}


@dataclass(frozen=True, slots=True)
class Facts:
    """What stats.html counts of a series row, and search.html filters it by."""

    parts: tuple[str, ...]
    title: str
    href: str
    week: str
    outcome: Outcome
    checks: tuple[str, ...] = ()
    lists: tuple[str, ...] = ()


def week_of(cover: Cover | Unreadable | None) -> str:
    """The ISO week, in UTC, of a cover letter's date, or ""."""
    when = posted(cover)
    if when is None:
        return ""
    year, week, _ = when.astimezone(UTC).date().isocalendar()
    return f"{year}-W{week:02}"


def applied_facts(overview: Overview, series: Series) -> Facts:
    checks: set[str] = set()
    introduced = False
    for page in overview.builds:
        for key, comparison in page.comparisons.items():
            if comparison.change in INTRODUCING:
                introduced = True
                checks.add(suite_of(key))
    return Facts(
        overview.parts,
        index_title(overview.cover, series),
        url("", *overview.parts, "index.html"),
        week_of(overview.cover),
        Outcome.INTRODUCED if introduced else Outcome.CLEAN,
        tuple(sorted(checks)),
        series.lists,
    )


def unapplied_facts(item: Unapplied) -> Facts:
    return Facts(
        item.parts,
        unapplied_title(item),
        url("", *item.parts, "index.html"),
        week_of(item.cover),
        Outcome.UNAPPLIED,
    )


def facts_attr(facts: Facts) -> str:
    """A row's data attributes for search.html's filters."""
    found = (
        ("lists", " ".join(facts.lists)),
        ("week", facts.week),
        ("outcome", facts.outcome.value),
        ("checks", " ".join(facts.checks)),
    )
    return "".join(f' data-{name}="{escape(value)}"' for name, value in found)


def series_row(
    overview: Overview,
    series: Series,
    base: Overview | None,
    marks: Marks,
    *,
    dated: bool,
) -> str:
    cover = overview.cover
    href = escape(url("", *overview.parts, "index.html"))
    findings = "".join(findings_line(page, dated=dated) for page in overview.builds)
    name, address = author_parts(cover, series)
    found = search_attr(
        overview.commit,
        index_title(cover, series),
        name,
        address,
        cover.message_id if isinstance(cover, Cover) else "",
        series.message_id,
        series.ref,
        *series.lists,
        *map(list_name, series.lists),
        series.base_name,
        series.base,
        *(f"{commit.subject} {commit.author}" for commit in series.commits),
        *(page.tree.named for page in overview.builds),
    )
    strip = versions_html(marks.versions, overview.parts, "")
    row = (
        f"<tr{found}{facts_attr(applied_facts(overview, series))}>"
        f'<td><a href="{href}" class="{ROW_LINK}">'
        f"{escape(index_title(cover, series))}</a>"
        f'<div class="lists">{lists_html(cover, series.lists)}</div>{strip}</td>'
        f"<td>{escape(name)}{small(address)}</td><td>{date_html(cover)}</td>"
        f'<td class="num">{len(series.commits)}</td>'
        f"<td>{base_link('', series, base)} "
        f"{base_commit_html(overview.project, series)}</td><td>{findings}</td></tr>"
    )
    return covered(row, href)


def search_attr(*fields: str) -> str:
    """A row's data-search: the text the index's search matches words against."""
    return f' data-search="{escape(" ".join(filter(None, fields)).lower())}"'


def base_row(overview: Overview, *, dated: bool) -> str:
    trees = [page.tree for page in overview.builds]
    builds = ", ".join(
        f"{tree_link('', tree, tree.named)} {failing_html(tree)}".rstrip()
        for tree in trees
    )
    found = all_producers(trees)
    words = search_attr(
        overview.commit,
        *(tree.named for tree in trees),
        *(producer.label for producer in found),
    )
    row = (
        f"<tr{words}>"
        f"<td>{overview_link('', overview, overview.commit, main=True)}"
        "</td>"
        f"<td>{builds}</td>{recorded_td(recorded_of(trees), dated=dated)}"
        f"<td>{producers_html(found)}</td></tr>"
    )
    return covered(row, escape(url("", *overview.parts, "index.html")))


def unapplied_title(item: Unapplied) -> str:
    """The cover letter's subject, else the Message-ID."""
    if isinstance(item.cover, Cover) and item.cover.subject:
        return item.cover.subject
    return item.name


def applied_html(entry: Entry) -> str:
    """Its finding: cannot apply when the record failed, else its own."""
    if isinstance(entry, Unreadable):
        return badge(Change.UNREADABLE.kind, Change.UNREADABLE.value)
    if entry.status is Status.FAILED:
        return badge("fail", "cannot apply")
    return verdict(entry)


def unapplied_row(item: Unapplied, marks: Marks) -> str:
    """A series row with no patches or base, linked to its own page."""
    cover = item.cover
    href = escape(url("", *item.parts, "index.html"))
    fields = [item.name]
    author = muted("not recorded")
    if isinstance(cover, Cover):
        name, address = person(cover.author)
        author = escape(name) + small(address) if cover.author else muted("not given")
        fields += [cover.subject, name, address, cover.message_id]
    strip = versions_html(marks.versions, item.parts, "")
    row = (
        f"<tr{search_attr(*fields)}{facts_attr(unapplied_facts(item))}>"
        f'<td><a href="{href}" class="{ROW_LINK}">{escape(unapplied_title(item))}</a>'
        f'<div class="lists">{lists_html(cover, ())}</div>{strip}</td>'
        f"<td>{author}</td><td>{date_html(cover)}</td>"
        f'<td class="num"></td><td></td>'
        f"<td>{applied_html(item.record)}</td></tr>"
    )
    return covered(row, href)


def message_id_of(cover: Cover | Unreadable | None) -> str:
    return bare_id(cover.message_id) if isinstance(cover, Cover) else ""


def item_id(item: Unapplied) -> str:
    """The Message-ID of a series that does not apply: its cover's, else its own."""
    return message_id_of(item.cover) or item.name


def posted(cover: Cover | Unreadable | None) -> datetime | None:
    """The cover letter's Date, UTC when it gives -0000, else None."""
    if not isinstance(cover, Cover) or not cover.date:
        return None
    try:
        when = parsedate_to_datetime(cover.date)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def newest_first(when: datetime | None) -> tuple[bool, timedelta]:
    """A key that sorts later times first, and None after any."""
    return (True, timedelta()) if when is None else (False, EPOCH - when)


type Order = tuple[tuple[bool, timedelta], tuple[bool, timedelta], tuple[str, ...]]


def row_order(
    cover: Cover | Unreadable | None, when: datetime | None, parts: tuple[str, ...]
) -> Order:
    """Where a series sorts, as sashiko's: newest posted first, then by when.

    When is the last commit of its records.
    """
    return newest_first(posted(cover)), newest_first(when), parts


def recorded_when(trees: Iterable[Tree]) -> datetime | None:
    recorded = recorded_of(trees)
    return recorded.when if recorded else None


def page_name(number: int) -> str:
    return "index.html" if number == 1 else f"index-{number}.html"


def pager_html(number: int, count: int, total: int, prefix: str = "") -> str:
    """The pager sashiko shows, as links: First, Prev, where, Next, Last."""
    if count == 1:
        return ""

    def button(text: str, target: int) -> str:
        if 1 <= target <= count and target != number:
            href = escape(url("", prefix + page_name(target)))
            return f'<a class="button" href="{href}">{text}</a>'
        return f'<span class="button disabled">{text}</span>'

    return (
        '<nav class="pagination" id="pager" aria-label="Pages">'
        f"{button('First', 1)}{button('Prev', number - 1)}"
        f"<span>Page {number} of {count} ({total} total)</span>"
        f"{button('Next', number + 1)}{button('Last', count)}</nav>"
    )


class View(Enum):
    """A view of the controls bar: its name and its first page."""

    SERIES = ("Series", "index.html")
    BASES = ("Bases", "bases.html")

    @property
    def label(self) -> str:
        return self.value[0]

    @property
    def page(self) -> str:
        return self.value[1]


@dataclass(frozen=True, slots=True)
class Tally:
    """A count of the status bar, as sashiko's."""

    value: int
    label: str
    sub: str = ""


def tally_html(tally: Tally) -> str:
    sub = f' <span class="stats-sub">({escape(tally.sub)})</span>' if tally.sub else ""
    return (
        '<div class="stats-group">'
        f'<span class="stats-val">{tally.value}</span> {escape(tally.label)}{sub}'
        "</div>"
    )


# The status bar's own pages, as sashiko links them after its name.
PAGES = (
    ("About", "about.html"),
    ("Settings", "settings.html"),
    ("Stats", "stats.html"),
)
# The pages that also open as a popover of every other root page, by its id.
POPOVERS = {"about.html": "about", "settings.html": "settings"}
SEPARATOR = '<span class="stats-sep"></span>'


@dataclass(frozen=True, slots=True)
class Site:
    """Everything the site's pages are rendered from."""

    overviews: list[Overview]
    unapplied: Sequence[Unapplied]
    footer: str
    dated: bool
    # The revision of render that wrote the site, or None when not given.
    revision: str | None = None
    # The site's public address, which a report links to, or None.
    address: str | None = None
    # Of a project's own site (project_sites): the project its rows are of,
    # and what each project's listings put before a root page's name.
    project: str = ""
    prefixes: Mapping[str, str] = dataclasses.field(default_factory=dict[str, str])

    def page(self, name: str) -> str:
        """The file of this project's page that the default project has as name."""
        return self.prefixes.get(self.project, "") + name

    @property
    def members(self) -> list[tuple[tuple[str, ...], str, Cover | Unreadable | None]]:
        """Each series row with a cover letter: where, its project, its cover."""
        found = [(o.parts, o.project, o.cover) for o in self.applied]
        return found + [(i.parts, i.project, i.cover) for i in self.unapplied]

    @property
    def lists(self) -> tuple[str, ...]:
        """Every mailing list of a series row, by short name, for the filter."""
        found = (
            address
            for overview in self.overviews
            if (series := overview.series)
            for address in series.lists
        )
        return by_list_name(found)

    @property
    def facts(self) -> list[Facts]:
        """The facts of every series row, as stats.html counts them."""
        found = [
            applied_facts(overview, series)
            for overview in self.overviews
            if (series := overview.series)
        ]
        return found + [unapplied_facts(item) for item in self.unapplied]

    @property
    def versions(self) -> dict[tuple[str, ...], tuple[Version, ...]]:
        return version_groups(self.members)

    @property
    def by_parts(self) -> dict[tuple[str, str], Overview]:
        return {overview.parts: overview for overview in self.overviews}

    @property
    def applied(self) -> list[Overview]:
        return [overview for overview in self.overviews if overview.series]

    @property
    def bases(self) -> list[Overview]:
        return [overview for overview in self.overviews if not overview.series]

    @property
    def series_count(self) -> int:
        """Every row of the index's series, applied or not."""
        return len(self.applied) + len(self.unapplied)

    @property
    def entries(self) -> list[Entry]:
        """Every record of the site, a series' that does not apply among them."""
        trees = (page.tree for overview in self.overviews for page in overview.builds)
        found = [entry for tree in trees for entry in tree.records.values()]
        return found + [item.record for item in self.unapplied]

    @property
    def tallies(self) -> tuple[tuple[Tally, ...], ...]:
        """The status bar's groups of counts, each group set apart."""
        records = plural(len(self.entries), "record")
        return (
            (Tally(self.series_count, "Series", records),),
            (Tally(len(self.bases), "Bases"),),
        )


def own_file(name: str, projects: Iterable[str]) -> bool:
    """Whether a project named name would overwrite a page of the site's own."""
    stems = {name, *(name.removeprefix(f"{project}-") for project in projects)}
    return any(stem in RESERVED or PAGED.fullmatch(stem) for stem in stems)


def prefixes_of(site: Site) -> dict[str, str]:
    """Each project with a row, by name, and the prefix of its listings' names.

    DEFAULT's, or without it the first's, are the root pages themselves.
    """
    found = {overview.project for overview in site.overviews}
    names = sorted(found | {item.project for item in site.unapplied})
    first = DEFAULT if DEFAULT in names else next(iter(names), "")
    return {name: "" if name == first else f"{name}-" for name in names}


def project_sites(site: Site) -> list[Site]:
    """The site of each project's rows alone, or of none: a listing is of one.

    A viewer follows one project, and a page of every project's rows has an
    empty Series where only another project, or only bases, have rows.
    """
    prefixes = prefixes_of(site)
    return [
        dataclasses.replace(
            site,
            overviews=[o for o in site.overviews if o.project == name],
            unapplied=[i for i in site.unapplied if i.project == name],
            project=name,
            prefixes=prefixes,
        )
        for name in prefixes
    ] or [site]


def page_link(name: str, href: str, page: str, here: str | None) -> str:
    """The status bar's link to page, and the button to its popover.

    The button is left out on the page itself, and when page has no popover.
    """
    link = f'<a class="stats-link" href="{escape(url("", href))}">{name}</a>'
    target = POPOVERS.get(page)
    if target is None or page == here:
        return link
    return (
        link.replace('"stats-link"', '"stats-link page"', 1)
        + f'<button type="button" class="stats-link" popovertarget="{target}"'
        f" hidden>{name}</button>"
    )


def popover(target: str, title: str, body: str) -> str:
    """A modal as sashiko's: a close button, the title, then body."""
    return (
        f'<div id="{target}" class="modal stack" popover><button type="button"'
        f' class="modal-close" popovertarget="{target}" popovertargetaction="hide"'
        f' aria-label="Close">\N{MULTIPLICATION SIGN}</button>'
        f'<h2 class="modal-title">{title}</h2>{body}</div>'
    )


def status_bar(site: Site, here: str | None = None) -> str:
    """The status bar sashiko shows: name, revision, pages, then the counts.

    The popovers of the pages but here follow it.
    """
    revision = f" {mono(site.revision)}" if site.revision else ""
    links = SEPARATOR.join(
        page_link(name, site.page(page), page, here) for name, page in PAGES
    )
    contents = {
        "about.html": about_html(site.page("")),
        "settings.html": settings_html(),
    }
    popovers = "".join(
        popover(target, name, contents[page])
        for name, page in PAGES
        if (target := POPOVERS.get(page)) and page != here
    )
    head = (
        f'<div class="stats-group"><span class="stats-label">{NAME}</span>'
        f"{revision}{SEPARATOR}{links}</div>"
    )
    groups = ["".join(map(tally_html, group)) for group in site.tallies]
    sep = '<div class="stats-sep"></div>'
    bar = f'<div class="stats-container" id="stats">{sep.join([head, *groups])}</div>'
    return bar + popovers


def root_page(
    site: Site,
    title: str,
    body: str,
    *,
    script: str | None = None,
    here: str | None = None,
) -> str:
    """A page at the site's root, here: its body, then the status bar."""
    body = f"{body}{status_bar(site, here)}\n"
    scripts = ("settings.js", *((script,) if script else ()))
    return html_page(title, "", body, site.footer, scripts=scripts)


def controls_html(view: View | None, site: Site) -> str:
    """The project, then the controls bar sashiko shows: lists, search, Clear, views.

    Each option of the project select is this page of that project, which
    theme.js opens; the select is no field of the search.
    """
    clear = url("", site.page((view or View.SERIES).page))
    kind = view.page if view else "search.html"
    named = "".join(
        f'<option value="{escape(url("", prefix + kind))}"'
        f"{' selected' if name == site.project else ''}>"
        f"{escape(PROJECTS.get(name, name))}</option>"
        for name, prefix in site.prefixes.items()
    )
    project = (
        '<select id="project" class="button" aria-label="Project"'
        f' autocomplete="off">{named}</select>'
    )
    options = "".join(
        f'<option value="{escape(address)}" title="{escape(address)}">'
        f"{escape(list_name(address))}</option>"
        for address in site.lists
    )
    views = "".join(
        f'<a class="button{" active" if other is view else ""}"'
        f'{' aria-current="page"' if other is view else ""}'
        f' href="{escape(url("", site.page(other.page)))}">{other.label}</a>'
        for other in View
    )
    return (
        f'<form class="controls" action="{escape(url("", site.page("search.html")))}"'
        f' method="get" role="search">{project if named else ""}'
        '<select id="list" name="list" class="button" aria-label="Mailing list">'
        f'<option value="">All lists</option>{options}</select>'
        '<input type="search" id="search" name="q" aria-label="Search"'
        ' placeholder="Search: subject, author, list, Message-ID, base, build">'
        '<button type="submit" class="button">Search</button>'
        f'<a class="button" href="{escape(clear)}">Clear</a>'
        f'<nav class="views" aria-label="Views">{views}</nav></form>'
    )


# Where search.js names the filters a chart's link set, which the form lacks.
FILTERS_NOTE = '\n<p class="muted" id="filters" hidden></p>'


def listing_page(site: Site, view: View | None, sections: str) -> str:
    """A page of the index, bases or search: the controls bar, then sections."""
    body = f"""
<section>
<h1>Results</h1>
<p class="muted">Each series is set against the records of its base, built the
same way.</p>
{controls_html(view, site)}{"" if view else FILTERS_NOTE}
</section>
{sections}"""
    script = None if view else "search.js"
    return root_page(site, NAME, body, script=script)


# The columns of the index's series, as col classes and their heads, each
# width in style.css, so that the table looks the same on every page it is on
# whatever its rows hold.
SERIES_COLUMNS = (
    ("subject", "<th>Subject</th>"),
    ("author", "<th>Author</th>"),
    ("date", "<th>Date</th>"),
    ("parts", '<th class="num">Parts</th>'),
    ("base", "<th>Base</th>"),
    ("findings", "<th>Findings</th>"),
)


def fixed_table(columns: Sequence[tuple[str, str]], body: str) -> str:
    """A table of fixed layout: its columns' widths and heads, then body."""
    cols = "".join(f'<col class="{name}">' for name, _ in columns)
    heads = "".join(head for _, head in columns)
    return (
        f'<div class="scroll"><table class="fixed rows"><colgroup>{cols}</colgroup>\n'
        f"<thead><tr>{heads}</tr></thead>\n<tbody>{body}</tbody>\n</table></div>"
    )


def series_section(rows: Sequence[str], pager: str = "", bases: str = "") -> str:
    """The series' table; with no row, a line that links bases, where given."""
    none_yet = '<tr><td colspan="6" class="muted">No series yet.</td></tr>'
    table = fixed_table(SERIES_COLUMNS, "".join(rows) or none_yet)
    if bases and not rows:
        table = (
            '<p class="muted">No series yet. Every record of this project is of'
            f' a base: see <a href="{escape(bases)}">Bases</a>.</p>'
        )
    return f'<section id="series"><h2>Series</h2>\n{table}{pager}</section>\n'


def bases_section(rows: Sequence[str], *, dated: bool) -> str:
    """The bases' table, one page of it, its columns sized by what they hold."""
    recorded = "<th>Recorded</th>" if dated else ""
    return (
        '<section id="bases"><h2>Bases</h2>\n<div class="scroll">'
        '<table class="rows bases">\n<thead><tr><th>Commit</th><th>Builds</th>'
        f"{recorded}<th>Producer</th></tr></thead>\n<tbody>{''.join(rows)}</tbody>\n"
        "</table></div></section>\n"
    )


def index_pages(site: Site) -> dict[str, str]:
    """A project's root pages by name: the series, the bases and the search.

    The series show PER_PAGE a page, newest first; the search holds them all.
    Each is written under site.page of its name.
    """
    by_parts, dated, versions = site.by_parts, site.dated, site.versions
    found: list[tuple[Order, str]] = [
        (
            row_order(
                overview.cover,
                recorded_when(page.tree for page in overview.builds),
                overview.parts,
            ),
            series_row(
                overview,
                series,
                by_parts.get((overview.project, series.base_name)),
                Marks(versions.get(overview.parts, ())),
                dated=dated,
            ),
        )
        for overview in site.overviews
        if (series := overview.series)
    ]
    found += [
        (
            row_order(
                item.cover, item.recorded.when if item.recorded else None, item.parts
            ),
            unapplied_row(item, Marks(versions.get(item.parts, ()))),
        )
        for item in site.unapplied
    ]
    rows = [row for _, row in sorted(found, key=lambda pair: pair[0])]
    bases = bases_section(
        [base_row(overview, dated=dated) for overview in site.bases], dated=dated
    )
    count = max(1, -(-len(rows) // PER_PAGE))
    listed = url("", site.page(View.BASES.page)) if site.bases else ""
    pages = {
        page_name(number): listing_page(
            site,
            View.SERIES,
            series_section(
                rows[(number - 1) * PER_PAGE : number * PER_PAGE],
                pager_html(number, count, len(rows), site.page("")),
                listed,
            ),
        )
        for number in range(1, count + 1)
    }
    pages[View.BASES.page] = listing_page(site, View.BASES, bases)
    series = series_section(rows, bases=listed)
    pages["search.html"] = listing_page(site, None, series + bases)
    pages["about.html"] = about_page(site)
    pages["settings.html"] = settings_page(site)
    pages["stats.html"] = stats_page(site)
    return pages


def back_link(root: str = "", *parts: str) -> str:
    """Sashiko's Back link, to parts under root, the series list by default."""
    href = escape(url(root, *(parts or ("index.html",))))
    return f'<p><a href="{href}">\N{LEFTWARDS ARROW} Back</a></p>'


# GitHub's mark, as sashiko's About shows it: mark-github of Octicons
# (https://github.com/primer/octicons), Copyright (c) GitHub Inc., under the
# MIT license in LICENSES/preferred/MIT.
GITHUB_MARK = (
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0c4.42 0 8 3.58 8 8a8'
    ".013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75"
    "-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36"
    "-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1"
    ".53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 "
    "3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24"
    "-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 "
    "0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 "
    '8-8Z"/></svg>'
)


def about_html(prefix: str = "") -> str:
    """What About says, on about.html and in its popover, of prefix's project."""
    return f"""
<p>This site shows what deterministic tools report on the
<a href="https://kernel.org">Linux kernel</a> and
<a href="https://www.qemu.org/">QEMU</a>, for a patch series from a mailing list
or any Git branch: builds of each configuration and architecture, test suites
such as <a href="https://git.kernel.org/pub/scm/fs/xfs/xfstests-dev.git">fstests</a> and
<a href="https://github.com/linux-blktests/blktests">blktests</a>, static analyzers
such as <a href="https://docs.kernel.org/dev-tools/sparse.html">sparse</a> and
<a href="https://github.com/error27/smatch">smatch</a>, and checks such as
<a href="https://docs.kernel.org/dev-tools/checkpatch.html">checkpatch.pl</a>. CI
systems write their results as <a href="{HOME}">{NAME}</a> records, and
<a href="https://github.com/linux-kdevops/kdevops-ng">kdevops-ng</a> is the first
that does.</p>
<h2>Reading it</h2>
<p><a href="{escape(url("", prefix + "index.html"))}">Series</a> lists each series,
newest first, with a line per build: a check mark where the series introduced nothing,
else counts, each named on hover. A series' page shows only what changed from its
base.</p>
<p><a href="{escape(url("", prefix + "bases.html"))}">Bases</a> lists the commits
series are set against. A base's page shows every output whole.</p>
<p>The site takes its look from
<a href="https://github.com/sashiko-dev/sashiko">sashiko</a>, and its first version
took its plain, file-based pages from
<a href="https://github.com/moto-timo/bodnuti">bodnuti</a>. kdevops' first dashboard,
with its results, is in <a
href="https://github.com/linux-kdevops/kdevops-results-archive">kdevops-results-archive</a>.</p>
<p class="github"><a href="{HOME}">\
{GITHUB_MARK}View project on GitHub</a></p>
"""


def about_page(site: Site) -> str:
    body = f"""
{back_link("", site.page("index.html"))}
<section class="stack"><h1>About</h1>{about_html(site.page(""))}</section>
"""
    return root_page(site, f"About {NAME}", body, here="about.html")


def settings_html() -> str:
    """What Settings holds, on settings.html and in its popover.

    settings.js shows the form and hides the note that it needs JavaScript.
    """
    options = "".join(
        f'<option value="{value}">{name}</option>'
        for value, name in (
            ("auto", "System default"),
            ("light", "Light"),
            ("dark", "Dark"),
        )
    )
    return f"""
<p class="muted" id="settings-nojs">Settings need JavaScript. Without it the site
follows the system's theme.</p>
<form class="settings" id="settings-form" hidden>
<label for="theme">Theme</label><select id="theme" class="button">{options}</select>
<label for="colorblind">Colorblind mode</label><input type="checkbox" id="colorblind">
</form>
<p class="text-muted-sm">Saved in this browser's storage for the site.</p>
"""


def settings_page(site: Site) -> str:
    body = f"""
{back_link("", site.page("index.html"))}
<section class="stack"><h1>Settings</h1>{settings_html()}</section>
"""
    return root_page(site, "Settings", body, here="settings.html")


# What stats.html counts as a series introducing something in a suite: the
# changes the index counts bold as introduced.
INTRODUCING = frozenset(FINDINGS[0][0])


def suite_outcomes(applied: Iterable[Overview]) -> dict[str, tuple[int, int]]:
    """Per suite, the series with a record in it, and how many introduced one."""
    checked, introduced = Counter[str](), Counter[str]()
    for overview in applied:
        found: dict[str, bool] = {}
        for page in overview.builds:
            for key, comparison in page.comparisons.items():
                name = suite_of(key)
                bad = comparison.change in INTRODUCING
                found[name] = found.get(name, False) or bad
        checked.update(found.keys())
        introduced.update(name for name, bad in found.items() if bad)
    return {name: (checked[name], introduced[name]) for name in suites(checked)}


def verdicts(entries: Iterable[Entry]) -> Counter[str]:
    """The records by verdict, pass, fail, skip or error, and those unreadable."""
    return Counter(
        "unreadable" if isinstance(entry, Unreadable) else entry.status.value
        for entry in entries
    )


def stat_card(title: str, value: int) -> str:
    return (
        f'<div class="stat-card"><h3>{escape(title)}</h3>'
        f'<div class="value">{value}</div></div>'
    )


def stats_cells(tag: str, cells: Sequence[str]) -> str:
    """A row of cells, all but the first numbers."""
    return "".join(
        f'<{tag}{' class="num"' if n else ""}>{cell}</{tag}>'
        for n, cell in enumerate(cells)
    )


def stats_table(head: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    body = "".join(f"<tr>{stats_cells('td', row)}</tr>" for row in rows)
    return (
        f'<div class="scroll"><table><thead><tr>{stats_cells("th", head)}</tr>'
        f"</thead><tbody>{body}</tbody></table></div>"
    )


# Charts: inline SVG that render draws whole, so a rerender is identical.
# Every mark is a link to the rows behind it, with a <title> for its
# tooltip; colours come from style.css classes, as the CSP allows no style.

CHART_WIDTH = 960
# The plot's margins in the viewBox: left for the y labels, bottom for x.
LEFT, RIGHT, TOP, BOTTOM = 48, 12, 12, 28
# The widest bar or column, and the gap between touching marks.
THICK, GAP = 24, 2
# What the build time chart colours: a profile's build, allmodconfig, else.
FAMILIES = (("profile", "Profile", "s1"), ("allmodconfig", "allmodconfig", "s2"))
OTHER_FAMILY = ("other", "Other", "s3")
# Lists the per-list chart shows by name; the rest fold into Other.
TOP_LISTS = 8


def num(value: float) -> str:
    """A coordinate, to a tenth, without a trailing .0."""
    return f"{value:.1f}".removesuffix(".0")


def nice_ticks(most: float) -> list[float]:
    """Round ticks from 0 to at least most, five or fewer steps."""
    steps = [m * 10**e for e in range(7) for m in (1, 2, 5)]
    step = next(step for step in steps if most / step <= 5)  # noqa: PLR2004
    return [step * n for n in range(int(-(-most // step)) + 1 or 2)]


def tick_label(value: float) -> str:
    return f"{value:,.0f}" if value >= 1 or value == 0 else f"{value:g}"


def column(x: float, y: float, w: float, h: float, *, rounded: bool) -> str:
    """A column from its baseline, its data end rounded when it is the top."""
    if not rounded or h <= 0:
        return (
            f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}"'
            f' height="{num(max(h, 0))}"/>'
        )
    r = min(4, w / 2, h)
    return (
        f'<path d="M{num(x)},{num(y + h)}V{num(y + r)}Q{num(x)},{num(y)} '
        f"{num(x + r)},{num(y)}H{num(x + w - r)}Q{num(x + w)},{num(y)} "
        f'{num(x + w)},{num(y + r)}V{num(y + h)}Z"/>'
    )


def bar_shape(x: float, y: float, w: float, h: float, *, rounded: bool) -> str:
    """A bar from its baseline at x, its data end rounded when it is the last."""
    if not rounded or w <= 0:
        return (
            f'<rect x="{num(x)}" y="{num(y)}" width="{num(max(w, 0))}"'
            f' height="{num(h)}"/>'
        )
    r = min(4, h / 2, w)
    return (
        f'<path d="M{num(x)},{num(y)}H{num(x + w - r)}Q{num(x + w)},{num(y)} '
        f"{num(x + w)},{num(y + r)}V{num(y + h - r)}Q{num(x + w)},{num(y + h)} "
        f'{num(x + w - r)},{num(y + h)}H{num(x)}Z"/>'
    )


def mark(href: str, title: str, shape: str, kind: str) -> str:
    """A mark: a link to its rows, its shape in its colour, its tooltip."""
    return (
        f'<a class="mark" href="{escape(href)}"><g class="{kind}">{shape}</g>'
        f"<title>{escape(title)}</title></a>"
    )


def svg(height: float, label: str, body: str) -> str:
    return (
        f'<svg class="chart" viewBox="0 0 {CHART_WIDTH} {num(height)}" role="img"'
        f' aria-label="{escape(label)}">{body}</svg>'
    )


def legend(items: Iterable[tuple[str, str]], *, dot: bool = False) -> str:
    """A legend of colour classes and names: a swatch, then the name."""
    shape = (
        '<circle cx="5" cy="5" r="4"/>'
        if dot
        else '<rect width="10" height="10" rx="2"/>'
    )
    return (
        '<ul class="legend">'
        + "".join(
            f'<li><svg viewBox="0 0 10 10" aria-hidden="true"><g class="{kind}">'
            f"{shape}</g></svg>{escape(name)}</li>"
            for kind, name in items
        )
        + "</ul>"
    )


def table_view(head: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    return (
        '<details class="table-view"><summary>Table</summary>'
        f"{stats_table(head, rows)}</details>"
    )


def search_href(page: str, **filters: str) -> str:
    """The search page with filters, as the chart marks link it."""
    query = "&".join(
        f"{name}={quote(value, safe='')}" for name, value in filters.items()
    )
    return url("", page) + (f"?{query}" if query else "")


def y_axis(
    ticks: Sequence[float], top: float, bottom: float, labels: Sequence[str]
) -> str:
    """Hairline gridlines at each tick with its label, and the baseline."""
    span = bottom - top
    found: list[str] = []
    for tick, text in zip(ticks, labels, strict=True):
        y = bottom - span * (tick - ticks[0]) / (ticks[-1] - ticks[0])
        kind = "axis" if tick == ticks[0] else "grid"
        found.append(
            f'<line class="{kind}" x1="{LEFT}" x2="{CHART_WIDTH - RIGHT}"'
            f' y1="{num(y)}" y2="{num(y)}"/>'
            f'<text x="{LEFT - 6}" y="{num(y + 4)}" text-anchor="end">'
            f"{escape(text)}</text>"
        )
    return "".join(found)


def weeks_between(first: date, last: date) -> list[date]:
    """Every Monday from first's week to last's, oldest first."""
    monday = first - timedelta(days=first.weekday())
    found: list[date] = []
    while monday <= last:
        found.append(monday)
        monday += timedelta(weeks=1)
    return found


def week_label(monday: date) -> str:
    year, week, _ = monday.isocalendar()
    return f"{year}-W{week:02}"


def weekly_chart(facts: Sequence[Facts], search: str) -> str:
    """Series per ISO week by cover date, stacked by outcome."""
    dated = [
        (date.fromisocalendar(int(f.week[:4]), int(f.week[6:]), 1), f)
        for f in facts
        if f.week
    ]
    if not dated:
        return '<p class="muted">No series has a dated cover letter.</p>'
    mondays = weeks_between(min(d for d, _ in dated), max(d for d, _ in dated))
    counts = Counter((d, f.outcome) for d, f in dated)
    order = [o for o in Outcome if any(o is f.outcome for _, f in dated)]
    totals = [sum(counts[monday, o] for o in Outcome) for monday in mondays]
    ticks = nice_ticks(max(totals))
    height = 240
    bottom = height - BOTTOM
    scale = (bottom - TOP) / ticks[-1]
    band = (CHART_WIDTH - LEFT - RIGHT) / len(mondays)
    width = min(THICK, band * 0.7)
    every = -(-len(mondays) // 12)
    marks: list[str] = []
    labels: list[str] = []
    for n, monday in enumerate(mondays):
        x = LEFT + band * n + (band - width) / 2
        y = bottom
        present = [o for o in order if counts[monday, o]]
        for k, outcome in enumerate(present):
            count = counts[monday, outcome]
            h = count * scale
            y -= h
            shape = column(
                x, y, width, h - (GAP if k else 0), rounded=k == len(present) - 1
            )
            week = week_label(monday)
            title = f"{week}: {count} {outcome.label.lower()}"
            href = search_href(search, week=week, outcome=outcome.value)
            marks.append(mark(href, title, shape, f"o-{outcome.value}"))
        if n % every == 0:
            labels.append(
                f'<text x="{num(x + width / 2)}" y="{height - 8}"'
                f' text-anchor="middle">{week_label(monday)}</text>'
            )
    grid = y_axis(ticks, TOP, bottom, [tick_label(t) for t in ticks])
    body = f'{grid}<g class="marks">{"".join(marks)}</g>{"".join(labels)}'
    rows = [
        [week_label(monday), *(str(counts[monday, o]) for o in order), str(total)]
        for monday, total in zip(mondays, totals, strict=True)
    ]
    undated = len(facts) - len(dated)
    note = (
        f'<p class="note text-muted-sm">{undated} series without a'
        " dated cover letter left out.</p>"
        if undated
        else ""
    )
    return (
        legend((f"o-{o.value}", o.label) for o in order)
        + svg(height, "Series per week by outcome", body)
        + note
        + table_view(["Week", *(o.label for o in order), "Total"], rows)
    )


def hbars(
    rows: Sequence[tuple[str, Sequence[tuple[int, str, str, str]]]], label: str
) -> str:
    """Horizontal bars, one row each: its name, then its stacked segments.

    Each segment is its count, colour class, link and tooltip; the row's
    total is printed at its tip.
    """
    gutter = 200
    pitch = 28
    height = TOP + pitch * len(rows) + BOTTOM
    most = max((sum(c for c, *_ in segments) for _, segments in rows), default=0)
    ticks = nice_ticks(max(most, 1))
    span = CHART_WIDTH - gutter - RIGHT - 40
    scale = span / ticks[-1]
    found: list[str] = []
    grid: list[str] = []
    for tick in ticks:
        x = gutter + tick * scale
        kind = "axis" if tick == 0 else "grid"
        grid.append(
            f'<line class="{kind}" x1="{num(x)}" x2="{num(x)}" y1="{TOP}"'
            f' y2="{num(height - BOTTOM)}"/><text x="{num(x)}"'
            f' y="{num(height - 10)}" text-anchor="middle">{tick_label(tick)}</text>'
        )
    for n, (name, segments) in enumerate(rows):
        y = TOP + pitch * n + (pitch - min(THICK, 18)) / 2
        h = min(THICK, 18)
        x = gutter
        present = [segment for segment in segments if segment[0]]
        for k, (count, kind, href, title) in enumerate(present):
            w = count * scale
            shape = bar_shape(
                x + (GAP if k else 0),
                y,
                w - (GAP if k else 0),
                h,
                rounded=k == len(present) - 1,
            )
            found.append(mark(href, title, shape, kind))
            x += w
        total = sum(c for c, *_ in segments)
        found.append(
            f'<text class="label" x="{gutter - 8}" y="{num(y + h / 2 + 4)}"'
            f' text-anchor="end">{escape(name)}</text>'
            f'<text x="{num(x + 6)}" y="{num(y + h / 2 + 4)}">{total}</text>'
        )
    body = "".join(grid) + f'<g class="marks">{"".join(found)}</g>'
    return svg(height, label, body)


def checks_chart(
    facts: Sequence[Facts], applied: Sequence[Overview], search: str
) -> str:
    """Per tool, the series that introduced something in it."""
    checked = Counter[str]()
    for overview in applied:
        tools = {suite_of(key) for page in overview.builds for key in page.comparisons}
        checked.update(tools)
    introduced = Counter(tool for f in facts for tool in f.checks)
    names = sorted(checked, key=lambda name: (-introduced[name], name))
    if not names:
        return '<p class="muted">No series has a check recorded.</p>'
    rows = [
        (
            name,
            [
                (
                    introduced[name],
                    "s1",
                    search_href(search, check=name),
                    (
                        f"{name}: {introduced[name]} of {checked[name]} series"
                        " introduced something"
                    ),
                )
            ],
        )
        for name in names
    ]
    table = [
        [escape(name), str(introduced[name]), str(checked[name])] for name in names
    ]
    return hbars(rows, "Series that introduced something, by check") + table_view(
        ["Check", "Introduced", "Checked"], table
    )


def lists_chart(facts: Sequence[Facts], search: str) -> str:
    """Per mailing list, the series checked, split by what they introduced."""
    shown = (Outcome.INTRODUCED, Outcome.CLEAN)
    counts = Counter(
        (address, f.outcome) for f in facts if f.outcome in shown for address in f.lists
    )
    totals = Counter[str]()
    for (address, _), count in counts.items():
        totals[address] += count
    ranked = sorted(totals, key=lambda a: (-totals[a], list_name(a), a))
    top, rest = ranked[:TOP_LISTS], ranked[TOP_LISTS:]
    if not top:
        return '<p class="muted">No checked series has a mailing list.</p>'
    rows: list[tuple[str, Sequence[tuple[int, str, str, str]]]] = []
    table: list[list[str]] = []
    for address in top:
        name = list_name(address)
        rows.append(
            (
                name,
                [
                    (
                        counts[address, o],
                        f"o-{o.value}",
                        search_href(search, list=address, outcome=o.value),
                        f"{name}: {counts[address, o]} {o.label.lower()}",
                    )
                    for o in shown
                ],
            )
        )
        table.append([escape(name), *(str(counts[address, o]) for o in shown)])
    if rest:
        other = [sum(counts[a, o] for a in rest) for o in shown]
        rows.append(
            (
                f"Other ({len(rest)} lists)",
                [
                    (
                        count,
                        f"o-{o.value}",
                        search_href(search, outcome=o.value),
                        f"{len(rest)} other lists: {count} {o.label.lower()}",
                    )
                    for o, count in zip(shown, other, strict=True)
                ],
            )
        )
        table.append([f"Other ({len(rest)} lists)", *map(str, other)])
    return (
        legend((f"o-{o.value}", o.label) for o in shown)
        + hbars(rows, "Series checked per mailing list, by outcome")
        + table_view(["List", *(o.label for o in shown)], table)
    )


# The make targets that write a whole .config, which a build's configuration
# names instead of its profiles.
TARGETS = re.compile(r"(?:tiny|def|all(?:no|yes|mod|def)|rand)config|\w+_defconfig")


def family_of(build: str) -> tuple[str, str, str]:
    """The configuration family of a build, by its configuration's directories."""
    configuration = build.split("/")[:-2]
    if configuration == ["allmodconfig"]:
        return FAMILIES[1]
    if len(configuration) == 1 and TARGETS.fullmatch(configuration[0]):
        return OTHER_FAMILY
    return FAMILIES[0]


@dataclass(frozen=True, slots=True)
class BuildTime:
    tree: Tree
    when: datetime
    minutes: float
    host: Host
    # What the build took of the machine, as resources_text() words it, or "".
    took: str = ""


def build_times(site: Site) -> tuple[list[BuildTime], int]:
    """Each build's time, by make/all, at its cover letter's date or its commit's.

    A base dates by when its records were committed; a build with neither
    date is counted apart.
    """
    found: list[BuildTime] = []
    undated = 0
    for overview in site.overviews:
        for page in overview.builds:
            tree = page.tree
            entry = tree.records.get(BUILD)
            if not isinstance(entry, Record) or entry.host is None:
                continue
            when = posted(overview.cover) if overview.series else None
            if when is None and tree.recorded:
                when = tree.recorded.when
            if when is None:
                undated += 1
                continue
            took = resources_text(entry.resources)
            minutes = entry.seconds / 60
            found.append(
                BuildTime(tree, when.astimezone(UTC), minutes, entry.host, took)
            )
    found.sort(key=lambda b: (b.when, b.tree.parts))
    return found, undated


def log_ticks(least: float, most: float) -> list[float]:
    """Decades from below least to above most: 0.1, 1, 10, 100 minutes."""
    low = math.floor(math.log10(max(least, 0.01)))
    high = math.ceil(math.log10(max(most, 0.1)))
    return [10.0**e for e in range(low, max(high, low + 1) + 1)]


# A dot's colour class, its legend's name, and what its tooltip calls it.
type Kind = tuple[str, str, str]


def dots_chart(
    timed: tuple[Sequence[BuildTime], int],
    kind_of: Callable[[BuildTime], Kind],
    kinds: Sequence[tuple[str, str]],
    *,
    label: str,
    column: str,
) -> str:
    """Kernel build minutes over time on a log scale, a dot per build.

    timed is build_times': the dated builds and how many are not. kind_of
    colours each dot, and kinds is the legend, in its order.
    """
    times, undated = timed
    height = 280
    bottom = height - BOTTOM
    ticks = log_ticks(min(t.minutes for t in times), max(t.minutes for t in times))
    low, high = math.log10(ticks[0]), math.log10(ticks[-1])
    first, last = times[0].when, times[-1].when
    span = max((last - first).total_seconds(), 1)
    plot = CHART_WIDTH - LEFT - RIGHT - 16

    def x_of(when: datetime) -> float:
        return LEFT + 8 + plot * (when - first).total_seconds() / span

    def y_of(minutes: float) -> float:
        value = math.log10(max(minutes, ticks[0]))
        return bottom - (bottom - TOP) * (value - low) / (high - low)

    labels = [tick_label(t) for t in ticks]
    grid = "".join(
        f'<line class="{"axis" if n == 0 else "grid"}" x1="{LEFT}"'
        f' x2="{CHART_WIDTH - RIGHT}" y1="{num(y_of(t))}" y2="{num(y_of(t))}"/>'
        f'<text x="{LEFT - 6}" y="{num(y_of(t) + 4)}" text-anchor="end">'
        f"{escape(text)}</text>"
        for n, (t, text) in enumerate(zip(ticks, labels, strict=True))
    )
    months: list[str] = []
    month = date(first.year, first.month, 1)
    while month <= last.date():
        moment = datetime(month.year, month.month, 1, tzinfo=UTC)
        if moment >= first:
            months.append(
                f'<text x="{num(x_of(moment))}" y="{height - 8}"'
                f' text-anchor="middle">{month:%Y-%m}</text>'
            )
        month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    dots: list[str] = []
    for t in times:
        kind, _, name = kind_of(t)
        x, y = num(x_of(t.when)), num(y_of(t.minutes))
        shape = (
            f'<circle class="hit" cx="{x}" cy="{y}" r="12"/>'
            f'<circle class="ring" cx="{x}" cy="{y}" r="4"/>'
        )
        title = (
            f"{t.tree.commit} {t.tree.named} ({name}): {t.minutes:.1f} minutes,"
            f" {stamp(t.when)} UTC{'; ' if t.took else ''}{t.took}"
        )
        dots.append(mark(url("", *t.tree.parts, "index.html"), title, shape, kind))
    body = f'{grid}<g class="marks">{"".join(dots)}</g>{"".join(months)}'
    table = [
        [
            escape(f"{t.tree.commit} {t.tree.named}"),
            escape(kind_of(t)[2]),
            f"{t.minutes:.1f}",
            escape(t.took),
            stamp(t.when),
        ]
        for t in reversed(times)
    ]
    note = (
        f'<p class="note text-muted-sm">{plural(undated, "build")} without a date'
        " left out.</p>"
        if undated
        else ""
    )
    return (
        legend(kinds, dot=True)
        + svg(height, label, body)
        + note
        + table_view(["Build", column, "Minutes", "Took", "Date (UTC)"], table)
    )


def times_chart(site: Site) -> str:
    """Kernel build minutes over time, a dot per build, by family."""
    times, undated = build_times(site)
    if not times:
        return '<p class="muted">No build has a dated build record.</p>'

    def kind_of(t: BuildTime) -> Kind:
        _, name, kind = family_of(t.tree.build)
        return kind, name, name

    used = {family_of(t.tree.build) for t in times}
    families = [
        (kind, name)
        for f in (*FAMILIES, OTHER_FAMILY)
        if f in used
        for _, name, kind in (f,)
    ]
    return dots_chart(
        (times, undated),
        kind_of,
        families,
        label="Kernel build minutes over time, by configuration",
        column="Family",
    )


# The machine types the machine chart names, each its own hue; with more,
# the busiest MACHINES - 1 keep theirs and the rest share the last.
MACHINES = 3


def machines_chart(site: Site) -> str:
    """Kernel build minutes over time, a dot per build, by machine type."""
    times, undated = build_times(site)
    types = Counter(t.host.label for t in times)
    if not types:
        return '<p class="muted">No build has a dated build record.</p>'
    ranked = sorted(types, key=lambda name: (-types[name], name))
    named = ranked if len(ranked) <= MACHINES else ranked[: MACHINES - 1]
    hues = {name: f"s{n}" for n, name in enumerate(named, 1)}
    other = f"Other ({len(ranked) - len(named)} machine types)"

    def kind_of(t: BuildTime) -> Kind:
        name = t.host.label
        if name in hues:
            return hues[name], name, name
        return f"s{MACHINES}", other, name

    kinds = {kind_of(t)[:2] for t in times}
    order = [*((hues[n], n) for n in named), (f"s{MACHINES}", other)]
    return dots_chart(
        (times, undated),
        kind_of,
        [kind for kind in order if kind in kinds],
        label="Kernel build minutes over time, by machine type",
        column="Machine",
    )


def stats_page(site: Site) -> str:
    """What the records hold, counted: series, bases, builds, records, weeks."""
    applied, bases = site.applied, site.bases
    builds = sum(len(overview.builds) for overview in site.overviews)
    cards = [
        stat_card("Series", site.series_count),
        stat_card("Applied", len(applied)),
        stat_card("Could not apply", len(site.unapplied)),
        stat_card("Bases", len(bases)),
        stat_card("Builds", builds),
        stat_card("Records", len(site.entries)),
    ]
    groups = (
        (
            "Series",
            (e for o in applied for p in o.builds for e in p.tree.records.values()),
        ),
        (
            "Bases",
            (e for o in bases for p in o.builds for e in p.tree.records.values()),
        ),
        ("Could not apply", (item.record for item in site.unapplied)),
    )
    kinds = ("pass", "fail", "skip", "error", "unreadable")
    records: list[list[str]] = []
    for name, entries in groups:
        counts = verdicts(entries)
        records.append(
            [name, *(str(counts[kind]) for kind in kinds), str(counts.total())]
        )
    outcomes = [
        [escape(name), str(checked), str(bad), str(checked - bad)]
        for name, (checked, bad) in suite_outcomes(applied).items()
    ]
    facts, search = site.facts, site.page("search.html")
    body = f"""
{back_link("", site.page("index.html"))}
<section><h1>Stats</h1>
<p class="muted">Counts of the records this site was rendered from. Each mark on a
chart links to its series.</p></section>
<section><div class="stat-card-grid">{"".join(cards)}</div></section>
<section><h2>Series per week</h2><p class="note text-muted-sm">By the ISO week of
the cover letter's date, in UTC, and by outcome.</p>
<div class="chart-card">{weekly_chart(facts, search)}</div></section>
<section><h2>Findings introduced, by tool</h2><p class="note text-muted-sm">The
series that introduced a failure, new lines or new findings in each tool's
records.</p>
<div class="chart-card">{checks_chart(facts, applied, search)}</div></section>
<section><h2>Series per mailing list</h2><p class="note text-muted-sm">The checked
series sent to each list: the {TOP_LISTS} busiest by name, the rest as Other.</p>
<div class="chart-card">{lists_chart(facts, search)}</div></section>
<section><h2>Kernel build time</h2><p class="note text-muted-sm">Minutes each
kernel build took, on a log scale, by its cover letter's date or, for a base,
when its records were committed.</p>
<div class="chart-card">{times_chart(site)}</div></section>
<section><h2>Kernel build time by machine</h2><p class="note text-muted-sm">The
same builds by the machine each ran on: its CPU, how many CPUs the build could
use, and its memory.</p>
<div class="chart-card">{machines_chart(site)}</div></section>
<section><h2>Records</h2>
{stats_table(["Of", "Pass", "Fail", "Skip", "Error", "Unreadable", "Total"], records)}
</section>
<section><h2>Suites</h2><p class="note text-muted-sm">The series with a record in
each suite, and those that introduced a failure, new lines or new findings in
it.</p>
{stats_table(["Suite", "Series", "Introduced", "Nothing introduced"], outcomes)}
</section>
"""
    return root_page(site, "Stats", body, script="stats.js")


def builds_table(head: str, rows: Iterable[str]) -> str:
    return (
        '<section><h2>Builds</h2><div class="scroll"><table class="rows">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody>"
        "</table></div></section>"
    )


def series_overview(
    overview: Overview,
    series: Series,
    base: Overview | None,
    marks: Marks,
    site: Site,
) -> str:
    root, dated = "../" * len(overview.parts), site.dated
    cover = overview.cover
    trees = [page.tree for page in overview.builds]
    base_commit = base_commit_html(overview.project, series)
    items = [
        *version_items(marks, overview.parts, root),
        ("Author", escape(author_of(cover, series))),
        *cover_items(cover, series.message_id, series.lists),
        *ref_items(series),
        ("Patches", str(len(series.commits))),
        ("Base", f"{base_link(root, series, base)} {base_commit}"),
        ("Report", f'<a href="{escape(url("", REPORT))}" class="mono">{REPORT}</a>'),
    ]
    if found := all_producers(trees):
        items.append(("Producer", producers_html(found)))
    when = "<th>Recorded</th>" if dated else ""
    rows = (
        covered(
            f"<tr><td>{tree_link(root, page.tree, page.tree.named, main=True)}</td>"
            f"<td>{suite_findings(page)}</td>"
            f"{recorded_td(page.tree.recorded, dated=dated)}"
            f"<td>{producers_html(producers(page.tree))}</td></tr>",
            escape(url(root, *page.tree.parts, "index.html")),
        )
        for page in overview.builds
    )
    head = f"<th>Build</th><th>Findings</th>{when}<th>Producer</th>"
    return f"""
{back_link(root, site.page("index.html"))}
<section><h1>{escape(title_of(cover, series))}</h1>
<p class="muted mono">{commit_html(overview, series)}</p></section>
<section>{definitions(items)}</section>
{cover_section(cover)}<section><h2>Patches</h2>{patches_table(series)}</section>
{builds_table(head, rows)}
{b4_section(overview.b4_log)}"""


def b4_section(log: str | Unreadable | None) -> str:
    """What b4 printed applying the series, whole and as written."""
    if log is None:
        return ""
    if isinstance(log, Unreadable):
        shown = f'<p class="note">{unreadable_html(log)}</p>'
    elif rows := lines(log):
        shown = "<pre>" + "\n".join(map(pictured, rows)) + "</pre>"
    else:
        shown = '<p class="note muted">b4 printed nothing.</p>'
    return (
        '<section id="b4"><h2>b4</h2><p class="note text-muted-sm">What b4 printed '
        "applying the series: how it came to the base, and each base the series "
        f"would not apply onto first.</p>{shown}</section>\n"
    )


def base_overview(overview: Overview, site: Site) -> str:
    root = "../" * len(overview.parts)
    return f"""
{back_link(root, site.page("bases.html"))}
<section><h1 class="mono">{escape(overview.commit)}</h1>
<p class="muted">Records of this commit, one row per build; a series on it is set
against the build of the same name.</p></section>
{plain_builds(overview, root, dated=site.dated)}
"""


def plain_builds(overview: Overview, root: str, *, dated: bool) -> str:
    """The Builds table of builds set against nothing: records and failing."""
    when = "<th>Recorded</th>" if dated else ""
    rows = (
        covered(
            f"<tr><td>{tree_link(root, tree, tree.named, main=True)}</td>"
            f'<td class="num">{len(tree.records)}</td>'
            f'<td class="num">{failing_html(tree) or "0"}</td>'
            f"{recorded_td(tree.recorded, dated=dated)}"
            f"<td>{producers_html(producers(tree))}</td></tr>",
            escape(url(root, *tree.parts, "index.html")),
        )
        for tree in (page.tree for page in overview.builds)
    )
    head = (
        '<th>Build</th><th class="num">Records</th><th class="num">Failing</th>'
        f"{when}<th>Producer</th>"
    )
    return builds_table(head, rows)


def unapplied_page(item: Unapplied, marks: Marks, site: Site) -> str:
    """A series that does not apply: its cover letter, then what the tool printed."""
    root = "../" * len(item.parts)
    cover = item.cover
    if isinstance(cover, Cover):
        author = escape(cover.author) if cover.author else muted("not given")
        items = [("Author", author), *cover_items(cover)]
    else:
        items = [*cover_items(cover), ("Thread", thread_html(item.name))]
    items[:0] = version_items(marks, item.parts, root)
    record = item.record
    if isinstance(record, Record) and record.producer is not None:
        items.append(("Producer", producer_html(record.producer)))
    if isinstance(record, Record):
        whole = url("", *output_file(item.key))
        shown = command_html(record) + invocation_html(record)
        shown += output_html(record, whole)
    else:
        shown = f'<p class="note">{mono(f"{record.path}: {record.reason}")}</p>'
    tool = item.applier.value
    body = f"""
{back_link(root, site.page("index.html"))}
<section><h1>{escape(unapplied_title(item))}</h1>
<p class="muted">{tool} could not apply the series, so nothing of it was built or
checked.</p></section>
<section>{definitions(items)}</section>
<section><h2>{tool}</h2><details id="{anchors([item.key])[item.key]}" open>
<summary>{entry_head(item.key, record, None)}</summary>
{shown}</details></section>
"""
    return html_page(unapplied_title(item), root, body, site.footer)


def overview_page(
    overview: Overview,
    base: Overview | None,
    marks: Marks,
    site: Site,
) -> str:
    """A commit's page, Back to the listing of site, its project's, that has it."""
    root = "../" * len(overview.parts)
    if series := overview.series:
        body = series_overview(overview, series, base, marks, site)
        title = title_of(overview.cover, series)
    else:
        body = base_overview(overview, site)
        title = overview.commit
    return html_page(title, root, body, site.footer)


def definitions(items: Iterable[tuple[str, str]]) -> str:
    return "<dl>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in items) + "</dl>"


def unreadable_html(entry: Unreadable) -> str:
    return f"{badge('warn', 'unreadable')} {mono(f'{entry.path}: {entry.reason}')}"


def cover_items(
    cover: Cover | Unreadable | None, message_id: str = "", lists: Sequence[str] = ()
) -> list[tuple[str, str]]:
    """The cover letter's items; its thread by message_id when it names none.

    Then the series' lists: always beside a cover letter, else only when some.
    """
    thread = [("Thread", thread_html(message_id))] if message_id else []
    named = [("Mailing lists", lists_html(cover, lists))] if lists else []
    if cover is None:
        return [("Cover letter", muted("not recorded")), *thread, *named]
    if isinstance(cover, Unreadable):
        return [("Cover letter", unreadable_html(cover)), *thread, *named]
    if cover.text is not None:
        # A cover letter series.json holds: one kept in a commit has no thread.
        change = [("Change-id", mono(cover.change_id))] if cover.change_id else []
        return [("Date", escape(cover.date)), *change, *thread, *named]
    return [
        ("Date", escape(cover.date) if cover.date else muted("not given")),
        ("Thread", thread_html(cover.message_id or message_id)),
        ("Mailing lists", lists_html(cover, lists)),
    ]


def cover_section(cover: Cover | Unreadable | None) -> str:
    """The text of a cover letter series.json holds, open when an output shows whole."""
    if not isinstance(cover, Cover) or not (rows := lines(cover.text or "")):
        return ""
    opened = "" if len(rows) > CUT else " open"
    shown = "\n".join(map(pictured, rows))
    return (
        f'<section id="cover"><h2>Cover letter</h2><details{opened}>'
        f"<summary>{plural(len(rows), 'line')}</summary>"
        f"<pre>{shown}</pre></details></section>\n"
    )


def thread_html(message_id: str) -> str:
    href = thread_url(message_id)
    if href is None:
        return muted("no Message-Id")
    bare = message_id.strip().removeprefix("<").removesuffix(">")
    return f'<a href="{escape(href)}" class="mono">{escape(bare)}</a>'


def configured_html(how: Configured | None) -> str:
    if how is Configured.DIFFERENT:
        return " " + badge("warn", how.value)
    if how is Configured.UNKNOWN:
        return " " + muted(how.value)
    return ""


def machines(tree: Tree) -> list[str]:
    """The machine types tree's records ran on, the build's first."""
    found = [
        entry.host.label
        for _, entry in sorted(tree.records.items(), key=lambda i: i[0] != BUILD)
        if isinstance(entry, Record) and entry.host
    ]
    return list(dict.fromkeys(found))


def tail_items(tree: Tree, how: Configured | None = None) -> list[tuple[str, str]]:
    """What both kinds of page end their header with."""
    items: list[tuple[str, str]] = []
    if found := producers(tree):
        items.append(("Producer", producers_html(found)))
    if found := machines(tree):
        items.append(("Machine", "<br>".join(map(escape, found))))
    if tree.config is not None:
        link = f'<a href="./{CONFIG}">.config</a>'
        items.append(("Configuration", link + configured_html(how)))
    if tree.recorded:
        subject = muted(tree.recorded.subject)
        items.append(("Recorded", f"{escape(stamp(tree.recorded.when))} {subject}"))
    return items


def subject_html(commit: Commit) -> str:
    href = web_link(commit.link)
    if href is None:
        return escape(commit.subject)
    return f'<a href="{escape(href)}">{escape(commit.subject)}</a>'


def patches_table(series: Series) -> str:
    total = len(series.commits)
    rows = "".join(
        f"<tr><td>{number}/{total}</td><td>{subject_html(commit)}</td>"
        f"<td>{escape(commit.author)}</td><td>{mono(commit.commit[:12])}</td></tr>"
        for number, commit in enumerate(series.commits, start=1)
    )
    return (
        '<div class="scroll"><table><thead><tr><th>Patch</th><th>Subject</th>'
        f"<th>Author</th><th>Commit</th></tr></thead><tbody>{rows}</tbody></table></div>"
    )


def back_html(tree: Tree, root: str) -> str:
    """The commit linked to its overview page, then the build."""
    href = escape(url(root, tree.project, tree.commit, "index.html"))
    return f'<a href="{href}" class="mono">{escape(tree.commit)}</a> {mono(tree.named)}'


def series_head(page: Page, series: Series, root: str) -> str:
    tree = page.tree
    base_commit = base_commit_html(tree.project, series)
    items = [
        ("Author", escape(author_of(tree.cover, series))),
        *cover_items(tree.cover, series.message_id, series.lists),
        *ref_items(series),
        ("Patches", str(len(series.commits))),
        ("Base", f"{base_html(page, series, root)} {base_commit}"),
        ("Build", mono(tree.named)),
    ]
    if series.unbuilt:
        items.append(("Not built", escape(", ".join(series.unbuilt))))
    items.extend(tail_items(tree, page.configured))
    return f"""
<section><h1>{escape(title_of(tree.cover, series))}</h1>
<p class="muted">{back_html(tree, root)}</p></section>
<section>{definitions(items)}</section>
<section><h2>Patches</h2>{patches_table(series)}</section>
{config_section(page, root)}<p class="muted">Each record shows how its output
differs from its base's, positions aside: + a line the base's lacks, - one the
series' lacks, a compiler's finding marked whole by its first line and drawn as
that line, its source and its notes but a macro's.</p>
"""


def config_section(page: Page, root: str) -> str:
    """The diff of the base build's .config against the series build's."""
    if page.configured is not Configured.DIFFERENT or page.base is None:
        return ""
    href = escape(url(root, *page.base.parts, CONFIG))
    shown = diff_html(page.config_rows) or muted("No line differs.")
    return (
        "<section><h2>Configuration against the base</h2>"
        '<p class="note text-muted-sm">The options this build sets otherwise than '
        f"<a href=\"{href}\">its base's .config</a>, the series' own Kconfig "
        f"changes among them.</p>{shown}</section>\n"
    )


def own_commit_html(tree: Tree, series: Series) -> str:
    """A base's commit, linked in one of its trees, and whether the tree had changes."""
    found = (commit_url(public.url, series.commit) for public in series.trees)
    if href := next(filter(None, found), None):
        text = escape(series.commit[:12])
        shown = f'<a href="{escape(href)}" class="mono">{text}</a>'
    else:
        shown = base_commit_html(tree.project, series)
    dirty = tree.commit.endswith(DIRTY)
    return f"{shown} {muted('with changes not committed')}" if dirty else shown


def base_head(tree: Tree, root: str) -> str:
    items = [("Project", escape(tree.project)), ("Build", mono(tree.named))]
    if isinstance(tree.series, Unreadable):
        items.append(("Series", unreadable_html(tree.series)))
    elif tree.series:
        items.append(("Commit", own_commit_html(tree, tree.series)))
        items.extend(ref_items(tree.series))
    items.extend(tail_items(tree))
    return f"""
<section><h1 class="mono">{escape(tree.commit)}</h1>
<p class="muted">{back_html(tree, root)}: records of this commit built this way; a
series on it is set against them.</p></section>
<section>{definitions(items)}</section>
"""


def verdict(record: Record) -> str:
    """Its verdict, coloured as its class in style.css, and the exit status."""
    status = record.status.value
    if record.status is Status.SKIPPED:
        return badge("none", status)
    return badge(status, f"{status}, exit {record.exit}")


type Cites = Callable[[str, str], str | None]


def revision(tree: Tree, series: Series | None) -> str:
    """The commit tree's records were made of, whole when a series.json names it.

    That is the tree's own, else series', by the hash in the tree's name;
    without either it is that hash, else the name, less DIRTY.
    """
    if isinstance(tree.series, Series) and tree.series.commit:
        return tree.series.commit
    name = tree.commit.removesuffix(DIRTY)
    found = DESCRIBED.search(name)
    if found is None:
        return name
    known = () if series is None else (series.base, *(c.commit for c in series.commits))
    return next((whole for whole in known if whole.startswith(found[1])), found[1])


def cites(tree: Tree, series: Series | None) -> Cites | None:
    """Where a path:LINE tree's records print shows that line, if anywhere."""
    host = CODE.get(tree.project)
    if host is None:
        return None
    commit = quote(revision(tree, series), safe="")
    blob = "/-/blob" if urlsplit(host).hostname == "gitlab.com" else "/blob"

    def href(path: str, line: str) -> str | None:
        # A file the build writes, which the tree does not hold.
        if "generated" in path.split("/"):
            return None
        return f"{host}{blob}/{commit}/{quote(path)}#L{line}"

    return href


def linked(text: str, cited: Cites) -> str:
    """The text escaped, each path:LINE it prints a link to that line."""
    shown: list[str] = []
    end = 0
    for found in CITED.finditer(text):
        href = cited(found[1], found[2])
        if href is None:
            continue
        shown.append(escape(text[end : found.start()]))
        shown.append(f'<a href="{escape(href)}">{escape(found[0])}</a>')
        end = found.end()
    shown.append(escape(text[end:]))
    return "".join(shown)


def pictured(line: str, cited: Cites | None = None) -> str:
    """The line escaped, its SGR colours as spans and other controls as pictures.

    A colour lasts until a code changes it or the line ends, so that each line
    of a diff stands alone.
    """
    shown: list[str] = []
    colour: str | None = None
    for index, part in enumerate(SGR.split(line)):
        if index % 2:
            for code in part.split(";"):
                if code in FOREGROUND:
                    colour = COLOURS.get(code)
                if code in EXTENDED:
                    break
        elif part:
            text = part.translate(PICTURES)
            text = escape(text) if cited is None else linked(text, cited)
            shown.append(f'<span class="{colour}">{text}</span>' if colour else text)
    return "".join(shown)


def output_html(record: Record, whole: str, cited: Cites | None = None) -> str:
    """The output, cut short with a link to whole, its text file, when long."""
    rows = lines(record.output)
    if not rows:
        return '<p class="note muted">The tool printed nothing.</p>'
    shown = "\n".join(pictured(row, cited) for row in rows[:CUT])
    note = ""
    if len(rows) > CUT:
        href = escape(whole)
        note = (
            f'<p class="note muted">The first {CUT} of {len(rows)} lines; '
            f'<a href="{href}">the whole output</a>.</p>'
        )
    return f"{note}<pre>{shown}</pre>"


def hunks(rows: Sequence[Row], context: int = CONTEXT) -> list[range]:
    """Each changed row with context rows either side, merged where they meet."""
    spans: list[range] = []
    for index, row in enumerate(rows):
        if row.mark is Mark.SAME:
            continue
        start, stop = max(0, index - context), min(len(rows), index + context + 1)
        if spans and start <= spans[-1].stop:
            spans[-1] = range(spans[-1].start, max(spans[-1].stop, stop))
        else:
            spans.append(range(start, stop))
    return spans


def diff_html(
    rows: Sequence[Row],
    context: int = CONTEXT,
    cited: Cites | None = None,
    base: Cites | None = None,
) -> str:
    """The hunks of a diff, with "…" where rows are left out.

    style.css draws each row's mark, outside the text a reader copies. A
    path:LINE links by cited, or by base in a row gone from the series.
    """
    skip = '<span class="skip"></span>'
    shown: list[str] = []
    end = 0
    for span in hunks(rows, context):
        if span.start > end:
            shown.append(skip)
        shown.extend(
            f'<span class="{row.mark.value}">'
            f"{pictured(row.text, base if row.mark is Mark.GONE else cited)}</span>"
            for row in rows[span.start : span.stop]
        )
        end = span.stop
    if not shown:
        return ""
    if end < len(rows):
        shown.append(skip)
    note = ""
    if len(shown) > CUT:
        note = f'<p class="note muted">The first {CUT} of {len(shown)} diff lines.</p>'
    return f"{note}<pre>" + "\n".join(shown[:CUT]) + "</pre>"


def anchors(keys: Iterable[str]) -> dict[str, str]:
    """An id per record key: "r-" and the key, other characters as "-", unique."""
    found: dict[str, str] = {}
    taken: set[str] = set()
    for key in sorted(keys, key=lambda key: key.split("/")):
        stem = anchor = "r-" + ANCHOR.sub("-", key)
        number = 1
        while anchor in taken:
            number += 1
            anchor = f"{stem}-{number}"
        taken.add(anchor)
        found[key] = anchor
    return found


def prominent(page: Page, key: str) -> bool:
    """Whether a series' record opens first: it is introduced, or differs.

    One labelled no base record, unreadable, or with no verdict, opens too.
    """
    comparison = page.comparisons.get(key)
    if comparison is None:
        return False
    opens = {
        Change.INTRODUCED,
        Change.NO_BASE,
        Change.UNREADABLE,
        Change.UNREADABLE_BASE,
        Change.ERRORS,
        Change.BASE_ERRORS,
    }
    return comparison.change in opens or bool(comparison.new or comparison.gone)


def base_href(page: Page, key: str, root: str, ids: Mapping[str, str]) -> str | None:
    """The base build page's anchor of the record of key, when it has one."""
    if page.base is None or key not in page.base.records:
        return None
    return url(root, *page.base.parts, "index.html") + "#" + ids[key]


def whole_output(record: Record) -> bool:
    """Whether a record's whole output goes into a text file beside its page."""
    return len(lines(record.output)) > CUT


def base_output_html(base: Tree, key: str, root: str, cited: Cites | None) -> str:
    entry = base.records[key]
    if isinstance(entry, Unreadable):
        return f'<p class="note">{mono(f"{entry.path}: {entry.reason}")}</p>'
    return output_html(entry, url(root, *base.parts, *output_file(key)), cited)


def outputs_html(anchor: str, outputs: Sequence[tuple[str, str]], base: str) -> str:
    """A button per output that shows it in place, then a link to base.

    Each button is a checkbox's label, so no script runs: style.css shows an
    output while its box is checked.
    """
    boxes = "".join(
        f'<input type="checkbox" class="toggle {name}" id="{name}-{escape(anchor)}">'
        for name, _ in outputs
    )
    labels = "".join(
        f'<label for="{name}-{escape(anchor)}" class="{name}">{name} output</label>'
        for name, _ in outputs
    )
    shown = "".join(
        f'<div class="out {name}"><p class="note text-muted-sm">{name} output</p>'
        f"{html}</div>"
        for name, html in outputs
    )
    link = f'<a href="{escape(base)}">base page</a>'
    return f'<div>{boxes}<p class="note toggles">{labels}{link}</p>{shown}</div>'


def entry_head(key: str, entry: Entry | None, comparison: Comparison | None) -> str:
    head = [mono(key.partition("/")[2] or key)]
    if isinstance(entry, Record):
        head.append(verdict(entry))
    if comparison is not None:
        head.append(badge(comparison.kind, comparison.label))
        head.extend(badge("warn", note) for note in comparison.notes)
    elif isinstance(entry, Unreadable):
        head.append(badge(Change.UNREADABLE.kind, Change.UNREADABLE.value))
    if isinstance(entry, Record):
        machine = entry.host.label if entry.host else None
        on = f' title="on {escape(machine)}"' if machine else ""
        head.append(f'<span class="muted"{on}>{entry.seconds} s</span>')
    return "".join(head)


def tool_html(tool: Tool) -> str:
    """A tool as named, then its build, a link where its url is one of the web."""
    build = escape(tool.build)
    if href := web_link(tool.url):
        build = f'<a href="{escape(href)}">{build}</a>'
    return f"{escape(tool.named)} ({build})"


def command_html(record: Record) -> str:
    """A record's command as a developer types it, and under it its tools."""
    tools = ", ".join(map(tool_html, record.tools))
    return (
        f'<p class="step muted mono">$ {escape(record.command_line)}</p>'
        f'<p class="note text-muted-sm">With {tools}</p>'
    )


def invocation_html(record: Record) -> str:
    """A record's latest run: its name, lab, start, time and machine, and what it took.

    Every figure of its resources follows, on a line of their own.
    """
    if record.producer is None:
        return ""
    said = f"Run {mono(record.run)} at {mono(record.origin)}, started "
    said += f"{escape(record.started)}, {record.seconds} s"
    if record.host:
        said += f" on {escape(record.host.label)}"
    if record.resources:
        said += f"<br>{escape(resources_text(record.resources))}"
    return f'<p class="note text-muted-sm">{said}</p>'


def entry_html(
    page: Page, key: str, *, anchor: str, root: str, base_ids: Mapping[str, str]
) -> str:
    entry = page.tree.records.get(key)
    comparison = page.comparisons.get(key)
    if isinstance(entry, Record):
        body = command_html(entry) + invocation_html(entry)
        whole = url("", *output_file(key))
        base = base_href(page, key, root, base_ids)
        cited = cites(page.tree, page.series)
        if base is None or page.base is None:
            body += output_html(entry, whole, cited)
        else:
            # With no diff to show, the series' output shows whole instead.
            alone = (
                comparison is not None and comparison.change is Change.UNREADABLE_BASE
            )
            base_cited = cites(page.base, page.series)
            outputs = [("base", base_output_html(page.base, key, root, base_cited))]
            if not alone:
                outputs.insert(0, ("series", output_html(entry, whole, cited)))
            body += outputs_html(anchor, outputs, base)
            if alone:
                body += output_html(entry, whole, cited)
            elif comparison is not None:
                body += diff_html(
                    comparison.rows, comparison.context, cited, base_cited
                )
    elif isinstance(entry, Unreadable):
        body = f'<p class="note">{mono(f"{entry.path}: {entry.reason}")}</p>'
    else:
        body = '<p class="note muted">The base has this record; the series has not.</p>'
    opened = " open" if prominent(page, key) else ""
    return (
        f'<details id="{escape(anchor)}"{opened}>'
        f"<summary>{entry_head(key, entry, comparison)}</summary>"
        f"{body}</details>"
    )


def tree_page(page: Page, footer: str) -> str:
    tree = page.tree
    root = "../" * len(tree.parts)
    if page.series:
        head = series_head(page, page.series, root)
        title = f"{title_of(tree.cover, page.series)} {tree.named}"
    else:
        head = base_head(tree, root)
        title = f"{tree.commit} {tree.named}"
    keys = list(page.comparisons if page.series else tree.records)
    keys.sort(key=lambda key: not prominent(page, key))
    ids = anchors(keys)
    base_ids = anchors(page.base.records) if page.base else {}
    sections = "".join(
        f"<section><h2>{escape(name)}</h2><div>"
        + "".join(
            entry_html(page, key, anchor=ids[key], root=root, base_ids=base_ids)
            for key in keys
            if suite_of(key) == name
        )
        + "</div></section>"
        for name in suites(keys)
    )
    body = back_link(root, tree.project, tree.commit, "index.html") + head + sections
    return html_page(title, root, body, footer)


def footer_text(head: Head | None, epoch: int | None) -> str:
    if head:
        source = f"the records at {head.commit}, committed {stamp(head.when)} UTC"
    elif epoch is not None:
        when = stamp(datetime.fromtimestamp(epoch, UTC))
        source = f"a directory of records on {when} UTC"
    else:
        source = "a directory of records"
    return f"Rendered by krender from {source}."


# Report: a series' reply to its thread, as plain text, from the values above;
# nothing here reads or writes a file.

# Beside a series' overview page. Another tool mails it (designs/site.md).
REPORT = "report.txt"
# The columns of what a report says; what a tool printed keeps its own.
WIDTH = 72
RULE = "-" * WIDTH
# The longest line RFC 5322 allows, in octets, and what ends one cut to fit.
LONGEST = 998
TRUNCATED = " [truncated]"
# A patch's Link: trailer as b4 and lore write it, and the Message-ID in it.
MSGID_LINK = re.compile(
    r"https://(?:patch\.msgid\.link|lore\.kernel\.org/[^/]+)/([^/]+)/?"
)
MIME = (
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=utf-8\n"
    "Content-Transfer-Encoding: 8bit\n"
)
SIGNATURE = f"-- \n{NAME} {HOME}"
# How the producer named KDEVOPS_NG_NAME applies a series, which a report
# repeats when it alone ran the series' records: in the shell of its flake,
# pinned by a revision after a "/", and committed as itself on the author's
# date, so that a commit's hash depends on the series alone.
FLAKE = "github:linux-kdevops/kdevops-ng"
COMMITTER = (
    "GIT_COMMITTER_NAME=kdevops-ng",
    "GIT_COMMITTER_EMAIL=kdevops-ng@localhost",
)
SHAZAM_FLAGS = (
    "GIT_CONFIG_COUNT=1",
    "GIT_CONFIG_KEY_0=b4.shazam-am-flags",
    "GIT_CONFIG_VALUE_0=--committer-date-is-author-date",
)


def prose(text: str, indent: str = "", hang: str = "") -> str:
    """What a report says, in ASCII and wrapped at WIDTH, words kept whole."""
    plain = text.replace("\N{MINUS SIGN}", "-")
    plain = plain.encode("ascii", "backslashreplace").decode("ascii")
    return textwrap.fill(
        plain,
        WIDTH,
        initial_indent=indent,
        subsequent_indent=hang or indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def verbatim(line: str) -> str:
    """A line a tool printed, without SGR colours, other controls as pictures.

    A line longer than LONGEST octets is cut short, and says so.
    """
    text = SGR.sub("", line).translate(PICTURES)
    data = text.encode(errors="backslashreplace")
    if len(data) <= LONGEST:
        return text
    return data[: LONGEST - len(TRUNCATED)].decode(errors="ignore") + TRUNCATED


def public(address: str, *parts: str) -> str:
    """The absolute URL of parts on the site published at address."""
    return url(address.rstrip("/") + "/", *parts)


def reply_id(overview: Overview, series: Series) -> str:
    """The bare Message-ID a report replies to: the cover's, else the series'.

    Without either, it is patch 1's, in its Link: trailer when it has one.
    """
    if found := message_id_of(overview.cover) or series.message_id:
        return found
    link = MSGID_LINK.fullmatch(series.commits[0].link)
    return bare_id(unquote(link[1])) if link else ""


def report_headers(subject: str, message_id: str) -> str:
    """A reply's headers, folded and, past ASCII, encoded as RFC 2047 asks.

    The sender adds From, To, Cc, Date and Message-ID.
    """
    subject = " ".join(subject.split())
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    fields = [("Subject", subject)]
    if message_id:
        fields += [(name, f"<{message_id}>") for name in ("In-Reply-To", "References")]
    return "".join(policy.default.fold(name, value) for name, value in fields) + MIME


def build_summary(page: Page) -> str:
    """A build and what the HTML findings line counts of it, in words."""
    if not page.series:
        said = ["not set against a base"]
    elif not page.comparisons:
        said = ["no records"]
    else:
        found = findings_of(page.comparisons.values())
        said = [f"{total} {meaning}" for _, total, meaning in found]
        said = said or ["nothing introduced"]
        if page.configured is Configured.DIFFERENT:
            said.insert(0, Configured.DIFFERENT.value)
    return prose(f"{page.tree.named}: {', '.join(said)}", "  ", "    ")


def printed(comparison: Comparison, record: Record) -> list[str]:
    """The lines the series printed that its base lacks, else all it printed.

    A diff has none when the base printed the same, or there is no base
    output, as for a check of the series itself.
    """
    found = [row.text for row in comparison.rows if row.mark is Mark.ADDED]
    return found or lines(record.output)


def shell(*words: str) -> str:
    """A command as a "$ " line, continued with backslashes past WIDTH.

    A word longer than a line stays whole.
    """
    rows = ["$"]
    for n, word in enumerate(words, 1):
        joined = f"{rows[-1]} {word}"
        room = WIDTH - (len(" \\") if n < len(words) else 0)
        if len(joined) <= room or rows[-1] == "$":
            rows[-1] = joined
        else:
            rows[-1] += " \\"
            rows.append("    " + word)
    return "\n".join(rows)


def ran_by(overview: Overview) -> list[Producer]:
    """Each producer that ran a record of the series, in any of its builds."""
    return all_producers(page.tree for page in overview.builds)


def pinned(overview: Overview) -> str | None:
    """The one revision of its producer that ran every record, if public.

    None when two producers or revisions ran them, or it is -dirty.
    """
    named = ran_by(overview)
    match = REVISION.fullmatch(named[0].version) if len(named) == 1 else None
    return named[0].version if match and not match[2] else None


def reproduce(overview: Overview, series: Series, message_id: str) -> str:
    """The commands that apply the series as its producer did, commit for commit.

    Of a producer other than KDEVOPS_NG_NAME, only what any takes: the base
    and b4.
    """
    if {producer.name for producer in ran_by(overview)} != {KDEVOPS_NG_NAME}:
        commands = [
            shell("git", "checkout", series.base),
            shell("b4", "shazam", message_id),
        ]
        return "Run these in a clone of the project.\n\n" + "\n".join(commands)
    said = [
        (
            "Run these in a Linux clone. The exports make git commit as kdevops-ng"
            " did, so commit hashes in the output match."
        )
    ]
    flake = FLAKE
    if revision := pinned(overview):
        flake += "/" + revision
    else:
        said.append(
            "The records don't name one published kdevops-ng revision, so"
            " this takes the latest."
        )
    commands = [
        shell("git", "checkout", series.base),
        shell("nix", "develop", flake),
        shell("export", *COMMITTER),
        shell("export", *SHAZAM_FLAGS),
        shell("b4", "shazam", "--add-link", message_id),
    ]
    return "\n".join(map(prose, said)) + "\n\n" + "\n".join(commands)


def build_config(page: Page, address: str | None) -> list[str]:
    """How to put a build's .config where a check of the build reads it."""
    if page.tree.config is None:
        return []
    if address:
        where = public(address, *page.tree.parts, CONFIG)
        return [shell("curl", "--create-dirs", "--output", "build/.config", where)]
    return [
        prose(
            f"Its .config is {CONFIG} beside the build's page; copy it to"
            " build/.config first."
        )
    ]


def quoted(rows: Sequence[str], seen: dict[str, str], name: str) -> str:
    """Up to CUT lines a tool printed, verbatim, and how many more there are.

    A line an earlier section quoted, as seen names, is not quoted again:
    one note per such section, where its first line was, counts them. seen
    gains the lines quoted here.
    """
    shown: list[str] = []
    repeated: Counter[str] = Counter()
    at: dict[str, int] = {}
    ours: dict[str, str] = {}
    for line in map(verbatim, rows[:CUT]):
        if (first := seen.get(line)) is None:
            shown.append(line)
            ours.setdefault(line, name)
            continue
        if first not in at:
            at[first] = len(shown)
            shown.append("")
        repeated[first] += 1
    for first, count in repeated.items():
        shown[at[first]] = prose(
            f"[{plural(count, 'line')} as quoted above for {first}]"
        )
    if len(rows) > CUT:
        shown.append(f"[{len(rows) - CUT} more lines]")
    seen.update(ours)
    return "\n".join(shown or ["[The tool printed nothing.]"])


def report_head(
    page: Page, key: str, record: Record, link: str | None, fetch: list[str]
) -> list[str]:
    """Whose a section is, how it stands, where, and the commands to rerun it.

    fetch puts the build's .config in place first. Its tools follow, each
    as named and with its build.
    """
    comparison = page.comparisons[key]
    head = [RULE, prose(f"{key}, {page.tree.named}")]
    head += [prose(", ".join([comparison.label, *comparison.notes]))]
    head += [*filter(None, [link]), *fetch, "$ " + verbatim(record.command_line)]
    tools = ", ".join(f"{tool.named} ({tool.build})" for tool in record.tools)
    return [*head, prose(f"With {tools}")]


def report_sections(page: Page, address: str | None, seen: dict[str, str]) -> list[str]:
    """A section per record the series introduced, or added lines to, in a build.

    Its anchor is the build page's, which anchors() keeps by the keys alone.
    The first check of the configured build, not of the series alone,
    fetches the build's .config. seen is as quoted() keeps it.
    """
    ids = anchors(page.comparisons)
    page_url = public(address, *page.tree.parts, "index.html") if address else None
    fetch = build_config(page, address)
    sections: list[str] = []
    for key, comparison in page.comparisons.items():
        record = page.tree.records.get(key)
        if comparison.change not in INTRODUCING or not isinstance(record, Record):
            continue
        link = f"{page_url}#{ids[key]}" if page_url else None
        configured = not record.series_check
        head = report_head(page, key, record, link, fetch if configured else [])
        if configured:
            fetch = []
        rows = printed(comparison, record)
        name = f"{key}, {page.tree.named}"
        sections.append("\n".join(head) + "\n\n" + quoted(rows, seen, name))
    return sections


def report(overview: Overview, series: Series, address: str | None = None) -> str:
    """A series' report: a reply to its thread with what it introduced.

    It depends on the records and address alone, so a rerender is identical;
    without address it links nothing.
    """
    message_id = reply_id(overview, series)
    count = len(series.commits)
    patches = f"{count} patch{'' if count == 1 else 'es'}"
    base = series.base_name
    if not DESCRIBED.search(base):
        base += f" ({series.base[:12]})"
    names = sorted({producer.name for producer in ran_by(overview)})
    by = " and ".join(names) or "A producer"
    said = [
        prose(
            f"{by} built this series of {patches} on {base} and ran the"
            " same builds and checks on the base. Findings by build, set"
            " against the base:"
        ),
        "\n".join(map(build_summary, overview.builds)),
    ]
    if address:
        said.append("Full results:\n" + public(address, *overview.parts, "index.html"))
    if message_id:
        said.append(reproduce(overview, series, message_id))
    seen: dict[str, str] = {}
    for page in overview.builds:
        said += report_sections(page, address, seen)
    headers = report_headers(title_of(overview.cover, series), message_id)
    return headers + "\n" + "\n\n".join([*said, SIGNATURE]) + "\n"


# Write: the only part that touches the output directory.


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="backslashreplace")


# Marks a site render wrote, which a later render may replace.
MARK = ".render"


def refusal(records: Path, output: Path) -> str | None:
    """Why render will not write into output, or None."""
    if output.resolve().is_relative_to(records.resolve()):
        return f"--output {output} is inside --records {records}"
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        return f"--output {output} is not a directory"
    if output.exists() and any(output.iterdir()) and not (output / MARK).is_file():
        return f"--output {output} is not empty and holds no site render wrote"
    return None


def replace(staging: Path, output: Path) -> None:
    """Put staging at output, in place of an empty dir or render's own site."""
    if not output.exists():
        staging.rename(output)
        return
    old = staging.with_name(staging.name + ".old")
    output.rename(old)
    staging.rename(output)
    shutil.rmtree(old)


def make_site(
    records: Path,
    output: Path,
    epoch: int | None = None,
    revision: str | None = None,
    address: str | None = None,
) -> None:
    """Write the site into output; on RenderError, nothing is written."""
    if reason := refusal(records, output):
        raise RenderError(reason)
    git = git_of(records)
    history = history_of(git, records) if git else {}
    trees = fold_earlier(load(records, history))
    unapplied = load_unapplied(records, history, trees)
    named = {tree.project for tree in trees} | {item.project for item in unapplied}
    if clash := sorted(n for n in named if own_file(n, named)):
        raise RenderError(f"a project named {clash[0]} would overwrite the site's own")
    if clash := sorted({t.parts[2] for t in trees} & {"index.html", REPORT}):
        raise RenderError(f"a build under {clash[0]} would overwrite its commit's")
    site = Site(
        overviews_of(pages_of(trees)),
        unapplied,
        footer_text(head_of(git, records) if git else None, epoch),
        dated=git is not None,
        revision=revision,
        address=address,
    )
    parent = output.absolute().parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=parent))
    try:
        write_site(staging, site)
        replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def write_site(output: Path, site: Site) -> None:
    """Write every page of the site into output, project by project."""
    write(output / MARK, "A site render wrote; the next render replaces it.\n")
    write(output / "style.css", STYLE)
    write(output / "theme.js", THEME)
    write(output / "search.js", SEARCH)
    write(output / "settings.js", SETTINGS)
    write(output / "stats.js", STATS)
    for part in project_sites(site):
        for name, page in index_pages(part).items():
            write(output / part.page(name), page)
        write_project(output, part)


def write_project(output: Path, site: Site) -> None:
    """Write the pages of each commit and series of site, a project's."""
    by_parts, footer, versions = site.by_parts, site.footer, site.versions
    for item in site.unapplied:
        out = output.joinpath(*item.parts)
        marks = Marks(versions.get(item.parts, ()))
        write(out / "index.html", unapplied_page(item, marks, site))
        if isinstance(item.record, Record) and whole_output(item.record):
            write(out.joinpath(*output_file(item.key)), item.record.output)
    for overview in site.overviews:
        series = overview.series
        base = by_parts.get((overview.project, series.base_name)) if series else None
        marks = Marks(versions.get(overview.parts, ()))
        page = overview_page(overview, base, marks, site)
        write(output.joinpath(*overview.parts, "index.html"), page)
        if series:
            text = report(overview, series, site.address)
            write(output.joinpath(*overview.parts, REPORT), text)
        for build in overview.builds:
            tree = build.tree
            out = output.joinpath(*tree.parts)
            write(out / "index.html", tree_page(build, footer))
            if tree.config is not None:
                (out / CONFIG).write_bytes(tree.config)
            for key, entry in tree.records.items():
                if isinstance(entry, Record) and whole_output(entry):
                    write(out.joinpath(*output_file(key)), entry.output)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="render",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--records", type=Path, required=True, metavar="DIR")
    parser.add_argument("--output", type=Path, required=True, metavar="DIR")
    parser.add_argument(
        "--revision", metavar="REV", help="the revision of render the site names"
    )
    parser.add_argument(
        "--url", metavar="URL", help="the site's public address, which reports link"
    )
    args = parser.parse_args()
    records: Path = args.records
    output: Path = args.output
    address: str | None = args.url
    if address is not None and web_link(address) is None:
        parser.error(f"--url {address} is not an http or https URL")
    if not records.is_dir():
        parser.error(f"--records {records} is not a directory")
    epoch = os.environ.get("SOURCE_DATE_EPOCH") or None
    if epoch is not None and not (epoch.isascii() and epoch.isdigit()):
        parser.error("SOURCE_DATE_EPOCH is not a whole number of seconds")
    try:
        revision: str | None = args.revision
        make_site(records, output, int(epoch) if epoch else None, revision, address)
    except RenderError as error:
        sys.exit(f"render: {error}")


if __name__ == "__main__":
    main()
