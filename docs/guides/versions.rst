Platform versions
=================

Accepted updates and edits change the session's platform state, and are saved
as snapshots in its report folder. They reach the platform's own files only
when saved as a **version**. Until a database provides versions, they are
commits of the Git repository holding the platform (the folder found through
``QIBOLAB_PLATFORMS``), so every step can also be followed with Git and on
GitHub.

Navigating
----------

``list_versions`` shows the versions that changed the platform's files, newest
first, the known branches, the checked-out version, and the version the
current session started from (``session_base``). Any of them is loaded with
``start_session(source="repository", ref=...)``, without changing the files on
disk. ``diff_platform`` compares the session's state with the files on disk, or
with any version: it tells what is not saved yet.

Saving
------

``save_platform(message, branch)`` saves the session's state as a new commit on
``branch``, in two calls:

1. Without ``confirm``, nothing is written: it returns the changes from the
   version it would extend, and a confirm token.
2. With the token, after the user explicitly approves the changes, it commits.
   The token is invalidated if the state or the branch changes meanwhile.

A long calibration procedure keeps **one branch**, such as
``calibration/readout``, extended linearly across sessions: review it as a pull
request, and merge it like any other change. Branch consistency is enforced: an
existing branch is only extended from the version the session started from, or
saved last. If the branch moved meanwhile, the save is refused rather than
silently reverting those changes; start a session from the branch and apply the
changes there. A new branch starts from the session's base version.

Saving to any other branch than the checked-out one leaves the files on disk,
the checkout and its index untouched. Saving to the checked-out branch commits
only the platform's two files, which must have no uncommitted changes, and
updates them on disk. Commit messages record the session ID and platform.

With ``push=true``, requested explicitly, the branch is pushed to its remote
(``origin`` by default), never forced; for GitHub remotes, the result links to
open the pull request. Commits and pushes use the execution host's Git identity
and credentials: with SLURM, the access node's.

Checking out
------------

``checkout_platform(ref)``, on request, checks out a branch, or a version
detached, as the platform's files on disk: what new servers load at startup.
It does not change the running session; load the new files with
``start_session(source="repository")``. It refuses to run over uncommitted
changes in the repository.

Confirmation is currently a protocol between the model and the server; asking
the user directly, through the client, is tracked in
`issue #49 <https://github.com/qiboteam/qibocal-mcp/issues/49>`_.
