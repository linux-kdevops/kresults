.. SPDX-License-Identifier: copyleft-next-0.3.1

============
Contributing
============

Patches go to ``kdevops@lists.linux.dev``. A change is reviewed against the
decisions in :src:`designs/format.md` and :src:`designs/site.md`, and its
commits and prose follow :src:`designs/writing.md`.

One command runs the linters, the prose check, the tests and the build of
these pages, each with the flake's pinned tools:

.. code-block:: console

   $ nix flake check

Without `Nix`_, the tests need `Python`_ 3.13, `Git`_ and
``python3-jsonschema``, and the pages `Sphinx`_ with the `PyData theme`_,
`sphinx-copybutton`_ and `myst-parser`_:

.. code-block:: console

   $ python3 -m unittest discover --start-directory tests
   $ sphinx-build --fail-on-warning docs docs/_build/html

`sphinx-autobuild`_ serves the pages on 127.0.0.1 and builds them again on
each save:

.. code-block:: console

   $ nix develop --command sphinx-autobuild docs docs/_build/html --port 8003

.. _Git: https://git-scm.com
.. _myst-parser: https://myst-parser.readthedocs.io
.. _Nix: https://nixos.org
.. _PyData theme: https://pydata-sphinx-theme.readthedocs.io
.. _Python: https://www.python.org
.. _Sphinx: https://www.sphinx-doc.org
.. _sphinx-autobuild: https://github.com/sphinx-doc/sphinx-autobuild
.. _sphinx-copybutton: https://sphinx-copybutton.readthedocs.io
