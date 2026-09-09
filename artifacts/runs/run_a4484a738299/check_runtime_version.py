#!/usr/bin/env python3
"""Reproducible check that a claimed LIVE Orca runtime version rests on machine-readable evidence.

Regression guard for review finding R-001 on PR #34.

The defect this exists to prevent: a documented live-runtime version claim that could be
satisfied by non-evidence -- a help page, a truncated stream, an error envelope, or a status
document that simply does not carry a version. This checker accepts *only* a well-formed
`orca status --json` document that positively reports the expected running application version,
and it fails closed when it cannot obtain input at all.

"Cannot obtain input" is decided on the DOCUMENT, not on how a process exited: an empty or
whitespace-only document is BLOCKED from every source (`--input`, `--stdin`, `--run-command`),
including a `--run-command` child that exits nonzero while printing nothing. A child that exits
nonzero but does print something -- a help page being the canonical case -- has produced evidence,
just bad evidence, and is rejected as FAIL so the two outcomes stay distinguishable.

Why `status --json` and not `orca --version`, at pinned Orca commit
5ee4ace516080891731d100f843b074408a9ce0e (tag v1.4.197):

  * `orca --version` is a disk read of the shipped CLI build's own package metadata:
    src/cli/index.ts:63-73 takes the fast path only when `argv.length === 1`, and calls
    `readOrcaCliVersion()` (src/cli/cli-version.ts:5-13), which `readFileSync`s a sibling
    `package.json`. It never contacts the running application. Any extra argument falls
    through to the generic parser and prints help instead -- which is how a help page can be
    mistaken for version evidence.
  * `orca status --json` reports `result.runtime.appVersion` obtained over RPC from the
    running runtime (`status.get`, src/cli/runtime/status.ts:40) and spread into the response
    at src/cli/runtime/status.ts:60.

Two consequences from that same source drive the checks below:

  * `ok` is NOT a health signal. `buildCliStatusResponse` hardcodes `ok: true`
    (src/cli/runtime/status.ts:91-100), so even a not-running runtime answers `ok: true`.
  * `appVersion` is a CONDITIONAL spread (src/cli/runtime/status.ts:60), absent whenever the
    RPC did not return one -- e.g. the `not_running` / `stale_bootstrap` / `starting` branches
    (status.ts:19-37, 71-88), which also set `reachable: false`.

So `ok: true` alone proves nothing; the check additionally requires `result.runtime.reachable`
to be exactly `true` (set only on the successful RPC path, src/cli/runtime/status.ts:54) and
requires `appVersion` to be present, a string, and exactly equal to the expected version.

Usage:
    check_runtime_version.py --expected-version 1.4.197 --input <file>
    check_runtime_version.py --expected-version 1.4.197 --stdin
    check_runtime_version.py --expected-version 1.4.197 --run-command   # runs the real CLI

Exit codes (distinct on purpose):
    0  PASS     -- evidence obtained and it positively establishes the expected version
    1  FAIL     -- evidence obtained and REJECTED (it does not establish the version)
    2  BLOCKED  -- no evidence could be obtained at all (unreadable/absent source, or an empty
                   or whitespace-only document from any source); never treated as a pass

Diagnostic-output discipline (fail closed):

  * A `--run-command` child's STDERR IS NEVER REPRODUCED, whole or in part. `--orca-bin` can
    name any executable, so that stderr is arbitrary text and no finite denylist could make an
    excerpt of it safe. The BLOCKED message reports only facts *about* it -- empty or not, its
    length in bytes, and a truncated SHA-256 digest for run-to-run comparison.
  * The evaluated DOCUMENT is different: it is the evidence the caller submitted for judgement,
    so a rejected value is quoted back, bounded to DOCUMENT_VALUE_ECHO_LIMIT characters and
    collapsed to one line so it cannot forge additional check lines. That is a display bound,
    not a secrecy guarantee.

`--expected-version` is a required explicit input: this script hard-codes no version.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_BLOCKED = 2

DEFAULT_ORCA_BIN = "/usr/local/bin/orca"

# Upper bound on how much of the *evaluated document* may be echoed back in a check detail.
# This is a display bound, not a secrecy guarantee -- see _render_document_value.
DOCUMENT_VALUE_ECHO_LIMIT = 120


class Blocked(Exception):
    """Input could not be obtained at all -> fail closed, distinctly from a rejection."""


def _describe_stderr(text: str) -> str:
    """Describe a child process's stderr WITHOUT reproducing any of it.

    `--orca-bin` can name any executable, so its stderr is arbitrary, environment- or
    attacker-controlled text: it may carry credentials, hostnames, IP addresses, e-mail
    addresses or absolute home paths in any shape, on any platform. A finite denylist cannot
    be a guarantee against arbitrary content, and an excerpt that *looks* scrubbed while still
    carrying a secret is worse than no scrubbing at all, because it invites the reader to trust
    and forward it. So this returns only FACTS ABOUT the stderr, never the stderr:

      * whether it was empty,
      * its length in bytes,
      * a truncated SHA-256 digest, which lets two runs be compared for equality -- or matched
        against a stderr the operator captured themselves -- without disclosing any content.

    No byte of `text` reaches the returned string.
    """
    data = text.encode("utf-8", "surrogateescape")
    if not data:
        return "stderr was empty (0 bytes)"
    digest = hashlib.sha256(data).hexdigest()[:16]
    return (f"stderr was non-empty ({len(data)} bytes, sha256:{digest}); its content is "
            f"withheld by design and is not reproduced here")


def _render_document_value(value: object) -> str:
    """Render a value taken from the evaluated DOCUMENT for a check-detail line.

    Unlike stderr, the document is the evidence the caller explicitly asked to have judged, so
    reporting which value was rejected is the tool's purpose and cannot be withheld. It is still
    bounded and collapsed to one line so that a hostile document cannot emit unbounded output or
    forge additional `[PASS]`/`[FAIL]` check lines. That is a display bound, NOT a secrecy
    guarantee: whatever a caller submits as the document may be quoted back to that caller.
    """
    try:
        rendered = json.dumps(value)
    except (TypeError, ValueError):
        rendered = repr(value)
    rendered = " ".join(rendered.split())
    if len(rendered) > DOCUMENT_VALUE_ECHO_LIMIT:
        rendered = rendered[:DOCUMENT_VALUE_ECHO_LIMIT] + "...<truncated>"
    return rendered


def _looks_like_help_page(text: str) -> bool:
    """Heuristic used only to LABEL a rejection, never to decide one."""
    probe = text[:4000].lower()
    return any(m in probe for m in ("usage:", "usage\n", "commands:", "options:", "--help"))


def load_source(args: argparse.Namespace) -> tuple[str, str]:
    """Return (origin, raw_text). Raises Blocked when nothing can be read."""
    if args.run_command:
        cmd = [args.orca_bin, "status", "--json"]
        origin = " ".join(cmd)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise Blocked(f"could not execute {origin}: {exc}") from exc
        if proc.stdout.strip() == "":
            # Empty stdout is the ABSENCE of evidence, not bad evidence -- and that is true
            # whatever the exit status, so the classification is made on stdout alone.
            #   * Nonzero + empty stdout is the runtime-is-down case. Reporting it as "evidence
            #     rejected" would tell the caller the opposite of the truth.
            #   * Zero + empty stdout is decided the same way deliberately, not by accident: the
            #     contract is about whether a version DOCUMENT was obtained, not about how the
            #     process terminated, and an empty document establishes nothing either way. It
            #     also keeps this path consistent with --stdin and --input, which already treat
            #     an empty document as BLOCKED.
            # The child's stderr is NOT echoed, in any form. `--orca-bin` may run anything,
            # so its stderr is arbitrary text; only facts *about* it are reported.
            raise Blocked(
                f"{origin} exited with status {proc.returncode} and produced no stdout; "
                f"no version evidence was obtained; {_describe_stderr(proc.stderr)}"
            )
        # Non-empty stdout IS evidence, even from a nonzero exit -- a help page is the canonical
        # case. It is evaluated and rejected on its merits, which keeps FAIL (1) distinguishable
        # from BLOCKED (2).
        return origin, proc.stdout

    if args.stdin:
        origin = "<stdin>"
        try:
            raw = sys.stdin.read()
        except OSError as exc:
            raise Blocked(f"could not read stdin: {exc}") from exc
        if raw.strip() == "":
            raise Blocked("stdin was empty or whitespace-only; no evidence was supplied")
        return origin, raw

    origin = args.input
    try:
        with open(args.input, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise Blocked(f"could not read {origin}: {exc}") from exc
    if raw.strip() == "":
        raise Blocked(f"{origin} is empty; no evidence was supplied")
    return origin, raw


def evaluate(raw: str, expected: str) -> list[tuple[str, bool, str]]:
    """Run every check. Returns [(check_id, passed, detail)]; stops at the first blocker."""
    checks: list[tuple[str, bool, str]] = []

    try:
        doc = json.loads(raw)
    except (ValueError, TypeError) as exc:
        label = "response is a help page / plain text, not JSON" if _looks_like_help_page(raw) \
            else "response is not parseable JSON (malformed or truncated)"
        checks.append(("json_parses", False, f"{label}: {exc}"))
        return checks
    checks.append(("json_parses", True, "input parsed as JSON"))

    if not isinstance(doc, dict):
        checks.append(("json_is_object", False, f"top level is {type(doc).__name__}, not an object"))
        return checks
    checks.append(("json_is_object", True, "top level is a JSON object"))

    ok = doc.get("ok")
    if ok is not True:
        rendered = "absent" if "ok" not in doc else _render_document_value(ok)
        checks.append(("ok_is_true", False, f"`ok` is {rendered}, expected boolean true"))
        return checks
    checks.append(("ok_is_true", True, "`ok` is boolean true"))

    result = doc.get("result")
    if not isinstance(result, dict):
        checks.append(("result_is_object", False, "`result` is missing or not an object"))
        return checks
    checks.append(("result_is_object", True, "`result` is an object"))

    runtime = result.get("runtime")
    if not isinstance(runtime, dict):
        checks.append(("runtime_is_object", False, "`result.runtime` is missing or not an object"))
        return checks
    checks.append(("runtime_is_object", True, "`result.runtime` is an object"))

    # `ok` is hardcoded true upstream, so reachability is what proves the value came from the
    # live runtime over RPC rather than from an offline fallback envelope.
    reachable = runtime.get("reachable")
    if reachable is not True:
        rendered = "absent" if "reachable" not in runtime else _render_document_value(reachable)
        checks.append(("runtime_reachable", False,
                       f"`result.runtime.reachable` is {rendered}; the runtime was not reached, "
                       f"so no live version was observed"))
        return checks
    checks.append(("runtime_reachable", True, "`result.runtime.reachable` is boolean true"))

    if "appVersion" not in runtime:
        checks.append(("app_version_present", False,
                       "`result.runtime.appVersion` is ABSENT; the status document carries no "
                       "live version and must not be read as version evidence"))
        return checks
    observed = runtime["appVersion"]
    if not isinstance(observed, str) or observed == "":
        checks.append(("app_version_present", False,
                       f"`result.runtime.appVersion` is {_render_document_value(observed)}, "
                       f"expected a non-empty string"))
        return checks
    checks.append(("app_version_present", True,
                   f"`result.runtime.appVersion` is present: "
                   f"{_render_document_value(observed)}"))

    if observed != expected:
        checks.append(("app_version_matches", False,
                       f"`result.runtime.appVersion` is {_render_document_value(observed)}, "
                       f"expected {_render_document_value(expected)}"))
        return checks
    checks.append(("app_version_matches", True,
                   f"`result.runtime.appVersion` == {_render_document_value(expected)}"))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a live Orca runtime version from machine-readable `orca status --json`."
    )
    parser.add_argument("--expected-version", required=True,
                        help="The version the live runtime must report. Required; never defaulted.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Path to a captured `orca status --json` document.")
    source.add_argument("--stdin", action="store_true", help="Read the document from stdin.")
    source.add_argument("--run-command", action="store_true",
                        help="Run `<orca-bin> status --json` and check its output.")
    parser.add_argument("--orca-bin", default=DEFAULT_ORCA_BIN,
                        help=f"orca executable for --run-command (default: {DEFAULT_ORCA_BIN}).")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="Timeout in seconds for --run-command (default: 30).")
    args = parser.parse_args(argv)

    if not args.expected_version.strip():
        print("BLOCKED: --expected-version was empty", file=sys.stderr)
        return EXIT_BLOCKED

    try:
        origin, raw = load_source(args)
    except Blocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        print("BLOCKED is not a pass: no version evidence was obtained.", file=sys.stderr)
        return EXIT_BLOCKED

    checks = evaluate(raw, args.expected_version.strip())
    print(f"source: {origin}")
    print(f"expected live appVersion: {args.expected_version.strip()!r}")
    for check_id, passed, detail in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {check_id}: {detail}")

    if all(passed for _, passed, _ in checks) and len(checks) == 8:
        print("RESULT: PASS -- live runtime version established from machine-readable status JSON.")
        return EXIT_PASS
    print("RESULT: FAIL -- input rejected; it does not establish the live runtime version.")
    return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
