Tutorial: the autonomous agent
==============================

In this tutorial, ``qibocal-agent`` calibrates on its own: you give it an
objective, and it plans, runs and inspects experiments without asking you at
each step. It reuses the setup from :doc:`interactive`, so complete that
tutorial first.

.. figure:: ../_images/local-workflow.svg
   :alt: An objective and platform context guide acquisition. The returned fit, table and image are inspected before an explicit update or rejection; follow-up acquisitions depend on the remaining budget.
   :width: 100%

   The agent separates acquiring evidence from accepting a fitted update.

1. Choose a model
-----------------

Unlike your interactive assistant, the agent needs its own model. Choose one
that can read images and call tools, and store it in the configuration:

.. code-block:: sh

   .venv/bin/qibocal-agent config set default.model openai:gpt-4.1

Then make the provider's credentials available in your terminal:

.. code-block:: sh

   export OPENAI_API_KEY=...

.. tip::

   Skip the export if your credentials are already configured. GitHub Copilot
   subscriptions, OpenWebUI and other providers are described in
   :doc:`../guides/model-providers`. Credentials never go in the configuration
   file.

2. Run an objective
-------------------

The objective is everything the agent knows about your intentions. Always
include what to calibrate, the hardware limits, a budget and when to stop:

.. code-block:: sh

   .venv/bin/qibocal-agent run \
     "Calibrate qubit 0's drive frequency using spectroscopy. Sweep no wider
      than 20 MHz, use drive amplitude at most 0.1 and at most 100 shots.
      Run at most two protocols. Inspect plots before accepting an update.
      Stop on errors or insufficient evidence."

The agent uses the default configuration, here the simulator. It reads the
calibration instructions and the platform context, then runs one protocol at
a time. After each one, it inspects the report images and fit results, and
either accepts the fitted update or tries something else. It ends with a
summary of what it ran, what it changed and where the reports are.

.. warning::

   On real hardware, use limits approved for your device. The agent is
   instructed to respect them, but nothing enforces them on the instruments.
   Read :doc:`../guides/safety` before running on a real chip.

3. Check the outcome
--------------------

The reports are in the same place as in the interactive tutorial, and
``qibocal-agent report`` shows them. Do not take the summary on trust: open
the reports of the accepted updates and judge the fits yourself.
:doc:`../guides/artifacts` explains what to look for.

Accepted updates change the platform state used for the following
experiments, and are saved as snapshots. Your platform files are never
overwritten: copying a snapshot into them is up to you.

4. Use a named configuration
----------------------------

Pass a configuration name as the first argument:

.. code-block:: sh

   .venv/bin/qibocal-agent run platinum "YOUR BOUNDED OBJECTIVE"

Named configurations inherit nothing, not even the model, so store one for
each name you use with the agent:

.. code-block:: sh

   .venv/bin/qibocal-agent config set platinum.model openai:gpt-4.1

You can also save a reviewed objective, and then launch with the name alone:

.. code-block:: sh

   .venv/bin/qibocal-agent config set platinum.objective "YOUR BOUNDED OBJECTIVE"
   .venv/bin/qibocal-agent run platinum

Next steps
----------

* Multi-turn sessions from Python: :doc:`../agent/advanced`.
* All configuration settings: :doc:`../guides/configuration`.
