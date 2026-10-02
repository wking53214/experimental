"""
Baseline reasoning: detect semantic poisoning via historical comparison.

Phase 2.5: Defend against attacks that masquerade as "expected" behavior.

Semantic poisoning succeeds when:
- Adversary marks violations as "database_migration" (legitimate context)
- Semantic layer accepts it as expected
- But violation pattern/magnitude is anomalous compared to historical database migrations

Defense: Maintain baseline profiles of legitimate activities.
When "expected" activity occurs, compare against historical baseline.
Deviations flag as poisoned (expected appearance, anomalous content).
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict
import time


class BaselineType(Enum):
    """Categories of baseline patterns."""
    MAINTENANCE_WINDOW = "maintenance_window"
    PEAK_LOAD = "peak_load"
    BATCH_JOB = "batch_job"
    KNOWN_WORKLOAD = "known_workload"
    ANOMALY = "anomaly"


@dataclass
class BaselineProfile:
    """Historical profile of a known workload pattern."""
    pattern_name: str
    workload_type: str

    # Statistical characteristics from historical observations
    avg_violation_rate: float = 0.0
    avg_throughput: float = 0.0
    avg_latency_p99: float = 0.0

    # Acceptable variance (standard deviation)
    violation_rate_std: float = 0.05
    throughput_std: float = 100.0
    latency_std: float = 50.0

    # Expected duration
    expected_duration_seconds: float = 300.0

    # Frequency
    occurrences_count: int = 0
    last_observed: float = field(default_factory=time.time)

    # Severity thresholds
    max_violation_rate: float = 0.5  # >50% violations = anomalous
    min_throughput: float = 10.0     # <10 throughput = anomalous


@dataclass
class BaselineAnomaly:
    """Detected deviation from baseline pattern."""
    pattern_name: str
    deviation_type: str  # "high_violation", "low_throughput", "long_duration"
    expected_value: float
    observed_value: float
    std_deviations: float  # How many stds away from mean
    severity: float  # 0.0 - 1.0
    timestamp: float = field(default_factory=time.time)


class BaselineComparator:
    """
    Compare current observations against historical baselines.

    Detects semantic poisoning: activities marked as "expected" that deviate
    from historical patterns of that activity type.
    """

    def __init__(self):
        self.profiles: Dict[str, BaselineProfile] = {}
        self.anomalies: Dict[str, BaselineAnomaly] = []

    def register_pattern(
        self,
        pattern_name: str,
        workload_type: str,
        avg_violation_rate: float = 0.1,
        avg_throughput: float = 1000.0,
        avg_latency_p99: float = 150.0,
    ) -> BaselineProfile:
        """Register a known workload pattern baseline."""
        profile = BaselineProfile(
            pattern_name=pattern_name,
            workload_type=workload_type,
            avg_violation_rate=avg_violation_rate,
            avg_throughput=avg_throughput,
            avg_latency_p99=avg_latency_p99,
        )
        self.profiles[pattern_name] = profile
        return profile

    def update_baseline(
        self,
        pattern_name: str,
        violation_rate: float,
        throughput: float,
        latency_p99: float,
    ) -> None:
        """Update baseline with new observation."""
        if pattern_name not in self.profiles:
            return

        profile = self.profiles[pattern_name]
        profile.occurrences_count += 1
        profile.last_observed = time.time()

        # Simple rolling average update
        n = profile.occurrences_count
        profile.avg_violation_rate = (
            (profile.avg_violation_rate * (n - 1) + violation_rate) / n
        )
        profile.avg_throughput = (
            (profile.avg_throughput * (n - 1) + throughput) / n
        )
        profile.avg_latency_p99 = (
            (profile.avg_latency_p99 * (n - 1) + latency_p99) / n
        )

    def detect_poisoning(
        self,
        pattern_name: str,
        observed_violation_rate: float,
        observed_throughput: float,
        observed_latency_p99: float,
    ) -> Optional[BaselineAnomaly]:
        """
        Check if observed metrics deviate from baseline pattern.

        Semantic poisoning appears as:
        - Activity marked as "expected" (e.g., "database_migration")
        - But violation rate/throughput deviate from historical pattern

        Returns: BaselineAnomaly if detected, None if within normal range
        """
        if pattern_name not in self.profiles:
            return None

        profile = self.profiles[pattern_name]

        # Check violation rate deviation
        violation_deviation = (
            observed_violation_rate - profile.avg_violation_rate
        ) / max(profile.violation_rate_std, 0.01)

        if abs(violation_deviation) > 2.0:  # >2 std devs = anomalous
            return BaselineAnomaly(
                pattern_name=pattern_name,
                deviation_type="violation_rate_anomaly",
                expected_value=profile.avg_violation_rate,
                observed_value=observed_violation_rate,
                std_deviations=abs(violation_deviation),
                severity=min(1.0, abs(violation_deviation) / 3.0),
            )

        # Check throughput deviation
        throughput_deviation = (
            profile.avg_throughput - observed_throughput
        ) / max(profile.throughput_std, 1.0)

        if abs(throughput_deviation) > 2.0:  # >2 std devs = anomalous
            return BaselineAnomaly(
                pattern_name=pattern_name,
                deviation_type="throughput_anomaly",
                expected_value=profile.avg_throughput,
                observed_value=observed_throughput,
                std_deviations=abs(throughput_deviation),
                severity=min(1.0, abs(throughput_deviation) / 3.0),
            )

        # Check latency deviation
        latency_deviation = (
            observed_latency_p99 - profile.avg_latency_p99
        ) / max(profile.latency_std, 1.0)

        if abs(latency_deviation) > 2.0:  # >2 std devs = anomalous
            return BaselineAnomaly(
                pattern_name=pattern_name,
                deviation_type="latency_anomaly",
                expected_value=profile.avg_latency_p99,
                observed_value=observed_latency_p99,
                std_deviations=abs(latency_deviation),
                severity=min(1.0, abs(latency_deviation) / 3.0),
            )

        return None

    def is_poisoned_claim(
        self,
        pattern_name: str,
        observed_violation_rate: float,
        observed_throughput: float,
        observed_latency_p99: float,
    ) -> bool:
        """
        Quick check: does this "expected" activity look poisoned?

        True = activity marked expected but deviates from baseline
        False = activity matches historical pattern of this type
        """
        anomaly = self.detect_poisoning(
            pattern_name,
            observed_violation_rate,
            observed_throughput,
            observed_latency_p99,
        )
        return anomaly is not None

    def get_profiles(self) -> Dict[str, BaselineProfile]:
        """Get all registered baseline profiles."""
        return dict(self.profiles)

    def get_anomalies(self, pattern_name: str) -> list[BaselineAnomaly]:
        """Get detected anomalies for a pattern."""
        return [
            a for a in self.anomalies
            if a.pattern_name == pattern_name
        ]
