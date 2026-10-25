"""Custom equations: user-defined derived quantities via a safe AST
evaluator (ADR-0074).

Analysts need small derived numbers without a code change -- purity
from an internal standard, a marker-to-total ratio, a spec verdict
("purity >= 95").  This module evaluates user-supplied arithmetic
expressions by WALKING a parsed AST against a strict whitelist: no
``eval``/``exec``/``compile`` anywhere (the same prohibition
``orchestrator/app/sandbox.py`` enforces for agent code, applied here
to analyst-authored expressions).

Whitelist (anything else raises ``EquationError`` with the offending
construct named):

* numeric constants and declared variable names;
* arithmetic ``+ - * / // % **`` and unary ``+ - not``;
* comparisons ``< <= > >= == !=`` and ``and``/``or`` -- so an
  expression can BE a specification check;
* calls to ``abs min max round sqrt exp log log10 pow`` only.

Results carry their substituted values as audit text; spec checks
evaluate to pass/fail with warning/severe levels following the
``qc_monitor.QCAlert`` pattern.  Non-physical runtime failures
(division by zero, log of a negative) are named errors, never NaN.
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field

__all__ = [
    "EquationError",
    "CustomEquation",
    "EquationResult",
    "SpecCheck",
    "SpecCheckResult",
    "BUILTIN_EQUATIONS",
    "ALLOWED_FUNCTIONS",
    "evaluate_equation",
    "check_specs",
]

EQUATIONS_MODEL_ID = "equations-0.1.0"

#: The only callable names an expression may use.
ALLOWED_FUNCTIONS: dict[str, object] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "pow": pow,
}

_ALLOWED_BINOPS: dict[type[ast.operator], object] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
}

_ALLOWED_CMPOPS: dict[type[ast.cmpop], object] = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


class EquationError(Exception):
    """Named error: the expression is not evaluable under the whitelist."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class CustomEquation:
    name: str
    expression: str
    variables: dict[str, str] = field(default_factory=dict)  # name -> unit
    description: str = ""


@dataclass
class EquationResult:
    name: str
    expression: str
    value: float | bool
    substituted: str  # audit text: every variable with its value
    notes: list[str] = field(default_factory=list)
    model_id: str = EQUATIONS_MODEL_ID


@dataclass
class SpecCheck:
    """A specification: an expression that must evaluate to a boolean."""

    name: str
    expression: str
    level: str = "warning"  # "warning" | "severe", qc_monitor convention
    description: str = ""


@dataclass
class SpecCheckResult:
    name: str
    level: str
    passed: bool
    value: bool
    message: str
    model_id: str = EQUATIONS_MODEL_ID


# ---------------------------------------------------------------------
# the evaluator
# ---------------------------------------------------------------------

def _eval_node(node: ast.AST, env: dict[str, float]) -> object:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, env)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or isinstance(node.value, (int, float)):
            return node.value
        raise EquationError(
            f"constant {node.value!r} is not numeric; strings and other "
            "literals are not allowed"
        )
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise EquationError(
                f"variable {node.id!r} is not declared/supplied; known: "
                f"{sorted(env)}"
            )
        return env[node.id]
    if isinstance(node, ast.BinOp):
        op = _ALLOWED_BINOPS.get(type(node.op))
        if op is None:
            raise EquationError(
                f"operator {type(node.op).__name__} is not whitelisted"
            )
        left = _eval_node(node.left, env)
        right = _eval_node(node.right, env)
        try:
            return op(left, right)
        except ZeroDivisionError as exc:
            raise EquationError(f"division by zero: {exc}") from exc
        except (OverflowError, ValueError) as exc:
            raise EquationError(f"arithmetic failed: {exc}") from exc
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.Not):
            return not operand
        raise EquationError(f"unary operator {type(node.op).__name__} is not whitelisted")
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, env)
        outcome = True
        for op_node, comparator in zip(node.ops, node.comparators):
            op = _ALLOWED_CMPOPS.get(type(op_node))
            if op is None:
                raise EquationError(
                    f"comparison {type(op_node).__name__} is not whitelisted"
                )
            right = _eval_node(comparator, env)
            outcome = outcome and bool(op(left, right))
            left = right
        return outcome
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(v, env) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise EquationError(
                f"call target {ast.dump(node.func)[:40]}... is not a plain "
                "function name"
            )
        fname = node.func.id
        if fname not in ALLOWED_FUNCTIONS:
            raise EquationError(
                f"function {fname!r} is not whitelisted; allowed: "
                f"{sorted(ALLOWED_FUNCTIONS)}"
            )
        if node.keywords:
            raise EquationError("keyword arguments are not allowed in equations")
        args = [_eval_node(a, env) for a in node.args]
        try:
            return ALLOWED_FUNCTIONS[fname](*args)
        except (ValueError, OverflowError, ZeroDivisionError) as exc:
            raise EquationError(f"{fname}(...) failed: {exc}") from exc
    raise EquationError(
        f"construct {type(node).__name__} is not whitelisted for custom "
        "equations"
    )


def evaluate_equation(
    equation: CustomEquation | str, values: dict[str, float]
) -> EquationResult:
    """Evaluate one equation under the whitelist, with an audit trail."""
    if isinstance(equation, str):
        equation = CustomEquation(name="adhoc", expression=equation)
    notes: list[str] = []
    for key in values:
        if key.startswith("_"):
            raise EquationError(
                f"variable name {key!r} starts with an underscore; reserved"
            )
    declared = set(equation.variables) or set(values)
    missing = sorted(declared - set(values))
    if missing:
        raise EquationError(f"no value supplied for declared variable(s): {missing}")
    undeclared = sorted(set(values) - declared)
    if undeclared:
        raise EquationError(
            f"supplied variable(s) not declared by the equation: {undeclared}"
        )
    try:
        tree = ast.parse(equation.expression, mode="eval")
    except SyntaxError as exc:
        raise EquationError(f"expression does not parse: {exc}") from exc
    value = _eval_node(tree, dict(values))
    if not isinstance(value, (bool, int, float)):
        raise EquationError(f"expression yielded {type(value).__name__}, not a number")
    substituted = ", ".join(f"{k}={v:.6g}" for k, v in sorted(values.items()))
    substituted += f" -> {value:.6g}" if not isinstance(value, bool) else f" -> {value}"
    used = {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }
    unused = sorted(declared - used)
    if unused:
        notes.append(f"declared but unused in expression: {unused}")
    return EquationResult(
        name=equation.name,
        expression=equation.expression,
        value=value if isinstance(value, bool) else float(value),
        substituted=substituted,
        notes=notes,
    )


def check_specs(
    specs: list[SpecCheck], values: dict[str, float]
) -> list[SpecCheckResult]:
    """Evaluate specification expressions to pass/fail results.

    A spec expression must evaluate to a boolean (comparisons/and/or);
    anything numeric is a named error because a pass/fail decision
    cannot be guessed from a magnitude.
    """
    results: list[SpecCheckResult] = []
    for spec in specs:
        if spec.level not in ("warning", "severe"):
            raise EquationError(
                f"spec {spec.name!r} level must be 'warning' or 'severe', "
                f"got {spec.level!r}"
            )
        outcome = evaluate_equation(
            CustomEquation(name=spec.name, expression=spec.expression), values
        )
        if not isinstance(outcome.value, bool):
            raise EquationError(
                f"spec {spec.name!r} expression must evaluate to a boolean, "
                f"got {outcome.value!r}"
            )
        passed = outcome.value
        results.append(
            SpecCheckResult(
                name=spec.name,
                level=spec.level,
                passed=passed,
                value=outcome.value,
                message=(
                    f"{spec.name}: {spec.expression} is "
                    f"{'PASS' if passed else 'FAIL'} ({outcome.substituted})"
                ),
            )
        )
    return results


# ---------------------------------------------------------------------
# ready-made equations (thin, documented, over the same evaluator)
# ---------------------------------------------------------------------

BUILTIN_EQUATIONS: dict[str, CustomEquation] = {
    "analyte_nmol": CustomEquation(
        name="analyte_nmol",
        expression="(I_a / N_a) / (I_s / N_s) * n_s",
        variables={
            "I_a": "analyte integral (intensity*ppm)",
            "N_a": "analyte protons in the integral",
            "I_s": "internal-standard integral",
            "N_s": "internal-standard protons in the integral",
            "n_s": "internal-standard amount (nmol)",
        },
        description="Internal-standard qNMR amount, identical to "
        "quantify.qnmr_calculate but evaluable standalone.",
    ),
    "wt_percent": CustomEquation(
        name="wt_percent",
        expression="m_analyte / m_sample * 100",
        variables={
            "m_analyte": "analyte mass (mg)",
            "m_sample": "sample mass (mg)",
        },
        description="Weight percent of an analyte in a sample.",
    ),
    "marker_ratio": CustomEquation(
        name="marker_ratio",
        expression="I_marker / I_total",
        variables={
            "I_marker": "marker integral",
            "I_total": "total integral",
        },
        description="Marker-to-total integral ratio for extract fingerprinting.",
    ),
}
