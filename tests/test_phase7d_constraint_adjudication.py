"""
Phase 7D: Constraint Adjudication and Sophisticated Attacks

Build on Phase 7C anomaly detector to test:
1. Measurement integrity attacks (Goodhart/Pareto gaming)
2. Constraint adjudication interface (human-in-loop decisions)
3. Slow-burn degradation attacks
4. Multi-metric correlation attacks

Hypothesis: Multi-signal anomaly detector catches sophisticated attacks
that try to evade single-metric thresholds.
"""

import pytest
from src.governance.anomaly_detector import AdaptiveAnomalyDetector


class TestPhase7dMeasurementIntegrity:
    """Test anomaly detector against measurement integrity attacks."""

    def test_pareto_gaming_detected(self):
        """Detect when attacker optimizes one metric at expense of others."""
        detector = AdaptiveAnomalyDetector("db_cpu")

        print("\n=== Phase 7D: Pareto Gaming Detection ===\n")

        # Learning phase: normal correlated metrics
        baseline = [40, 42, 41, 39, 40]
        for i, val in enumerate(baseline):
            detector.add_observation(val, i)

        print(f"Baseline learned: mean={detector.baseline_mean:.1f}±{detector.baseline_std:.2f}")

        # Attack: keep metric low but degrade other indicators
        # (In real system, this would be correlated with other metrics)
        test_sequence = [
            (40, "normal"),
            (38, "normal"),
            (42, "low cpu (gaming: kept within bounds)"),
            (39, "low cpu (gaming continues)"),
            (41, "low cpu (but memory climbing elsewhere)"),
        ]

        anomalies = 0
        for idx, (value, desc) in enumerate(test_sequence):
            result = detector.detect_anomaly(value, len(baseline) + idx)
            if result.is_anomaly:
                anomalies += 1
                status = "✗ SUSPICIOUS"
            else:
                status = "✓ NORMAL"
            print(f"  Value {value} ({desc}): {status} (score={result.anomaly_score:.2f})")

        print(f"\nResult: Detected {anomalies}/5 as anomalous")

    def test_slow_burn_degradation(self):
        """Detect slow, sustained degradation that accumulates."""
        detector = AdaptiveAnomalyDetector("query_latency")

        print("\n=== Phase 7D: Slow-Burn Degradation ===\n")

        # Learning phase: stable latencies
        baseline = [50, 51, 49, 50, 52]
        for i, val in enumerate(baseline):
            detector.add_observation(val, i)

        print(f"Baseline: {detector.baseline_mean:.0f}ms ±{detector.baseline_std:.0f}ms")

        # Attack: gradual increase over time (not sudden spike)
        test_sequence = [50, 53, 56, 59, 62, 65, 68, 71]

        detected_at = None
        for idx, value in enumerate(test_sequence):
            result = detector.detect_anomaly(value, len(baseline) + idx)
            status = "🔴 DETECTED" if result.is_anomaly else "○ normal"
            print(f"  Iter {idx+1}: {value}ms {status} (score={result.anomaly_score:.2f})")

            if result.is_anomaly and detected_at is None:
                detected_at = idx + 1

        if detected_at:
            print(f"\n✓ Slow-burn attack detected at iteration {detected_at}")
        else:
            print(f"\n✗ Slow-burn attack NOT detected (all iterations normal)")

    def test_metric_injection_attack(self):
        """Detect when attacker injects false data into metric stream."""
        detector = AdaptiveAnomalyDetector("request_rate")

        print("\n=== Phase 7D: Metric Injection Attack ===\n")

        # Learning: steady request rate
        baseline = [1000, 1020, 980, 1010, 990]
        for i, val in enumerate(baseline):
            detector.add_observation(val, i)

        print(f"Expected rate: {detector.baseline_mean:.0f}±{detector.baseline_std:.0f} req/s")

        # Injection attack: spike metrics to make things look good
        test_sequence = [
            (1000, "legitimate"),
            (2000, "injected spike"),  # Tries to inflate metrics
            (500, "crash after injection"),  # System can't sustain
            (800, "recovery attempt"),
            (3500, "desperation: massive injection"),
        ]

        detections = []
        for idx, (value, desc) in enumerate(test_sequence):
            result = detector.detect_anomaly(value, len(baseline) + idx)
            detected = "✓ DETECTED" if result.is_anomaly else "✗ missed"
            detections.append(result.is_anomaly)

            signals_triggered = [s.signal_type for s in result.signals if s.exceeded]
            print(f"  {value} req/s ({desc}): {detected} (signals: {signals_triggered})")

        injection_detected = sum(detections[1:])  # Exclude first normal
        print(f"\nDetected {injection_detected}/4 injection attempts")
        assert injection_detected >= 2, f"Should detect at least 2 injection attempts"


class TestPhase7dConstraintAdjudication:
    """Test constraint adjudication interface (human-in-loop)."""

    def test_adjudication_proposal_generation(self):
        """Generate proposals when anomalies detected for human review."""
        detector = AdaptiveAnomalyDetector("resource_limit")

        print("\n=== Phase 7D: Adjudication Proposal Generation ===\n")

        # First, establish baseline and pass burn-in period
        baseline_metrics = [40, 42, 41, 39, 40, 40, 41, 42]  # 8 observations to pass burn-in
        for idx, value in enumerate(baseline_metrics):
            detector.add_observation(value, idx)

        print(f"Baseline established: mean={detector.baseline_mean:.1f}±{detector.baseline_std:.2f}")
        print(f"Now at observation {detector.observation_count}, burn-in until 25\n")

        # Now simulate metric stream with violations (still in burn-in but with clear anomalies)
        metrics = [40, 42, 65, 68, 70, 42, 39, 41]
        violations_detected = []

        for idx, value in enumerate(metrics):
            result = detector.detect_anomaly(value, len(baseline_metrics) + idx)
            if result.is_anomaly:
                violations_detected.append({
                    "iteration": len(baseline_metrics) + idx,
                    "value": value,
                    "score": result.anomaly_score,
                    "signals": [s.signal_type for s in result.signals if s.exceeded],
                    "explanation": result.explanation,
                })
                print(f"  ⚠️ Violation at iter {len(baseline_metrics) + idx}: value={value}, score={result.anomaly_score:.2f}")
            else:
                print(f"  ✓ Normal at iter {len(baseline_metrics) + idx}: value={value}")

        print(f"\nAnomalies detected for adjudication: {len(violations_detected)}")
        for v in violations_detected:
            print(f"  - Iter {v['iteration']}: {v['explanation']}")

        # With established baseline, spikes should be detectable
        assert len(violations_detected) >= 1, f"Should detect at least 1 anomaly, got {len(violations_detected)}"


class TestPhase7dMultiSignalSophistication:
    """Test detector against attacks that try multiple signal evasion."""

    def test_balanced_evasion_attack(self):
        """Attack that avoids triggering any single signal strongly."""
        detector = AdaptiveAnomalyDetector("balanced_evasion")

        print("\n=== Phase 7D: Balanced Evasion Attack ===\n")

        # Learning: establish baseline
        baseline = [50, 52, 48, 50, 51]
        for i, val in enumerate(baseline):
            detector.add_observation(val, i)

        # Evasion strategy: moderate increase that might avoid
        # triggering individual signals too strongly
        test_sequence = [
            (50, "normal"),
            (55, "small increase"),
            (60, "moderate increase"),
            (62, "sustained at high level"),
        ]

        detector_results = []
        for idx, (value, desc) in enumerate(test_sequence):
            result = detector.detect_anomaly(value, len(baseline) + idx)
            detector_results.append(result)

            triggered = [s.signal_type for s in result.signals if s.exceeded]
            print(f"  {value} ({desc}): score={result.anomaly_score:.2f}, signals={triggered}")

        # Multi-signal detector should catch this even if balanced
        late_detections = sum(1 for r in detector_results[1:] if r.is_anomaly)
        print(f"\nDetected {late_detections}/3 evasion attempts with multi-signal approach")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
