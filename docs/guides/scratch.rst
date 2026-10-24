Scratch protocols
=================

Qibocal's built-in protocols cover the usual calibration steps. When none
fits, agents may write their own protocols, but only in a **scratch
repository**: a Git repository whose commits make every run reproducible.
Agents never send code to run directly.

Scratch protocols are opt-in, per connection:

.. code-block:: sh

   qibocal-agent config set lab.scratch true

That is enough: the repository is created on first use under
``$XDG_DATA_HOME/qibocal/agents/scratch/lab``, and, for remote execution, a bare
repository receiving its commits under ``~/.local/share/qibocal/agents/scratch``
on the SSH host. ``scratch_path`` and ``scratch_remote`` override these
locations (see :doc:`configuration`); ``--no-scratch`` disables them for one
launch.

The repository
--------------

It is a plain package: a top-level ``__init__.py``, and one module per
protocol, defining Qibocal ``Protocol`` objects, typically adapted from a
built-in one. There is no packaging: to consolidate a protocol, move it out to
a distributed package. Protocols are addressed as ``<module>.<name>``, with
``source="scratch"`` in ``execute_protocol`` and ``get_protocol``.

Runs
----

A scratch protocol always runs the repository's committed ``HEAD``:
uncommitted changes, untracked files included, are refused. Before the first
run of each new commit, the agent gives the user the protocol's path and the
commit hash, and waits for approval: review the code on your own filesystem.

- **Locally**, the server imports the protocol from the repository itself.
- **Remotely**, ``qibocal-agent mcp`` pushes the commit to the bare repository,
  on a single ref (``refs/qibocal/scratch``), updated at each push. The server
  updates **one** working directory next to it to that commit, and imports from
  there. Nothing accumulates on the remote besides Git objects; older commits
  stay in your local history.

Each run records its source, the commit and the protocol's path. To run it
again, check that commit out locally and execute it. The MCP manages no other
Git operation: branches and history are yours, on the local repository.

Updating from a scratch run works within the session; later, while its commit
is still available: locally, as long as it is ``HEAD``; remotely, as long as
the bare repository has it.

Direct use
----------

Without ``qibocal-agent``, ``qibocal-mcp --scratch PATH`` serves the scratch
protocols of a local repository, with no remote support.

Displaying scratch runs in Qibocamp is tracked in
`qibocamp#87 <https://github.com/qiboteam/qibocamp/issues/87>`_.
