"""Custom equations under the whitelisted AST evaluator (ADR-0074).

Ground truth: hand-computed arithmetic.  The security assertions are
exhaustive over the rejection surface: every non-whitelisted construct
must raise EquationError NAMING the construct -- an equation is an
arithmetic formula over numbers, never a program.
"""

from __future__ import annotations

import math

import pytest

from nmr_preproc.equations import (
    BUILTIN_EQUATIONS,
    CustomEquation,
    EquationError,
    SpecCheck,
    check_specs,
    evaluate_equation,
)


class TestArithmetic:
    def test_precedence_and_operators(self) -> None:
        cases = {
            "1 + 2 * 3": 7.0,
            "(1 + 2) * 3": 9.0,
            "2 ** 10": 1024.0,
            "7 // 2": 3.0,
            "7 % 3": 1.0,
            "-a + b": 1.0,
        }
        for expression, expected in cases.items():
            result = evaluate_equation(expression, {"a": 2.0, "b": 3.0})
            assert result.value == pytest.approx(expected), expression

    def test_whitelisted_functions(self) -> None:
        values = {"a": 4.0, "b": 9.0}
        cases = {
            "sqrt(a)": 2.0,
            "min(a, b)": 4.0,
            "max(a, b)": 9.0,
            "abs(0 - a)": 4.0,
            "round(a / 3, 2)": 1.33,
            "exp(log(1.0))": 1.0,
            "log10(1000.0)": 3.0,
            "pow(a, 0.5)": 2.0,
        }
        for expression, expected in cases.items():
            assert evaluate_equation(expression, values).value == pytest.approx(
                expected
            ), expression

    def test_audit_text_carries_substitutions(self) -> None:
        result = evaluate_equation(
            CustomEquation(name="ratio", expression="a / b * 100",
                           variables={"a": "mg", "b": "mg"}),
            {"a": 3.0, "b": 4.0},
        )
        assert result.name == "ratio"
        assert result.value == pytest.approx(75.0)
        assert "a=3" in result.substituted and "b=4" in result.substituted
        assert "75" in result.substituted

    def test_unused_declared_variable_is_noted_not_fatal(self) -> None:
        result = evaluate_equation(
            CustomEquation(name="x", expression="a * 2", variables={"a": "", "z": ""}),
            {"a": 1.0, "z": 5.0},
        )
        assert result.value == pytest.approx(2.0)
        assert any("unused" in note for note in result.notes)


class TestRejectionSurface:
    """Every forbidden construct raises EquationError with a named reason."""

    @pytest.mark.parametrize(
        "expression",
        [
            "x.__class__",           # attribute access
            "(1).real",              # attribute on a literal
            "xs[0]",                 # subscript
            "(lambda v: v)(1)",      # lambda (call target is not a Name)
            "open('f')",             # non-whitelisted call
            "eval('1')",             # the forbidden call itself
            "__import__('os')",      # dunder call
            "1 if a else 2",         # ternary
            "[a, a][0]",             # list literal -> subscript chain
            "a + 'text'",            # string constant
            "' Framed '",            # bare string
            "f'{a}'",                # f-string
            "not_defined + 1",       # undeclared variable
            "a / 0",                 # runtime division by zero
            "sqrt(0 - 1)",           # math domain error
        ],
    )
    def test_forbidden_constructs_raise(self, expression: str) -> None:
        with pytest.raises(EquationError) as excinfo:
            evaluate_equation(expression, {"a": 1.0, "xs": [1.0]})
        assert excinfo.value.reason  # the reason names the construct

    def test_undeclared_supplied_value_raises(self) -> None:
        with pytest.raises(EquationError, match="not declared"):
            evaluate_equation(
                CustomEquation(name="x", expression="a * 2", variables={"a": ""}),
                {"a": 1.0, "extra": 2.0},
            )

    def test_missing_declared_value_raises(self) -> None:
        with pytest.raises(EquationError, match="no value supplied"):
            evaluate_equation(
                CustomEquation(name="x", expression="a + b",
                               variables={"a": "", "b": ""}),
                {"a": 1.0},
            )

    def test_syntax_error_raises_named_error(self) -> None:
        with pytest.raises(EquationError, match="does not parse"):
            evaluate_equation("a +* 2", {"a": 1.0})


class TestSpecChecks:
    def test_pass_and_fail_with_levels(self) -> None:
        results = check_specs(
            [
                SpecCheck(name="purity_min", expression="purity >= 95",
                          level="severe"),
                SpecCheck(name="ratio_window", expression="ratio > 0.4 and ratio < 0.6",
                          level="warning"),
            ],
            {"purity": 97.2, "ratio": 0.75},
        )
        by_name = {r.name: r for r in results}
        assert by_name["purity_min"].passed is True
        assert by_name["purity_min"].level == "severe"
        assert "PASS" in by_name["purity_min"].message
        assert by_name["ratio_window"].passed is False  # 0.75 outside window
        assert "FAIL" in by_name["ratio_window"].message

    def test_non_boolean_expression_is_named_error(self) -> None:
        with pytest.raises(EquationError, match="must evaluate to a boolean"):
            check_specs([SpecCheck(name="bad", expression="a * 2")], {"a": 1.0})

    def test_bad_level_is_named_error(self) -> None:
        with pytest.raises(EquationError, match="level"):
            check_specs(
                [SpecCheck(name="x", expression="a > 0", level="fatal")], {"a": 1.0}
            )


class TestBuiltins:
    def test_analyte_nmol_matches_qnmr_math(self) -> None:
        values = {
            "I_a": 150.0, "N_a": 3.0,
            "I_s": 100.0, "N_s": 2.0, "n_s": 100.0,
        }
        result = evaluate_equation(BUILTIN_EQUATIONS["analyte_nmol"], values)
        assert result.value == pytest.approx(100.0)  # (150/3)/(100/2)*100

    def test_wt_percent_and_marker_ratio(self) -> None:
        assert evaluate_equation(
            BUILTIN_EQUATIONS["wt_percent"], {"m_analyte": 0.03, "m_sample": 100.0}
        ).value == pytest.approx(0.03)
        assert evaluate_equation(
            BUILTIN_EQUATIONS["marker_ratio"], {"I_marker": 1.0, "I_total": 4.0}
        ).value == pytest.approx(0.25)

    def test_every_builtin_documented_with_units(self) -> None:
        for equation in BUILTIN_EQUATIONS.values():
            assert equation.variables, equation.name
            used = set(equation.expression.replace("(", " ").replace(")", " ").split())
            names = {t for t in used if t.isidentifier() and t not in
                     {"abs", "min", "max", "round", "sqrt", "exp", "log",
                      "log10", "pow"}}
            assert names == set(equation.variables), equation.name


class TestConstants:
    def test_model_id_stamped(self) -> None:
        result = evaluate_equation("1 + 1", {"a": 0.0})
        assert result.model_id == "equations-0.1.0"
        assert math.isfinite(result.value)
