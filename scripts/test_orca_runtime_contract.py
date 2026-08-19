#!/usr/bin/env python3
"""Offline regression tests for the pinned Orca runtime contract adapter."""

from __future__ import annotations

import subprocess
import unittest
from os import environ
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from scripts.orca_fake_agent import send_done
from scripts.orca_runtime_harness import (
    REQUIRED_ORCA_CLI_GUIDE_SNIPPETS,
    REQUIRED_ORCHESTRATION_GUIDE_SNIPPETS,
    SUPPORTED_ORCA_APP_VERSION,
    OrcaRuntimeHarness,
    UnsupportedOrcaContract,
    validate_orca_contract,
)


class OrcaRuntimeContractTests(unittest.TestCase):
    def test_pinned_version_and_guide_contract_pass(self) -> None:
        validate_orca_contract(
            SUPPORTED_ORCA_APP_VERSION,
            "\n".join(REQUIRED_ORCHESTRATION_GUIDE_SNIPPETS),
            "\n".join(REQUIRED_ORCA_CLI_GUIDE_SNIPPETS),
        )

    def test_different_orca_version_is_blocked(self) -> None:
        with self.assertRaisesRegex(UnsupportedOrcaContract, "installed runtime"):
            validate_orca_contract("1.4.185", "", "")

    def test_guide_grammar_drift_is_blocked(self) -> None:
        with self.assertRaisesRegex(UnsupportedOrcaContract, "pinned grammar"):
            validate_orca_contract(SUPPORTED_ORCA_APP_VERSION, "", "")

    @patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"})
    def test_environment_override_resolves_non_default_executable(self) -> None:
        self.assertEqual(OrcaRuntimeHarness._resolve_orca(), "/opt/orca-dev")

    @patch("scripts.orca_fake_agent.subprocess.run")
    def test_worker_done_uses_resolved_orca_executable(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}\n", stderr=""
        )

        with redirect_stdout(StringIO()):
            send_done(
                "task_example",
                "ctx_example",
                None,
                "succeeded",
                "done",
                "/opt/orca-dev",
            )

        command = run.call_args.args[0]
        self.assertEqual(command[0], "/opt/orca-dev")
        self.assertEqual(command.count("worker_done"), 1)


if __name__ == "__main__":
    unittest.main()
