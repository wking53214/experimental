"""
Metrics collection and effectiveness measurement.

Phase 2 Sprint 2: Replace categorical validation with data-driven effectiveness
measurement. Track key metrics before/after adaptation and determine whether
the adaptation actually improved the system.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Any
import time


class MetricType(Enum):
    """Type of metric being tracked."""
    VIOLATION_RATE = "violation_rate"
    SLO_ATTAINMENT = "slo_attainment"
    THROUGHPUT = "throughput"
    LATENCY_P99 = "latency_p99"
    ERROR_RATE = "error_rate"
    CUSTOM = "custom"


class EffectivenessOutcome(Enum):
    """Result of post-adaptation effectiveness measurement."""
    IMPROVED = "improved"
    UNCHANGED = "unchanged"
    DEGRADED = "degraded"
    INCONCLUSIVE = "inconclusive"


@dataclass
class MetricSnapshot:
    """
    Snapshot of system metrics at a point in time.

    Captures all relevant metrics for effectiveness measurement.
    """
    timestamp: float
    boundary_id: str
    metrics: dict[str, float] = field(default_factory=dict)
    notes: dict = field(default_factory=dict)

    def get_metric(self, metric_name: str) -> Optional[float]:
        """Retrieve a specific metric value."""
        return self.metrics.get(metric_name)

    def set_metric(self, metric_name: str, value: float) -> None:
        """Set a metric value."""
        self.metrics[metric_name] = value


@dataclass
class AdaptationMetrics:
    """
    Before/after metrics for an adaptation.

    Enables comparison to determine effectiveness.
    """
    proposal_id: str
    boundary_id: str
    snapshot_pre: MetricSnapshot
    snapshot_post: MetricSnapshot
    observation_duration_seconds: float
    effectiveness: EffectivenessOutcome = EffectivenessOutcome.INCONCLUSIVE
    confidence: float = 0.0
    reasoning: str = ""
    notes: dict = field(default_factory=dict)


class MetricsCollector:
    """
    Collects pre/post adaptation metrics.

    Takes snapshots before adaptation and again after observation window.
    """

    def __init__(self):
        self.snapshots: dict[str, MetricSnapshot] = {}
        self.adaptation_metrics: dict[str, AdaptationMetrics] = {}

    def record_pre_adaptation_snapshot(
        self,
        boundary_id: str,
        metrics: dict[str, float],
        notes: Optional[dict] = None,
    ) -> MetricSnapshot:
        """
        Record metrics before adaptation is applied.

        Called when pattern is detected but before boundary is updated.
        """
        snapshot = MetricSnapshot(
            timestamp=time.time(),
            boundary_id=boundary_id,
            metrics=metrics,
            notes=notes or {},
        )

        # Store with prefix for retrieval
        key = f"pre_{boundary_id}_{int(time.time() * 1000)}"
        self.snapshots[key] = snapshot

        return snapshot

    def record_post_adaptation_snapshot(
        self,
        proposal_id: str,
        boundary_id: str,
        metrics: dict[str, float],
        notes: Optional[dict] = None,
        observation_duration_seconds: float = 300,
    ) -> AdaptationMetrics:
        """
        Record metrics after adaptation and observation period.

        Called after waiting for observation_duration to see effects of adaptation.
        """
        snapshot_post = MetricSnapshot(
            timestamp=time.time(),
            boundary_id=boundary_id,
            metrics=metrics,
            notes=notes or {},
        )

        # Get corresponding pre-adaptation snapshot
        # In real system, would look up by proposal_id
        pre_snapshots = [
            s for k, s in self.snapshots.items()
            if k.startswith(f"pre_{boundary_id}") and s.timestamp > time.time() - 3600
        ]

        if not pre_snapshots:
            # No pre-adaptation snapshot found, create dummy
            snapshot_pre = MetricSnapshot(
                timestamp=time.time() - observation_duration_seconds,
                boundary_id=boundary_id,
                metrics={},
                notes={"issue": "no_pre_snapshot"},
            )
        else:
            # Use most recent pre-adaptation snapshot
            snapshot_pre = max(pre_snapshots, key=lambda s: s.timestamp)

        # Create adaptation metrics record
        adaptation_metrics = AdaptationMetrics(
            proposal_id=proposal_id,
            boundary_id=boundary_id,
            snapshot_pre=snapshot_pre,
            snapshot_post=snapshot_post,
            observation_duration_seconds=observation_duration_seconds,
        )

        self.adaptation_metrics[proposal_id] = adaptation_metrics

        return adaptation_metrics


class EffectivenessOracle:
    """
    Evaluates whether adaptations were effective.

    Uses data-driven comparison of pre/post metrics to determine if
    adaptation improved the system.

    GOODHART-RESISTANT: Detects when improving one metric destroys another.
    """

    def __init__(self):
        # Define which metrics matter and their improvement direction
        self.metric_importance = {
            "violation_rate": {"direction": "lower", "weight": 1.0},
            "slo_attainment": {"direction": "higher", "weight": 1.0},
            "throughput": {"direction": "higher", "weight": 1.0},  # RAISED: equal importance
            "latency_p99": {"direction": "lower", "weight": 0.5},
            "error_rate": {"direction": "lower", "weight": 0.8},
        }

        # Thresholds for determining improvement
        self.improvement_threshold = 0.10  # 10% improvement counts as IMPROVED
        self.degradation_threshold = -0.10  # -10% counts as DEGRADED

        # GOODHART DETECTION: Safety guardrails
        self.max_throughput_drop = -0.20  # Throughput cannot drop >20% even if violations improve
        self.goodhart_collapse_ratio = 0.5  # If throughput drops >50% while violations improve, flag

    def evaluate_adaptation(
        self,
        adaptation_metrics: AdaptationMetrics,
    ) -> tuple[EffectivenessOutcome, float, str]:
        """
        Evaluate whether adaptation was effective.

        Returns: (outcome, confidence, reasoning)
        """
        pre_metrics = adaptation_metrics.snapshot_pre.metrics
        post_metrics = adaptation_metrics.snapshot_post.metrics

        if not pre_metrics or not post_metrics:
            return (
                EffectivenessOutcome.INCONCLUSIVE,
                0.0,
                "Insufficient metrics for evaluation"
            )

        # Calculate changes for each metric
        metric_changes = {}
        evaluated_metrics = []

        for metric_name, config in self.metric_importance.items():
            if metric_name not in pre_metrics or metric_name not in post_metrics:
                continue

            pre_val = pre_metrics[metric_name]
            post_val = post_metrics[metric_name]

            if pre_val == 0:
                # Avoid division by zero
                if post_val == 0:
                    change = 0.0
                else:
                    change = 1.0 if post_val > 0 else -1.0
            else:
                change = (post_val - pre_val) / abs(pre_val)

            # Apply direction (higher is better or lower is better)
            if config["direction"] == "lower":
                change = -change

            metric_changes[metric_name] = {
                "change": change,
                "weight": config["weight"],
                "pre": pre_val,
                "post": post_val,
            }
            evaluated_metrics.append(metric_name)

        if not evaluated_metrics:
            return (
                EffectivenessOutcome.INCONCLUSIVE,
                0.0,
                "No overlapping metrics between pre and post"
            )

        # GOODHART DETECTION: Check for anti-patterns
        goodhart_pattern = self._detect_goodhart_pattern(metric_changes)
        if goodhart_pattern:
            return (
                EffectivenessOutcome.DEGRADED,
                0.95,
                goodhart_pattern["reasoning"]
            )

        # Calculate weighted average change
        total_weight = sum(m["weight"] for m in metric_changes.values())
        weighted_change = sum(
            m["change"] * m["weight"] for m in metric_changes.values()
        ) / total_weight if total_weight > 0 else 0.0

        # Determine outcome
        if weighted_change >= self.improvement_threshold:
            outcome = EffectivenessOutcome.IMPROVED
            confidence = min(1.0, abs(weighted_change) / self.improvement_threshold)
        elif weighted_change <= self.degradation_threshold:
            outcome = EffectivenessOutcome.DEGRADED
            confidence = min(1.0, abs(weighted_change) / abs(self.degradation_threshold))
        else:
            outcome = EffectivenessOutcome.UNCHANGED
            confidence = 1.0 - abs(weighted_change) / self.improvement_threshold if self.improvement_threshold > 0 else 0.5

        # Generate reasoning
        reasoning = self._generate_reasoning(metric_changes, weighted_change, outcome)

        return outcome, confidence, reasoning

    def _detect_goodhart_pattern(self, metric_changes: dict) -> Optional[dict]:
        """
        Detect Goodhart attacks: improving one metric while destroying another.

        Patterns:
        1. violation_rate improves BUT throughput collapses
        2. slo_attainment improves BUT throughput collapses
        3. efficiency metric (throughput/violations) degrades

        Returns: {"reasoning": "..."} if pattern detected, None otherwise
        """
        if "violation_rate" not in metric_changes or "throughput" not in metric_changes:
            return None

        violation_change = metric_changes["violation_rate"]["change"]
        throughput_change = metric_changes["throughput"]["change"]

        # GOODHART PATTERN 1: Violations improve while throughput collapses
        if violation_change > self.improvement_threshold and throughput_change < self.max_throughput_drop:
            return {
                "reasoning": (
                    f"Goodhart pattern detected: violation_rate improved {violation_change:+.1%} "
                    f"but throughput collapsed {throughput_change:+.1%} (>20% drop). "
                    f"System optimized for wrong metric."
                )
            }

        # GOODHART PATTERN 2: Throughput dropped more than 50% (catastrophic)
        if throughput_change < -self.goodhart_collapse_ratio:
            return {
                "reasoning": (
                    f"Catastrophic throughput collapse: {throughput_change:+.1%}. "
                    f"Adaptation severely degraded system capacity."
                )
            }

        # GOODHART PATTERN 3: SLO improves but efficiency dies
        if "slo_attainment" in metric_changes:
            slo_change = metric_changes["slo_attainment"]["change"]
            if slo_change > self.improvement_threshold and throughput_change < self.degradation_threshold:
                return {
                    "reasoning": (
                        f"Efficiency trap: slo_attainment improved {slo_change:+.1%} "
                        f"but throughput degraded {throughput_change:+.1%}. "
                        f"System trading performance for nominal compliance."
                    )
                }

        return None

    def _generate_reasoning(
        self,
        metric_changes: dict,
        weighted_change: float,
        outcome: EffectivenessOutcome,
    ) -> str:
        """Generate human-readable explanation of effectiveness."""
        improved = []
        degraded = []
        unchanged = []

        for metric_name, changes in metric_changes.items():
            change = changes["change"]
            if change > self.improvement_threshold:
                improved.append(
                    f"{metric_name}: {change:+.1%}"
                )
            elif change < self.degradation_threshold:
                degraded.append(
                    f"{metric_name}: {change:+.1%}"
                )
            else:
                unchanged.append(
                    f"{metric_name}: {change:+.1%}"
                )

        parts = [f"Overall: {weighted_change:+.1%}"]

        if improved:
            parts.append(f"Improved: {', '.join(improved)}")
        if degraded:
            parts.append(f"Degraded: {', '.join(degraded)}")
        if unchanged:
            parts.append(f"Unchanged: {', '.join(unchanged)}")

        return "; ".join(parts)


class MetricsTracker:
    """
    Tracks historical metrics and adaptation effectiveness.

    Used to identify patterns in what works and what doesn't.
    """

    def __init__(self):
        self.collector = MetricsCollector()
        self.oracle = EffectivenessOracle()
        self.evaluation_history: dict[str, AdaptationMetrics] = {}

    def evaluate_proposal(
        self,
        proposal_id: str,
        boundary_id: str,
        pre_metrics: dict[str, float],
    ) -> None:
        """Register that we're about to evaluate a proposal."""
        self.collector.record_pre_adaptation_snapshot(
            boundary_id=boundary_id,
            metrics=pre_metrics,
        )

    def evaluate_post_adaptation(
        self,
        proposal_id: str,
        boundary_id: str,
        post_metrics: dict[str, float],
        observation_duration_seconds: float = 300,
    ) -> tuple[EffectivenessOutcome, float]:
        """
        Evaluate a proposal after observation period.

        Returns: (outcome, confidence)
        """
        adaptation_metrics = self.collector.record_post_adaptation_snapshot(
            proposal_id=proposal_id,
            boundary_id=boundary_id,
            metrics=post_metrics,
            observation_duration_seconds=observation_duration_seconds,
        )

        outcome, confidence, reasoning = self.oracle.evaluate_adaptation(
            adaptation_metrics
        )

        # Store for history
        self.evaluation_history[proposal_id] = adaptation_metrics

        # Update the adaptation_metrics with evaluation results
        adaptation_metrics.effectiveness = outcome
        adaptation_metrics.confidence = confidence
        adaptation_metrics.reasoning = reasoning

        return outcome, confidence

    def get_adaptation_effectiveness(self, proposal_id: str) -> Optional[AdaptationMetrics]:
        """Retrieve effectiveness evaluation for a proposal."""
        return self.evaluation_history.get(proposal_id)

    def get_boundary_effectiveness_summary(self, boundary_id: str) -> dict:
        """Get effectiveness summary for all adaptations of a boundary."""
        boundary_evals = [
            m for m in self.evaluation_history.values()
            if m.boundary_id == boundary_id
        ]

        if not boundary_evals:
            return {"total": 0, "improved": 0, "unchanged": 0, "degraded": 0, "success_rate": 0.0}

        improved_count = sum(
            1 for m in boundary_evals
            if m.effectiveness == EffectivenessOutcome.IMPROVED
        )
        unchanged_count = sum(
            1 for m in boundary_evals
            if m.effectiveness == EffectivenessOutcome.UNCHANGED
        )
        degraded_count = sum(
            1 for m in boundary_evals
            if m.effectiveness == EffectivenessOutcome.DEGRADED
        )

        total = len(boundary_evals)
        success_rate = (improved_count + unchanged_count) / total if total > 0 else 0.0

        return {
            "total": total,
            "improved": improved_count,
            "unchanged": unchanged_count,
            "degraded": degraded_count,
            "success_rate": success_rate,
        }
