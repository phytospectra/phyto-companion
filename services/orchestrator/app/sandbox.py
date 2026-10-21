"""Code-as-actions sandbox for agent-written Python.

The real boundary is a Docker container (no network, capped memory and
CPU, timeout).  On the local plane -- or wherever Docker is unavailable
-- the sandbox REFUSES to execute and offers validation-only mode
(``dry_run=True``): syntax and static guards without running anything.
Static guards are defense-in-depth, never the security boundary.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from phyto_shared.exceptions import SandboxError
from phyto_shared.provenance import sha256_bytes

__all__ = ["SandboxResult", "CodeSandbox"]

_FORBIDDEN_MODULES = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "shutil",
        "socket",
        "pathlib",
        "requests",
        "urllib",
        "http",
        "ctypes",
        "multiprocessing",
    }
)
_FORBIDDEN_CALLS = frozenset({"eval", "exec", "open", "__import__", "compile"})


@dataclass(frozen=True)
class SandboxResult:
    """Outcome of one sandbox submission."""

    code_hash: str
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    executed: bool
    notes: str = ""


class CodeSandbox:
    """Executes agent-written Python inside a locked-down container.

    ``docker_binary`` is injectable so tests can simulate a machine
    without Docker (keeping the suite hermetic -- no daemon needed).
    """

    def __init__(
        self,
        docker_image: str = "python:3.12-slim",
        timeout_seconds: float = 15.0,
        memory_mb: int = 256,
        cpu_limit: float = 1.0,
        docker_binary: str = "docker",
    ) -> None:
        self.docker_image = docker_image
        self.timeout_seconds = timeout_seconds
        self.memory_mb = memory_mb
        self.cpu_limit = cpu_limit
        self.docker_binary = docker_binary

    # -- static analysis ----------------------------------------------------

    def _static_guards(self, code: str) -> None:
        """Syntax check + forbidden-import/call scan (never executes code)."""
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise SandboxError(
                "Submitted code has a syntax error",
                context={"line": exc.lineno, "message": exc.msg},
            ) from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            else:
                names = []
            for name in names:
                if name in _FORBIDDEN_MODULES:
                    raise SandboxError(
                        f"Import of module '{name}' is forbidden in the sandbox",
                        context={"module": name},
                    )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _FORBIDDEN_CALLS
            ):
                raise SandboxError(
                    f"Call to '{node.func.id}' is forbidden in the sandbox",
                    context={"call": node.func.id},
                )

    # -- execution ----------------------------------------------------------

    def run(self, code: str, *, dry_run: bool = False) -> SandboxResult:
        """Validate (always) and optionally execute the submitted code.

        ``dry_run=True`` validates syntax and guards and returns without
        executing anything -- the local-plane default.
        """
        self._static_guards(code)
        code_hash = sha256_bytes(code.encode("utf-8"))

        if dry_run:
            return SandboxResult(
                code_hash=code_hash,
                stdout="",
                stderr="",
                exit_code=0,
                duration_seconds=0.0,
                executed=False,
                notes="validated (syntax + static guards); not executed (--dry-run)",
            )

        docker_path = shutil.which(self.docker_binary)
        if docker_path is None:
            raise SandboxError(
                "Docker is unavailable: sandboxed execution refused. Use "
                "dry_run=True for validation-only, or run in the cloud plane.",
                context={"docker_binary": self.docker_binary, "code_hash": code_hash},
            )

        command = [
            docker_path,
            "run",
            "--rm",
            "--network", "none",
            "--memory", f"{self.memory_mb}m",
            "--cpus", str(self.cpu_limit),
            "--interactive",
            self.docker_image,
            "python", "-",
        ]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                input=code.encode("utf-8"),
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise SandboxError(
                "Sandboxed execution exceeded its time limit",
                context={
                    "timeout_seconds": self.timeout_seconds,
                    "code_hash": code_hash,
                },
            ) from None
        duration = time.monotonic() - started
        return SandboxResult(
            code_hash=code_hash,
            stdout=completed.stdout.decode("utf-8", errors="replace"),
            stderr=completed.stderr.decode("utf-8", errors="replace"),
            exit_code=completed.returncode,
            duration_seconds=round(duration, 3),
            executed=True,
        )
