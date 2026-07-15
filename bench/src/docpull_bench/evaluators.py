"""Optional Pydantic Evals bridge around the canonical lane scorers."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from .models import BenchmarkCase, BenchmarkInput, RunObservation
from .scoring import score_observation


@dataclass
class EvaluationMetadata:
    """Gold case metadata retained by the orchestrator and never sent to adapters."""

    case: BenchmarkCase


@dataclass
class DeterministicEvaluator(Evaluator[BenchmarkInput, RunObservation, EvaluationMetadata]):
    """Expose the canonical score to optional Pydantic Evals consumers."""

    def evaluate(
        self,
        ctx: EvaluatorContext[BenchmarkInput, RunObservation, EvaluationMetadata],
    ) -> dict[str, bool | int | float]:
        metadata = ctx.metadata
        if metadata is None:
            raise ValueError("benchmark case is missing evaluator metadata")
        score = score_observation(metadata.case, ctx.output)
        return {
            "passed": score.passed,
            "completed": score.completed,
            "required_check_rate": score.required_check_rate,
            "assertion_count": len(score.assertions),
        }

    def get_evaluator_version(self) -> str:
        return "2"
