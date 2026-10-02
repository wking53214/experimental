"""
Metrics collection and effectiveness measurement.

Phase 2 Sprint 2: Replace categorical validation with data-driven effectiveness
measurement. Track key metrics before/after adaptation and determine whether
the adaptation actually improved the system.

Phase 8B: Metric collection infrastructure for end-to-end governance workflows.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Any
import time
import statistics


class MetricType(Enum):
    """Type of metric being tracked."""
    VIOLATION_RATE = "violation_rate"
    SLO_ATTAINMENT = "slo_attainment"
    THROUGHPUT = "throughput"
    LATENCY_P99 = "latency_p99"
    ERROR_RATE = "error_rate"
    EFFICIENCY = "efficiency"  # throughput / violation_rate
    RATE_OF_CHANGE = "rate_of_change"  # sudden metric swings
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

        # GOODHART PATTERN 4: Rate-of-change anomaly (sudden metrics swings)
        # Detect when multiple metrics change dramatically in same direction
        large_changes = sum(
            1 for m in metric_changes.values()
            if abs(m["change"]) > 0.3  # >30% change is suspicious
        )

        if large_changes >= 3:
            return {
                "reasoning": (
                    f"Rate-of-change anomaly: {large_changes} metrics swung >30% simultaneously. "
                    f"Suggests system under stress or adversarial attack."
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


class MetricDatapoint:
    """
    Single timestamped metric observation for a boundary.

    Supports multiple metrics per boundary with efficient lookup.
    """

    def __init__(self, timestamp: float, boundary_id: str):
        self.timestamp = timestamp
        self.boundary_id = boundary_id
        self.metrics: dict[str, float] = {}

    def set_metric(self, name: str, value: float) -> None:
        """Set a metric value."""
        self.metrics[name] = value

    def get_metric(self, name: str) -> Optional[float]:
        """Retrieve a metric value."""
        return self.metrics.get(name)

    def get_all_metrics(self) -> dict[str, float]:
        """Get all metrics in this datapoint."""
        return self.metrics.copy()


class MetricStream:
    """
    Time-series metric ingestion for boundaries.

    Enables:
    - Multiple metrics per boundary
    - History buffering with configurable window
    - Timestamp tracking
    - Baseline establishment
    - Multi-metric correlation analysis

    Phase 8B: Core infrastructure for end-to-end governance workflows.
    """

    def __init__(self, boundary_id: str, history_window: int = 100):
        """
        Initialize metric stream for a boundary.

        Args:
            boundary_id: Identifier for the boundary this stream tracks
            history_window: Number of datapoints to buffer in memory
        """
        self.boundary_id = boundary_id
        self.history_window = history_window
        self.history: list[MetricDatapoint] = []
        self.baseline: Optional[dict[str, dict]] = None
        self.baseline_established = False
        self.correlation_matrix: Optional[dict] = None

    def add_observation(self, timestamp: float, metrics: dict[str, float]) -> MetricDatapoint:
        """
        Add a new observation to the stream.

        Args:
            timestamp: Unix timestamp of observation
            metrics: Dict of {metric_name: value}

        Returns:
            The created MetricDatapoint
        """
        datapoint = MetricDatapoint(timestamp, self.boundary_id)
        for name, value in metrics.items():
            datapoint.set_metric(name, value)

        self.history.append(datapoint)

        # Trim history to window size
        if len(self.history) > self.history_window:
            self.history = self.history[-self.history_window:]

        return datapoint

    def get_history(self, metric_name: Optional[str] = None) -> list:
        """
        Get metric history.

        Args:
            metric_name: If provided, return only this metric's values
                        If None, return all datapoints

        Returns:
            List of tuples (timestamp, value) or list of datapoints
        """
        if metric_name is None:
            return self.history.copy()

        return [
            (dp.timestamp, dp.get_metric(metric_name))
            for dp in self.history
            if dp.get_metric(metric_name) is not None
        ]

    def get_latest(self, metric_name: Optional[str] = None) -> Optional[tuple]:
        """
        Get the most recent observation.

        Returns:
            (timestamp, value) for a metric or latest datapoint
        """
        if not self.history:
            return None

        latest_dp = self.history[-1]

        if metric_name is None:
            return (latest_dp.timestamp, latest_dp.get_all_metrics())

        value = latest_dp.get_metric(metric_name)
        if value is not None:
            return (latest_dp.timestamp, value)
        return None

    def get_metric_names(self) -> set[str]:
        """Get all metric names in this stream."""
        names = set()
        for dp in self.history:
            names.update(dp.metrics.keys())
        return names

    def get_time_range(self) -> Optional[tuple[float, float]]:
        """Get min and max timestamps in history."""
        if not self.history:
            return None
        return (self.history[0].timestamp, self.history[-1].timestamp)

    def size(self) -> int:
        """Get number of observations in history."""
        return len(self.history)


class BaselineEstablisher:
    """
    Learns and maintains baseline metrics for attack detection.

    Handles:
    - Automatic baseline learning from metric history
    - Handling of startup transients
    - Baseline refresh strategies
    """

    def __init__(self,
                 min_observations: int = 10,
                 learning_buffer_size: int = 50):
        """
        Initialize baseline establisher.

        Args:
            min_observations: Minimum datapoints needed before baseline is valid
            learning_buffer_size: Size of buffer for learning phase
        """
        self.min_observations = min_observations
        self.learning_buffer_size = learning_buffer_size
        self.baselines: dict[str, dict] = {}  # {metric_name: baseline_stats}
        self.established_at: dict[str, float] = {}  # {metric_name: timestamp}

    def learn_baseline(self, stream: MetricStream) -> dict[str, dict]:
        """
        Establish baseline from a metric stream.

        Returns dict of {metric_name: {mean, std, min, max, percentile_5, percentile_95}}
        """
        if stream.size() < self.min_observations:
            return {}

        baselines = {}
        metric_names = stream.get_metric_names()

        for metric_name in metric_names:
            history = stream.get_history(metric_name)
            if not history:
                continue

            values = [v for _, v in history]

            if len(values) < self.min_observations:
                continue

            # Use only recent data for learning (trim oldest to handle startup)
            learning_data = values[-self.learning_buffer_size:]

            try:
                mean = statistics.mean(learning_data)
                stdev = statistics.stdev(learning_data) if len(learning_data) > 1 else 1.0

                # Percentiles
                sorted_data = sorted(learning_data)
                n = len(sorted_data)
                p5_idx = max(0, int(n * 0.05))
                p95_idx = min(n - 1, int(n * 0.95))

                baselines[metric_name] = {
                    "mean": mean,
                    "std": stdev,
                    "min": min(learning_data),
                    "max": max(learning_data),
                    "percentile_5": sorted_data[p5_idx],
                    "percentile_95": sorted_data[p95_idx],
                    "count": len(learning_data),
                }

                self.established_at[metric_name] = time.time()
            except (statistics.StatisticsError, ValueError):
                continue

        self.baselines = baselines
        return baselines

    def get_baseline(self, metric_name: str) -> Optional[dict]:
        """Get baseline stats for a metric."""
        return self.baselines.get(metric_name)

    def is_established(self, metric_name: Optional[str] = None) -> bool:
        """Check if baseline is established (for a specific metric or any)."""
        if metric_name is None:
            return len(self.baselines) > 0
        return metric_name in self.baselines

    def refresh_baseline(self, stream: MetricStream) -> bool:
        """
        Refresh baseline with latest data.

        Returns True if baseline was updated.
        """
        new_baseline = self.learn_baseline(stream)
        return len(new_baseline) > 0


class CorrelationAnalyzer:
    """
    Detects multi-metric attacks and gaming patterns.

    Identifies:
    - Pareto gaming (optimize one metric at expense of others)
    - Cross-metric anomalies
    - Coordinated metric attacks
    """

    def __init__(self, correlation_threshold: float = 0.7):
        """
        Initialize correlation analyzer.

        Args:
            correlation_threshold: Threshold for detecting correlated changes
        """
        self.correlation_threshold = correlation_threshold
        self.metric_correlations: dict[str, dict] = {}  # Pairwise correlations

    def analyze_stream(self, stream: MetricStream, baseline: dict) -> dict:
        """
        Analyze metric correlations and detect gaming patterns.

        Returns dict with:
        - anomalies: list of detected multi-metric anomalies
        - gaming_detected: bool indicating Pareto gaming
        - explanations: list of explanations
        """
        results = {
            "anomalies": [],
            "gaming_detected": False,
            "pareto_gaming_score": 0.0,
            "explanations": [],
        }

        if not baseline or stream.size() < 2:
            return results

        latest = stream.get_latest()
        if not latest:
            return results

        _, latest_metrics = latest

        # Check for Pareto gaming: one metric improves while critical ones degrade
        gaming_score = self._detect_pareto_gaming(latest_metrics, baseline)

        if gaming_score > 0.5:
            results["gaming_detected"] = True
            results["pareto_gaming_score"] = gaming_score
            results["explanations"].append(
                f"Pareto gaming detected: improved non-critical metrics at expense of critical ones (score: {gaming_score:.2f})"
            )

        # Detect coordinated multi-metric anomalies
        anomaly_count = self._count_anomalies(latest_metrics, baseline)

        if anomaly_count >= 2:
            results["anomalies"].append({
                "type": "multi_metric_anomaly",
                "count": anomaly_count,
                "severity": min(1.0, anomaly_count / len(baseline)),
            })
            results["explanations"].append(
                f"Multi-metric anomaly: {anomaly_count} metrics deviate from baseline simultaneously"
            )

        return results

    def _detect_pareto_gaming(self, current: dict, baseline: dict) -> float:
        """
        Detect Pareto gaming pattern.

        Score from 0 (no gaming) to 1.0 (definite gaming).

        Gaming occurs when one set of metrics improves while another degrades,
        suggesting optimization of wrong metrics at expense of critical ones.
        """
        if not current or not baseline:
            return 0.0

        # Define critical metrics that should not degrade
        # For lower-is-better metrics: negative change = improvement
        # For higher-is-better metrics: positive change = improvement
        critical_lower_is_better = {"violation_rate", "error_rate", "latency_p99"}
        critical_higher_is_better = {"slo_attainment", "throughput"}

        critical_improvements = []  # How much critical metrics improved
        critical_degradations = []  # How much critical metrics degraded

        for metric_name, value in current.items():
            if metric_name not in baseline:
                continue

            baseline_stats = baseline[metric_name]
            baseline_mean = baseline_stats.get("mean", 1.0)

            if baseline_mean == 0:
                continue

            change = (value - baseline_mean) / abs(baseline_mean)

            # Determine if this change is improvement or degradation
            if metric_name in critical_lower_is_better:
                # Lower is better: negative change = improvement
                if change < -0.05:  # Significant improvement
                    critical_improvements.append(-change)
                elif change > 0.05:  # Significant degradation
                    critical_degradations.append(change)
            elif metric_name in critical_higher_is_better:
                # Higher is better: positive change = improvement
                if change > 0.05:  # Significant improvement
                    critical_improvements.append(change)
                elif change < -0.05:  # Significant degradation
                    critical_degradations.append(-change)

        # Pareto gaming: critical metrics improve significantly while some degrade significantly
        # This suggests gaming of one critical metric at expense of another
        if critical_improvements and critical_degradations:
            avg_improvement = sum(critical_improvements) / len(critical_improvements)
            avg_degradation = sum(critical_degradations) / len(critical_degradations)

            # Both significant: strong signal of Pareto gaming
            if avg_improvement > 0.1 and avg_degradation > 0.1:
                return min(1.0, avg_improvement + avg_degradation)

        return 0.0

    def _count_anomalies(self, current: dict, baseline: dict) -> int:
        """Count how many metrics are anomalous (>2 std devs from baseline)."""
        count = 0

        for metric_name, value in current.items():
            if metric_name not in baseline:
                continue

            baseline_stats = baseline[metric_name]
            baseline_mean = baseline_stats.get("mean", 0.0)
            baseline_std = baseline_stats.get("std", 1.0)

            if baseline_std == 0:
                baseline_std = 1.0

            z_score = abs((value - baseline_mean) / baseline_std)

            if z_score > 2.0:  # 2 standard deviations
                count += 1

        return count


class DetectorPipeline:
    """
    Phase 8C: Complete detector pipeline integrating metrics with anomaly detection.

    Orchestrates:
    1. Metric stream ingestion
    2. Baseline establishment
    3. Correlation analysis
    4. Multi-metric anomaly detection
    5. Violation event generation
    """

    def __init__(self, boundary_id: str, history_window: int = 100):
        """
        Initialize detector pipeline for a boundary.

        Args:
            boundary_id: The boundary being monitored
            history_window: Size of metric history buffer
        """
        self.boundary_id = boundary_id
        self.stream = MetricStream(boundary_id, history_window=history_window)
        self.baseline_establisher = BaselineEstablisher(min_observations=15)
        self.correlation_analyzer = CorrelationAnalyzer()

        self.baseline = {}
        self.baseline_locked = False
        self.observation_count = 0

        # Detection state
        self.last_anomaly_result = None
        self.anomaly_threshold = 0.85
        self.gaming_threshold = 0.5

    def ingest_metrics(self, timestamp: float, metrics: dict[str, float]) -> None:
        """
        Ingest new metric observation into the pipeline.

        Args:
            timestamp: Unix timestamp of observation
            metrics: Dict of {metric_name: value}
        """
        self.observation_count += 1
        self.stream.add_observation(timestamp, metrics)

        # Auto-establish baseline after learning window
        if not self.baseline_locked and self.stream.size() >= 15:
            self.baseline = self.baseline_establisher.learn_baseline(self.stream)
            if self.baseline:
                self.baseline_locked = True

    def detect_anomalies(self) -> dict:
        """
        Run multi-metric anomaly detection on current stream.

        Returns dict with:
        - anomaly_detected: bool
        - anomaly_score: float (0-1)
        - gaming_detected: bool
        - gaming_score: float (0-1)
        - anomaly_count: int (metrics >2σ from baseline)
        - explanation: str
        - signals: list of individual anomalies
        """
        if not self.baseline_locked or not self.baseline:
            return {
                "anomaly_detected": False,
                "anomaly_score": 0.0,
                "gaming_detected": False,
                "gaming_score": 0.0,
                "anomaly_count": 0,
                "explanation": "Baseline not yet established",
                "signals": [],
            }

        # Run correlation analysis
        results = self.correlation_analyzer.analyze_stream(self.stream, self.baseline)

        latest_metrics = self.stream.get_latest()
        if not latest_metrics:
            return {
                "anomaly_detected": False,
                "anomaly_score": 0.0,
                "gaming_detected": False,
                "gaming_score": 0.0,
                "anomaly_count": 0,
                "explanation": "No metrics available",
                "signals": [],
            }

        timestamp, current_metrics = latest_metrics

        # Combine signals
        anomaly_count = self._count_anomalies_from_baseline(current_metrics)
        gaming_score = results["pareto_gaming_score"]
        anomaly_score = self._calculate_combined_anomaly_score(
            anomaly_count, gaming_score
        )

        # Generate signals
        signals = []

        if anomaly_count >= 2:
            signals.append({
                "type": "multi_metric_anomaly",
                "count": anomaly_count,
                "severity": min(1.0, anomaly_count / len(self.baseline)),
            })

        if results["gaming_detected"]:
            signals.append({
                "type": "pareto_gaming",
                "score": gaming_score,
                "severity": gaming_score,
            })

        anomaly_detected = anomaly_score > self.anomaly_threshold

        return {
            "anomaly_detected": anomaly_detected,
            "anomaly_score": anomaly_score,
            "gaming_detected": results["gaming_detected"],
            "gaming_score": gaming_score,
            "anomaly_count": anomaly_count,
            "explanation": "; ".join(results["explanations"]) if results["explanations"] else "Within normal parameters",
            "signals": signals,
        }

    def _count_anomalies_from_baseline(self, metrics: dict) -> int:
        """Count metrics deviating >2σ from baseline."""
        count = 0
        for metric_name, value in metrics.items():
            if metric_name not in self.baseline:
                continue

            baseline_stats = self.baseline[metric_name]
            baseline_mean = baseline_stats.get("mean", 0.0)
            baseline_std = baseline_stats.get("std", 1.0)

            if baseline_std == 0:
                baseline_std = 1.0

            z_score = abs((value - baseline_mean) / baseline_std)
            if z_score > 2.0:
                count += 1

        return count

    def _calculate_combined_anomaly_score(self, anomaly_count: int, gaming_score: float) -> float:
        """
        Combine anomaly and gaming signals into single score.

        Accounts for:
        - Number of anomalous metrics
        - Pareto gaming pattern
        """
        # Normalize anomaly count to 0-1
        if not self.baseline:
            anomaly_signal = 0.0
        else:
            total_metrics = len(self.baseline)
            # Boosted scaling: multiple anomalies are a strong signal
            if anomaly_count >= 2:
                # 2 anomalies: 0.6, 3: 0.8, 4: 1.0
                anomaly_signal = min(1.0, 0.5 + (anomaly_count * 0.2))
            else:
                anomaly_signal = anomaly_count / max(1, total_metrics * 2)

        # Combine: gaming is a strong signal, anomalies are supporting
        if gaming_score > 0.5:
            # Gaming detected: high confidence
            combined = gaming_score * 0.7 + anomaly_signal * 0.3
        else:
            # Use anomalies: need multiple metrics to be confident
            # With 2+ anomalies, significantly boost the score
            if anomaly_count >= 2:
                combined = anomaly_signal * 0.9 + gaming_score * 0.1
            else:
                combined = anomaly_signal * 0.6 + gaming_score * 0.4

        return min(1.0, combined)

    def get_baseline_stats(self, metric_name: Optional[str] = None) -> Optional[dict]:
        """Get baseline statistics for a metric or all metrics."""
        if metric_name is None:
            return self.baseline
        return self.baseline.get(metric_name)

    def get_stream_size(self) -> int:
        """Get number of observations in stream."""
        return self.stream.size()

    def get_metric_names(self) -> set[str]:
        """Get all metric names in the stream."""
        return self.stream.get_metric_names()

    def refresh_baseline(self) -> bool:
        """Refresh baseline with latest data."""
        self.baseline = self.baseline_establisher.learn_baseline(self.stream)
        return len(self.baseline) > 0
