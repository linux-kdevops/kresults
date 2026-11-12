.. SPDX-License-Identifier: copyleft-next-0.3.1

===========
Terminology
===========

Each term has one meaning across the format, the programs, the commits and
these pages, and :doc:`format` gives it in full. The words under *Avoid* are
not used for it.

Records
=======

.. glossary::

   Record
      A JSON file of what tools reported on one :term:`subject <Subject>`,
      with one :term:`entry <Entry>` for each distinct run.

      Avoid: *report*, *result*, *artifact*.

   Subject
      What a record is about: a commit of a project, narrowed to a
      :term:`build <Build>`, a file, an object or a test in it, or a
      :term:`series <Series>` by its Message-ID.

   Entry
      One distinct run in a record: the tools and their builds, the
      command, its environment and ``.config``, and what the run reported.
      A run that differs in any of these is another entry.

   Invocation
      One time an entry's command ran: the :term:`producer <Producer>`,
      the :term:`lab <Lab>`, the run, when, on what machine, and what it
      took.

   Verdict
      What a run came to: ``pass``, ``fail``, ``skip`` or ``error``. The
      format calls it ``status``.

   Producer
      A program that runs tools and writes records, such as `kdevops-ng`_.

      Avoid: *engine*, *reporter*.

   Lab
      The name of the place records are made, in lowercase letters, digits
      and ``-``. A record carries it, and names no host. The format calls
      it ``origin``.

      Avoid: *site*, *hostname*.

Where records lie
=================

.. glossary::

   Build
      A :term:`configuration <Configuration>`, the machine the kernel
      built for, and a compiler, each a directory under a commit:
      ``tinyconfig/x86_64/clang/``. A record lies under the build its
      command ran in.

      Avoid: *build dir*, *output*, *objects*.

   Configuration
      What made a build's ``.config``: a make target, as ``tinyconfig``, or
      the :term:`profiles <Profile>` merged into it.

      Avoid: *config*, and *defconfig* for a target of another name.

   Profile
      Configuration fragments named after the ``MAINTAINERS`` entry whose
      code they configure, as ``block-layer``.

   Series
      Patches posted together, named by the Message-ID of the first
      message. Its records are set against its :term:`base <Base>`'s.

   Base
      The commit a series starts from.

.. _kdevops-ng: https://github.com/linux-kdevops/kdevops-ng
