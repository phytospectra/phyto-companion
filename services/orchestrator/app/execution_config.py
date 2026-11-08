"""Validated executor selection, kept separate from authentication dry-run."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from phyto_shared.exceptions import InvalidInputError

from .planner_agent import ToolExecutor
from .tool_registry import MockToolExecutor, ToolRegistry

SCIENTIFIC_MODE_VARIABLE = "PHYTO_SCIENTIFIC_EXECUTION"
_REFERENCE_WORKFLOW = "reference_hsqc_identify"


@dataclass(frozen=True)
class ScientificExecutionConfig:
    mode: Literal["mock", "real"]
    environment: Literal["local", "cloud"]
    structure_library: Path | None = None
    reference_library: Path | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ScientificExecutionConfig":
        env = os.environ if environ is None else environ
        environment = "cloud" if env.get("PHYTO_ENV") == "cloud" else "local"
        raw_mode = env.get(SCIENTIFIC_MODE_VARIABLE)
        mode = raw_mode
        if mode not in ("mock", "real"):
            raise InvalidInputError(
                "Select scientific execution explicitly with "
                f"{SCIENTIFIC_MODE_VARIABLE}=mock or real",
                context={"variable": SCIENTIFIC_MODE_VARIABLE, "value": raw_mode},
            )
        if mode == "mock" and environment == "cloud" and env.get("PHYTO_DRY_RUN") != "1":
            raise InvalidInputError(
                "Explicit offline mock science also requires PHYTO_DRY_RUN=1; "
                "a real cloud environment cannot silently select it",
                context={"variable": SCIENTIFIC_MODE_VARIABLE},
            )
        if mode == "mock":
            return cls(mode=mode, environment=environment)
        paths: dict[str, Path] = {}
        for attribute, variable in (
            ("structure_library", "PHYTO_STRUCTURE_DB_PATH"),
            ("reference_library", "PHYTO_HSQC_REFERENCE_LIBRARY"),
        ):
            value = env.get(variable)
            if not value:
                raise InvalidInputError(
                    f"Real reference HSQC execution requires {variable}",
                    context={"variable": variable, "workflow": _REFERENCE_WORKFLOW},
                )
            path = Path(value).expanduser()
            if not path.is_file():
                raise InvalidInputError(
                    f"Configured scientific library is not a readable file: {variable}",
                    context={"variable": variable},
                )
            paths[attribute] = path
        return cls(mode="real", environment=environment, **paths)


def build_tool_executor(
    registry: ToolRegistry,
    config: ScientificExecutionConfig,
) -> ToolExecutor:
    """Construct only the selected executor; real failures are never mock fallback."""

    if config.mode == "mock":
        return MockToolExecutor(registry)
    from .real_executor import RealToolExecutor

    return RealToolExecutor(
        registry=registry,
        structure_path=config.structure_library,
        reference_path=config.reference_library,
    )


__all__ = [
    "ScientificExecutionConfig",
    "SCIENTIFIC_MODE_VARIABLE",
    "build_tool_executor",
]
