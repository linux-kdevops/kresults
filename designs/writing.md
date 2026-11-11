<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->

# Writing commit messages, docs and comments

kresults is reviewed the way the kernel is, so its prose follows how the
kernel's own maintainers write. The rules below are the ones the kernel's
process documents state, and the numbers come from a sample of the 200
latest non-merge commits by each of 15 maintainers at Linux v7.3-rc7: Linus
Torvalds, Greg Kroah-Hartman, Thomas Gleixner, Andrew Morton, Ingo Molnar,
Christoph Hellwig, Jens Axboe, Darrick J. Wong, Dave Chinner, Theodore
Ts'o, Kees Cook, Jonathan Corbet, Paolo Bonzini, Jakub Kicinski and Al
Viro.

## Commits

A commit makes one change; a spelling fix does not ride along with code.
Its subject is `subsystem: summary` in the imperative, at most 75
characters, the subsystem naming the area changed (`format`, `render`,
`docs`, `kresults` for the project as a whole). Its body is plain
paragraphs wrapped at 75 columns, with no list, carrying only what the
diff does not show.

The message closes with a `Signed-off-by` in your Git identity, a
person's, who answers for the change under the
[Developer Certificate of Origin]. No trailer marks the use of a tool. A
message names a tool only when the tool found the problem the commit
fixes.

## Commit messages

What goes into the body comes from [submitting-patches.rst] and the
[tip tree] handbook:

- Lead with the problem, then its effect a developer would see, then the
  change in plain words. 86% of the sampled bodies open with context, not
  with the change.
- Say why, for a reader years later. The body should make sense to someone
  who never saw the discussion. Give numbers for a performance or size
  claim, and name what the change costs.
- Write in the imperative and do not narrate the patch. Give orders to the
  code base ("make xyzzy do frotz"); never open with the patch or the
  commit as the subject (maintainers do, in 0.2% of commits), and never
  retell the diff. Code is not a character either: "Cancel the worker"
  rather than "we make the handler cancel the worker".
- Keep it short. The median body is 6 lines (46 words), the upper quartile
  12, and only 5% use a list. One problem per commit; a long body is a
  sign the commit should be split.
- Cite a commit by its first 12 characters and its subject, and summarise
  a discussion rather than only linking it. Trim a log or a backtrace to
  the lines that show the problem.
- Name functions with parentheses, `foo()`, in the subject and the body,
  and commands, options and paths in plain text, without backticks.

### Examples

Read two before writing, with `git -C <linux> show --no-patch <hash>` in
any Linux clone; a list of banned words on its own mostly teaches a writer
to reach for a synonym.

- `43a1e3744548` (Linus Torvalds): a bug fix: the problem, why the obvious
  code missed it, the chosen fix and the alternative it rejects.
- `0e65cd9e5d41` (Paolo Bonzini): a preventive fix that says plainly
  nothing was observed, and why it is still right.
- `c793bbfc4a0a` (Thomas Gleixner): context, problem and fix in three
  short paragraphs, the tip tree's order.
- `a92193c91e88` (Jens Axboe): a regression fix whose evidence is a
  trimmed KASAN report.
- `ab1c416d2377` (Darrick J. Wong): names the tool that found the bug, and
  why the simple fix is enough.
- `c01e6df60e7b` (Christoph Hellwig): a feature: why it is useful, then
  its one design decision and the alternative.
- `12c84dd4d308` (Theodore Ts'o): a feature in five lines.
- `3ed403bbc967` (Thomas Gleixner): a cleanup in two sentences of reason
  and one order.
- `f2e65e4e5b4b` (Jonathan Corbet): a documentation fix: what the text
  claimed, why that is wrong, the change.
- `a5beeb64f226` (Al Viro): a comment-only commit whose body explains the
  locking it documents.
- `ac059ae422d7` (Ingo Molnar): a build fix with the compiler error and
  the bisect result, ending "Build tested only".
- `9db03d2116ab` (Jens Axboe): a revert that admits the wrong assumption
  and names the user it broke.
- `761e015e5a54` (Christoph Hellwig): a performance fix that says what
  must stay and which regression it ends.
- `d681d7ef617e` (Linus Torvalds): a merge: why it is merged now.

## Comments and docstrings

From the commenting sections of [coding-style.rst], the [tip tree]
handbook and the [KVM x86] handbook:

- Say what and why, never how. Write the code so that how it works is
  plain; a comment that repeats it is noise, and a comment cannot rescue
  code that is hard to read.
- Comment what would tempt a wrong cleanup: a barrier, a lock rule, an
  ordering that looks arbitrary. An assertion beats a comment where one
  can say it.
- Do not document the obvious. No comment that only names the next line's
  operation, no docstring that repeats the signature, and no history
  ("added", "fixed"): history belongs in git.
- Write in the imperative, without pronouns, with sentences that start
  with a capital letter.

Why a change was made goes in its commit body, not in a comment.

## Documentation

From the kernel's [doc guide]: keep the markup simple, update the content
and not only the formatting, and write for every reader, including one who
arrives from a search with no context.

A page starts with its SPDX line, wraps its prose at 80 columns, and
states each fact once: a second page that needs the fact links to the
first.

`docs/` says what exists, for whoever uses kresults. Its pages are
reStructuredText, built with Sphinx by `nix flake check`, which fails on a
warning. The format and its versions are the Markdown of `format/`, which
the site includes as it is:

- Headings take the kernel's adornments: `=` with an overline for the
  title, `=` for chapters and `-` for sections, in sentence case.
- Commands go in a `.. code-block:: console`, each after a `$` prompt, and
  a literal in double backticks.
- A command that installs a file or a directory, as `ln`, `mkdir` or `cp`
  does, takes `--verbose`, so its output shows what it installed.
- A file the reader writes is a code block of its content, captioned with
  its path, which a button copies. No command that prints a file or pipes
  text into one stands for it.
- A command is linked with the `:cmd:` role, which takes its manual from
  the `cmd_links` table of `docs/conf.py`, and a path of this repository
  with `:src:`. An unknown command or a missing path fails the build.
- Another page is linked with `:doc:` and a labelled section with `:ref:`,
  never by its path.
- The first mention on a page of an upstream project, tool or service is
  linked to its manual or source, by a named target at the foot of the
  page. Later mentions stay plain.
- The first mention on a page of a term of `docs/terms.rst` is linked to
  it with `:term:`, in whatever form the sentence takes:
  ``:term:`tasks <Task>` ``. Later mentions stay plain, and a word used in
  another sense than the glossary's is not linked.

## The checks

`scripts/check-prose.py` refuses the phrasing these maintainers almost
never write. The script holds the patterns and the rates measured for
them. It reads a commit message (`--message`), a commit's message with the
prose the commit adds (`--commit`), or whole files (`--file`). Prose is a
Markdown or reStructuredText file outside its code blocks, and the
comments and docstrings of code. By kind:

- Refused: the vocabulary of generated text, the patch or commit as the
  subject of a sentence, en and em dashes, filler openers and summary
  headings, a list in a commit body, and, in comments, history words and
  lines that only name an increment or decrement.
- Warned: inflated words, connective openers, padding such as a long way
  of saying "to", and, in comments, openers that restate the code or
  describe "this function" or a "helper". A warning prints and passes; a
  maintainer writes these now and then.

Some words stay off the list on purpose: robust, utilize, various and
properly appear in maintainer commits as often as anywhere else, so
refusing them would refuse good writing.

`nix flake check` runs it on the pages, the designs, the READMEs and the
programs' comments and docstrings, and holds the pages of `docs/` to 80
columns.

## A second reader

A pattern cannot tell whether a comment restates its code, or whether a
body gives the problem before the fix. That takes a reader. A writer
reviewing their own draft cuts less than a second reader does, so
for anything longer than a few lines, give the draft to a reader who did
not write it: a colleague, or a model in a fresh context. Hand it this
page, the draft, the diff, and this brief:

```text
You are the editor, not the author. Read designs/writing.md and two of
its example commits. For the draft below, list every sentence that
restates the diff, narrates the patch, explains how rather than why, or
could be deleted without loss, then give the shortest version that keeps
every fact a reviewer needs. Keep technical terms, numbers and commit
references exactly. Do not add anything.
```

[Developer Certificate of Origin]: https://developercertificate.org/
[submitting-patches.rst]: https://docs.kernel.org/process/submitting-patches.html
[tip tree]: https://docs.kernel.org/process/maintainer-tip.html
[coding-style.rst]: https://docs.kernel.org/process/coding-style.html
[KVM x86]: https://docs.kernel.org/process/maintainer-kvm-x86.html
[doc guide]: https://docs.kernel.org/doc-guide/sphinx.html
