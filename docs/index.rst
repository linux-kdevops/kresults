.. SPDX-License-Identifier: copyleft-next-0.3.1

========
kresults
========

.. include:: ../README.md
   :parser: myst_parser.docutils_
   :start-after: # kresults
   :end-before: <!-- docs/index.rst includes

:src:`krecord <format/krecord.py>` files one run of a command, names the
commits of a series, lists what a build compiles of one and files the
results of a blktests run, each by a subcommand.
:src:`validate <format/validate.py>` checks a tree of :term:`records <Record>`,
and :src:`krender <format/render.py>` writes one as a static site.
:doc:`deploy` sets each up.

Community
=========

- Source and issues: `GitHub <https://github.com/linux-kdevops/kresults>`__.
- Chat: `Discord <https://bit.ly/linux-kdevops-chat>`__, or ``#kdevops`` on
  `OFTC IRC <https://webchat.oftc.net/?channels=kdevops>`__.
- Mailing list: ``kdevops@lists.linux.dev``.

.. toctree::
   :maxdepth: 2

   format
   versions
   terms
   deploy
   contributing
