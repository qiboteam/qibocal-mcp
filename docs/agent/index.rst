Autocalibration agent
=====================

Use ``qibocal-agent`` to pursue a bounded calibration objective with a
vision- and tool-capable model. The agent discovers platform context, chooses
protocols, inspects their reports and explicitly accepts or rejects fitted
updates. It owns its MCP subprocess; do not launch a server separately.

Start with the minimal local setup in :doc:`../installation`, then
:doc:`../getting-started`. The tutorials cover local calibration and remote
execution. Alternative providers, persistent settings and deployment
customization are optional; see :doc:`../guides/index` when needed.

.. toctree::
   :maxdepth: 2

   ../installation
   ../getting-started
   ../tutorials/index
