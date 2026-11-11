# SPDX-License-Identifier: copyleft-next-0.3.1
import os
import re

from docutils import nodes

project = "kresults"
copyright = "2026, kresults authors"
author = "kresults authors"

extensions = [
    "myst_parser",
    "sphinx_copybutton",
]

# The format and its versions are Markdown beside the schemas, read here as
# they are (format.md, versions.md).
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}

# Show the "$ " prompt but strip it, and follow "\" continuations, on copy.
copybutton_prompt_text = r"\$ "
copybutton_prompt_is_regexp = True
copybutton_line_continuation_character = "\\"

exclude_patterns = [
    "_build",
]

html_theme = "pydata_sphinx_theme"
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "/")
html_static_path = ["_static"]
# A caption that names a file gets a button that copies its path.
html_css_files = ["custom.css"]
html_js_files = ["caption-copy.js"]
html_favicon = "_static/favicon.ico"
html_theme_options = {
    "logo": {
        "image_light": "_static/logo.png",
        "image_dark": "_static/logo.png",
        "alt_text": "kresults",
    },
    "navigation_with_keys": False,
    "navbar_align": "left",
    "header_links_before_dropdown": 8,
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/linux-kdevops/kresults",
            "icon": "fa-brands fa-github",
        },
        {
            "name": "Discord",
            "url": "https://bit.ly/linux-kdevops-chat",
            "icon": "fa-brands fa-discord",
        },
        {
            "name": "IRC: #kdevops on OFTC",
            "url": "https://webchat.oftc.net/?channels=kdevops",
            "icon": "fa-solid fa-comments",
        },
        {
            "name": "Mailing list: kdevops@lists.linux.dev",
            "url": "mailto:kdevops@lists.linux.dev",
            "icon": "fa-solid fa-envelope",
        },
    ],
}
html_context = {
    "default_mode": "light",
}

# ":cmd:`name`" renders ``name`` as a literal linked to its manual. This table
# is the one place a command's URL is written, so every page links a name the
# same way.
cmd_links = {
    "git": "https://git-scm.com/docs/git",
    "journalctl": "https://www.freedesktop.org/software/systemd/man/latest/journalctl.html",
    "nix": "https://nix.dev/manual/nix/latest/command-ref/new-cli/nix",
    "sphinx-build": "https://www.sphinx-doc.org/en/master/man/sphinx-build.html",
    "systemctl": "https://www.freedesktop.org/software/systemd/man/latest/systemctl.html",
    "systemd-cat": "https://www.freedesktop.org/software/systemd/man/latest/systemd-cat.html",
    "systemd-run": "https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html",
    "systemd.unit": "https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html",
}


def _cmd_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    uri = inliner.document.settings.env.config.cmd_links.get(text)
    code = nodes.literal(rawtext, text)
    if uri is None:
        msg = inliner.reporter.error(
            f"cmd: no link registered for {text!r} (add it to cmd_links in "
            f"docs/conf.py)",
            line=lineno,
        )
        return [code], [msg]
    return [nodes.reference(rawtext, "", code, refuri=uri)], []


# ":src:`path`" renders a repository path as a literal linked to its source on
# GitHub, and ":src:`title <path>`" links a short title. A path missing from
# the tree fails the build.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_BASE = "https://github.com/linux-kdevops/kresults"
_EXPLICIT_TITLE_RE = re.compile(r"^(.+?)\s*<(.+?)>$", re.DOTALL)


def _src_url(path):
    full = os.path.join(_REPO_ROOT, path)
    if os.path.isdir(full):
        return f"{_SRC_BASE}/tree/main/{path}"
    if os.path.isfile(full):
        return f"{_SRC_BASE}/blob/main/{path}"
    return None


def _src_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    m = _EXPLICIT_TITLE_RE.match(text)
    title, target = (m.group(1), m.group(2)) if m else (text, text)
    uri = _src_url(target)
    code = nodes.literal(rawtext, title)
    if uri is None:
        msg = inliner.reporter.error(
            f"src: no source found for {target!r} under the repository root",
            line=lineno,
        )
        return [code], [msg]
    return [nodes.reference(rawtext, "", code, refuri=uri)], []


def setup(app):
    app.add_config_value("cmd_links", {}, "env")
    app.add_role("cmd", _cmd_role)
    app.add_role("src", _src_role)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
