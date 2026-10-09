"""Check launch commands and SSH quoting without opening a network connection."""

import argparse
import os
import shlex
import sys
import unittest
from unittest.mock import patch

from qibocal_mcp.agent import transport_for


class TransportTests(unittest.TestCase):
    def options(self, **changes):
        options = dict(
            server_python=None, ssh=None, platform="qpu", output="runs",
            platforms=None, partition=None, workdir=None, slurm_arg=[],
        )
        options.update(changes)
        return argparse.Namespace(**options)

    def test_direct_compute_node(self):
        transport = transport_for(self.options())
        self.assertEqual(transport.command, sys.executable)
        self.assertIn("--no-config", transport.args)
        self.assertNotIn("--partition", transport.args)
        self.assertEqual(transport.env, dict(os.environ))
        self.assertFalse(transport.keep_alive)

    def test_access_node_dispatch(self):
        transport = transport_for(self.options(partition="qpu"))
        self.assertIn("--partition", transport.args)
        self.assertNotIn("ssh", transport.command)

    def test_ssh_to_gateway_quotes_remote_paths(self):
        directory = "/shared/calibration project"
        python = directory + "/.venv/bin/python"
        with patch.dict(os.environ, {"SSH_AUTH_SOCK": "/tmp/test-ssh-agent"}):
            transport = transport_for(
                self.options(
                    ssh="access-node", partition="qpu", workdir=directory,
                    server_python=python, platforms="/shared/platform definitions",
                    slurm_arg=["--account=quantum"],
                )
            )
        self.assertEqual(transport.command, "ssh")
        self.assertEqual(
            transport.args[:5],
            ["-T", "-o", "BatchMode=yes", "--", "access-node"],
        )
        tokens = shlex.split(transport.args[-1])
        self.assertEqual(tokens[:4], ["cd", directory, "&&", "exec"])
        self.assertEqual(tokens[4], python)
        self.assertIn("--partition", tokens)
        self.assertIn("/shared/platform definitions", tokens)
        self.assertIn("--slurm-arg=--account=quantum", tokens)
        self.assertEqual(transport.env, {"SSH_AUTH_SOCK": "/tmp/test-ssh-agent"})
        self.assertFalse(transport.keep_alive)

    def test_workdir_is_not_resolved_twice(self):
        transport = transport_for(
            self.options(partition="qpu", workdir="relative/project")
        )
        self.assertEqual(transport.cwd, "relative/project")
        self.assertNotIn("--workdir", transport.args)
