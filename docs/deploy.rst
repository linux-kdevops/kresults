.. SPDX-License-Identifier: copyleft-next-0.3.1

==========
Deployment
==========

The three programs are scripts under :src:`format`, each run from a clone
or under its name on ``PATH``. They need `Python`_ 3.13 or later and `Git`_,
and ``validate`` also needs `jsonschema`_ (``python3-jsonschema``).

.. code-block:: console

   $ git clone https://github.com/linux-kdevops/kresults.git
   $ cd kresults
   $ ln --symbolic --verbose "$PWD/format/record.py" ~/.local/bin/krecord
   $ ln --symbolic --verbose "$PWD/format/render.py" ~/.local/bin/krender

With `Nix`_, the flake installs all three and the units:

.. code-block:: console

   $ nix profile add .#kresults

Writing records
===============

Name your lab once, in lowercase letters, digits and ``-``:

.. code-block:: console

   $ git config --global kresults.origin my-lab

``krecord`` files a record of one run of a command. Run the command as a
transient `systemd`_ user unit, in the tree, and name ``krecord`` as the
unit's ``ExecStopPost=``:

.. code-block:: text

   $ systemd-run --user --wait --collect --verbose --setenv=PATH \
       --working-directory="$PWD" \
       --property=ExecStopPost='-env krecord --records ../records
           --output make/all.json --command make
           --config build/.config --configuration tinyconfig
           --priority warning
           --tool make --tool gcc --env ARCH --env LLVM
           --producer by-hand --producer-version 1 --run manual' \
       systemd-cat --stderr-priority=warning make O=build

systemd runs ``krecord`` when the command exits, pass or fail, with its
exit status. ``krecord`` reads what the unit printed from your
journal, what the run took from the unit's cgroup, and the commit, the
``.config`` and the tools' builds from the tree and the host.
:cmd:`systemd-cat` logs make's standard error as warnings and
``--priority warning`` keeps only those, so the journal has make's
progress and the record does not.

A run the format cannot file leaves no record: ``krecord`` says why in one
line and exits 0. That is a run with no lab named, outside a Git work
tree, whose ``--config`` file is missing, or that a signal ended.

``krecord`` reads your own journal, which `journald`_ keeps only when it
stores on disk: ``/var/log/journal/*/user-UID.journal`` exists.

Checking records
================

.. code-block:: console

   $ format/validate.py --records ../records
   $ nix run .#validate -- --records ../records

``validate`` prints each file that breaks the format, and why, and exits 1
if any does. It refuses a version it does not know. A producer complies
with a version of the format when ``validate`` accepts every file it
writes.

Reading records
===============

``krender`` writes a tree of records as a static site, plain HTML and CSS
that reads the same with no script:

.. code-block:: console

   $ krender --records ../records --output public

Rendering again into the same ``--output`` replaces the site. Its About
page says how to read it.

Serving a site
==============

Three user units under :src:`units/user` keep a site of a tree current and
serve it on 127.0.0.1, port 8002:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Unit
     - What it does
   * - ``kresults-serve@.service``
     - Serves the site. Starting it starts the other two.
   * - ``kresults-render@.path``
     - Watches the top of the records tree.
   * - ``kresults-render@.service``
     - Runs ``krender`` once, into ``~/.cache/kresults``.

A unit's instance is the tree's path, as :cmd:`systemd-escape` writes it.
Link the units once, then enable the one that serves:

.. code-block:: console

   $ systemctl --user link "$PWD"/units/user/kresults-*
   $ systemctl --user enable --now \
       "kresults-serve@$(systemd-escape --path ~/records).service"

Installed with Nix, the units are in the profile and need no link:

.. code-block:: console

   $ systemctl --user enable --now \
       ~/.nix-profile/lib/systemd/user/kresults-serve@.service
   $ systemctl --user start \
       "kresults-serve@$(systemd-escape --path ~/records).service"

The path unit watches the top of the tree alone, since `inotify`_ reports
nothing from below a directory. ``krecord`` touches it after it writes, and
a producer that writes records itself does the same.

A drop-in (:cmd:`systemd.unit`) changes a setting, such as the port:

.. code-block:: console

   $ mkdir --parents --verbose ~/.config/systemd/user/kresults-serve@.service.d

.. code-block:: ini
   :caption: ~/.config/systemd/user/kresults-serve@.service.d/port.conf

   [Service]
   Environment=PORT=8080

.. code-block:: console

   $ systemctl --user daemon-reload

:cmd:`journalctl` ``--user-unit 'kresults-render@*'`` has each render, and
why one failed.

.. _Git: https://git-scm.com
.. _inotify: https://man7.org/linux/man-pages/man7/inotify.7.html
.. _journald: https://www.freedesktop.org/software/systemd/man/latest/systemd-journald.service.html
.. _jsonschema: https://python-jsonschema.readthedocs.io/
.. _Nix: https://nixos.org
.. _Python: https://www.python.org
.. _systemd: https://systemd.io
