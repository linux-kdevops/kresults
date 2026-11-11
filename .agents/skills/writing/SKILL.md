---
# SPDX-License-Identifier: copyleft-next-0.3.1
name: writing
description: >-
  Use when writing or editing a commit message, a page or a code comment
  in this repository. It is the pass a draft gets before it is committed:
  it removes the patterns of generated text and checks the project's form.
---

# Writing in kresults

Read `designs/writing.md` first. It is the rule, and this is the pass a
draft gets before it is committed.

1. Draft to the rules: the problem first, then why, in the imperative.
2. Remove what marks generated text, and add nothing:
   - a sentence that restates the diff or narrates the patch;
   - three or more clauses stacked with commas, "so", "which" or a
     semicolon, which become two sentences;
   - a list of three made to sound complete, cut to what is true;
   - "not X but Y" and "not only";
   - an inflated word where a plain one does;
   - "serves as" and "acts as" for "is";
   - a phrase in -ing tacked on for weight;
   - an em or en dash, bold type, a heading in title case;
   - an opening or a closing line that says nothing.
3. Keep the kernel's register: no first person, no opinion, no humour.
4. Check the form. A subject is `subsystem: summary` in 75 columns. A body
   is wrapped at 75 and has no list. A page is wrapped at 80. The only
   trailer is the owner's `Signed-off-by`.
5. Hand anything longer than a few lines to a second reader, with the
   brief at the end of the writing rules.
