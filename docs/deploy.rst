.. SPDX-License-Identifier: copyleft-next-0.3.1

==========
Deployment
==========

The five programs are scripts under :src:`format`, each run from a clone
or under its name on ``PATH``. They need `Python`_ 3.13 or later and `Git`_,
and ``validate`` also needs `jsonschema`_ (``python3-jsonschema``).

.. code-block:: console

   $ git clone https://github.com/linux-kdevops/kresults.git
   $ cd kresults
   $ ln --symbolic --verbose "$PWD/format/record.py" ~/.local/bin/krecord
   $ ln --symbolic --verbose "$PWD/format/render.py" ~/.local/bin/krender
   $ ln --symbolic --verbose "$PWD/format/series.py" ~/.local/bin/kseries
   $ ln --symbolic --verbose "$PWD/format/objects.py" ~/.local/bin/kobjects

With `Nix`_, the flake installs all five and the units:

.. code-block:: console

   $ nix profile add .#kresults

Writing records
===============

Name your :term:`lab <Lab>` once, in lowercase letters, digits and ``-``:

.. code-block:: console

   $ git config --global kresults.origin my-lab

``krecord`` files a :term:`record <Record>` of one run of a command. Run
the command as a transient `systemd`_ user unit, in the tree, and name
``krecord`` as the unit's ``ExecStopPost=``:

.. code-block:: text

   $ systemd-run --user --unit=make-build --wait --collect --verbose \
       --setenv=PATH \
       --working-directory="$PWD" \
       --property=StateDirectory=krecords \
       --property=ExecStopPost='-env krecord --command make
           --config build/.config --configuration tinyconfig
           --priority warning --producer by-hand --producer-version 1' \
       systemd-cat --identifier=make-build.service \
           --stderr-priority=warning make O=build

systemd runs ``krecord`` when the command exits, pass or fail, with its
exit status. ``krecord`` reads from your journal the lines since the unit
started whose identifier is the unit's name, which ``--identifier`` gives
them. It reads what the run took from the unit's cgroup, and the commit,
the ``.config`` and the tools' builds from the tree and the host.
:cmd:`systemd-cat` logs make's standard error as warnings and
``--priority warning`` keeps only those, so the journal has make's
progress and the record does not.

Records go to ``~/.local/state/krecords`` unless ``--records`` names
another tree, and the run is ``manual`` unless ``--run`` names it. The
record's name comes from the line of :src:`format/commands.json` whose
``argv`` the command matches, and ``--output`` names one no line matches.
The tools are the command's own, the compiler the ``.config`` names, and
any ``--tool``. The variables recorded are those the list names for the
command's tool, and any ``--env``. A producer whose settings are a file,
as a drop-in is, names its version by ``--producer-tree``, a directory
whose Git tree describes the producer.
``--configuration-file`` names a file another command left in the build,
and ``{configuration}`` in ``--output`` and ``--command`` stands for what
it holds. So do ``--range`` or ``--range-file``, whose ``{range}`` and
``{base}`` name the commits a tool read; ``--file`` takes the name of what
the command worked on, and ``{file}`` stands for it; ``{compiler}``, in
``--output`` alone, names the build's compiler. ``--arguments-file`` names
a file that holds the arguments that change from one run to the next, one
a line, and the record's
command is ``--command`` with each after it, quoted where a shell needs
it. ``--message-id-file`` names a file that holds the Message-ID
of the series a tool took, and the record lies under the Message-ID,
not the tree's commit.

A run the format cannot file leaves no record: ``krecord`` says why in one
line and exits 0. That is a run with no lab named, outside a Git work
tree, whose ``--config`` file is missing, or that a signal ended.

``krecord`` reads your own journal, which `journald`_ keeps only when it
stores on disk: ``/var/log/journal/*/user-UID.journal`` exists.

``kseries`` writes the ``series.json`` of commits that came from no
message. Run it at the top of the tree with their range, ``BASE..TIP``, or
``BASE..`` for the tree's ``HEAD``:

.. code-block:: console

   $ kseries --range v7.3..xfs-fix

It names the base, the commits the base lacks and the files they change
under the tip's directory, in place of the ``series.json`` ``krecord``
left there. It takes ``--project`` and ``--records`` as ``krecord`` does,
and exits 1 with the reason when the range names no such commits.

``kobjects`` writes the ``objects.json`` of one build of a series. Run it
at the top of the tree once the build is done and ``kseries`` has named
the series, with the ``.config`` and the configuration's name as
``krecord`` takes them:

.. code-block:: console

   $ kobjects --config build/.config --configuration defconfig

It lists the objects the build compiles from the series' C files, and the
C files it does not compile, beside the build's ``config``. It takes
``--configuration-file``, ``--project`` and ``--records`` as ``krecord``
does, and exits 1 with the reason when the commit has no series or the
build is not finished.

Checking records
================

.. code-block:: console

   $ format/validate.py --records ~/.local/state/krecords
   $ nix run .#validate -- --records ~/.local/state/krecords

``validate`` prints each file that breaks the format, and why, and exits 1
if any does. It refuses a version it does not know. A
:term:`producer <Producer>` complies with a version of the format when
``validate`` accepts every file it writes.

Reading records
===============

``krender`` writes a tree of records as a static site, plain HTML and CSS
that reads the same with no script:

.. code-block:: console

   $ krender --records ~/.local/state/krecords --output public

Rendering again into the same ``--output`` replaces the site. Its About
page says how to read it.

Serving a site
==============

Three user units under :src:`units/user` keep a site of
``~/.local/state/krecords`` current and serve it on 127.0.0.1, port 8002:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Unit
     - What it does
   * - ``kresults-serve.service``
     - Serves the site. Starting it starts the other two.
   * - ``kresults-render.path``
     - Watches the top of the records tree.
   * - ``kresults-render.service``
     - Runs ``krender`` once, into ``~/.cache/kresults/site``.

Link the units once, then enable the one that serves:

.. code-block:: console

   $ systemctl --user link "$PWD"/units/user/kresults-*
   $ systemctl --user enable --now kresults-serve.service

Installed with Nix, the units to link are
``~/.nix-profile/lib/systemd/user/kresults-*``.

The path unit watches the top of the tree alone, since `inotify`_ reports
nothing from below a directory. ``krecord`` touches it after it writes, and
a producer that writes records itself does the same.

A drop-in (:cmd:`systemd.unit`) changes a setting, such as the port:

.. code-block:: console

   $ mkdir --parents --verbose ~/.config/systemd/user/kresults-serve.service.d

.. code-block:: ini
   :caption: ~/.config/systemd/user/kresults-serve.service.d/port.conf

   [Service]
   Environment=PORT=8080

.. code-block:: console

   $ systemctl --user daemon-reload

Another records tree takes a drop-in on each of the two render units, for
``PathChanged=`` and ``ExecStart=``.

:cmd:`journalctl` ``--user-unit kresults-render`` has each render, and why
one failed.

.. _Git: https://git-scm.com
.. _inotify: https://man7.org/linux/man-pages/man7/inotify.7.html
.. _journald: https://www.freedesktop.org/software/systemd/man/latest/systemd-journald.service.html
.. _jsonschema: https://python-jsonschema.readthedocs.io/
.. _Nix: https://nixos.org
.. _Python: https://www.python.org
.. _systemd: https://systemd.io
