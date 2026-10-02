"""
Phase 7C: Adaptive Anomaly Scoring Fix

Solve the Phase 7B discrepancy: attacks under workload noise weren't detected.

Solution: Replace binary threshold with multi-signal anomaly scoring:
1. Deviation from baseline (z-score)
2. Rate-of-change acceleration
3. Persistent elevation (multiple high values)
4. Variance spikes (unusual within recent variance period)

Hypothesis: Combined anomaly score detects attacks hidden in variance
while maintaining false-positive immunity on legitimate load.
"""

import pytest
from src.governance.anomaly_detector import AdaptiveAnomalyDetector


class TestPhase7cAnomalyScoring:
    """Test anomaly detector solving Phase 7B gap."""

    def test_anomaly_detector_detects_attacks_under_noise(self):
        """Anomaly detector should detect attacks masked by legitimate variance."""
        detector = AdaptiveAnomalyDetector("test_boundary")

        print("\n=== Phase 7C: Anomaly Scoring on Noisy Attack ===\n")

        # Phase 1: Learning (normal baseline with variance)
        print("Phase 1: Learning baseline from legitimate load")
        baseline_values = [40, 42, 38, 45, 39, 41, 43, 40, 44, 38]
        for i, val in enumerate(baseline_values):
            detector.add_observation(val, i)

        print(f"  Baseline learned: mean={detector.baseline_mean:.1f}, std={detector.baseline_std:.1f}")

        # Phase 2: Attack hidden in variance
        print("\nPhase 2: Attack signal mixed with legitimate variance")

        # Sequence: normal, normal, attack, normal, attack, normal, attack
        test_sequence = [
            (40, "legitimate"),
            (38, "legitimate"),
            (65, "attack"),  # 60% spike (stands out even in variance)
            (42, "legitimate"),
            (68, "attack"),  # Another spike
            (39, "legitimate"),
            (70, "attack"),  # Strong sustained attack
        ]

        detected_attacks = 0
        missed_attacks = 0
        false_positives = 0

        for idx, (value, is_attack) in enumerate(test_sequence):
            result = detector.detect_anomaly(value, len(baseline_values) + idx)

            if is_attack == "attack":
                if result.is_anomaly:
                    detected_attacks += 1
                    status = "✓ DETECTED"
                else:
                    missed_attacks += 1
                    status = "✗ MISSED"
            else:
                if result.is_anomaly:
                    false_positives += 1
                    status = "✗ FALSE POS"
                else:
                    status = "✓ CORRECT"

            print(f"  Value {value}: {status} (score={result.anomaly_score:.2f})")
            print(f"    Signals: {[s.signal_type for s in result.signals if s.exceeded]}")

        print(f"\nPhase 7C Results:")
        print(f"  Detected attacks: {detected_attacks}/3")
        print(f"  Missed attacks: {missed_attacks}/3")
        print(f"  False positives: {false_positives}/4")

        # Validation
        assert detected_attacks >= 2, f"Should detect at least 2/3 attacks, got {detected_attacks}"
        assert false_positives == 0, f"Should have no false positives, got {false_positives}"

        print(f"\n✓ Anomaly scoring successfully detects attacks under variance")

    def test_anomaly_detector_legitimate_load_immunity(self):
        """Anomaly detector maintains false-positive immunity on legitimate load."""
        detector = AdaptiveAnomalyDetector("test_boundary_2")

        print("\n=== Phase 7C: False-Positive Immunity ===\n")

        # Simulate 50 iterations of legitimate load with diurnal variance
        legitimate_sequence = []
        for hour in range(48):
            # Diurnal pattern: higher during day (8-18), lower at night
            if 8 <= (hour % 24) <= 18:
                value = 45 + (hour % 5)  # Day: 45-50 with variance
            else:
                value = 35 + (hour % 5)  # Night: 35-40 with variance
            legitimate_sequence.append(value)

        anomalies_detected = 0
        print(f"Processing 48 legitimate load observations...")

        for idx, value in enumerate(legitimate_sequence):
            result = detector.detect_anomaly(value, idx)
            if result.is_anomaly:
                anomalies_detected += 1
                print(f"  Iter {idx}: FALSE POSITIVE detected (score={result.anomaly_score:.2f})")

        false_positive_rate = anomalies_detected / len(legitimate_sequence)
        print(f"\nFalse-positive rate: {false_positive_rate:.1%} ({anomalies_detected}/{len(legitimate_sequence)})")

        # Should maintain <5% false-positive rate
        assert false_positive_rate < 0.05, f"False-positive rate too high: {false_positive_rate:.1%}"

        print(f"\n✓ Anomaly detector maintains false-positive immunity")

    def test_anomaly_detector_signal_breakdown(self):
        """Verify which signals trigger on attacks vs legitimate load."""
        detector = AdaptiveAnomalyDetector("test_boundary_3")

        print("\n=== Phase 7C: Signal Breakdown ===\n")

        # Learning
        for i, val in enumerate([40, 41, 39, 42, 40]):
            detector.add_observation(val, i)

        # Test: sudden spike (should trigger multiple signals)
        result = detector.detect_anomaly(70, 5)

        print(f"Attack signal (value=70):")
        print(f"  Overall anomaly score: {result.anomaly_score:.2f}")
        print(f"  Signals triggered:")
        for signal in result.signals:
            status = "✓" if signal.exceeded else "✗"
            print(f"    {status} {signal.signal_type}: {signal.score:.2f}")

        # Test: legitimate variance
        result2 = detector.detect_anomaly(43, 6)

        print(f"\nLegitimate variance (value=43):")
        print(f"  Overall anomaly score: {result2.anomaly_score:.2f}")
        print(f"  Signals triggered:")
        for signal in result2.signals:
            status = "✓" if signal.exceeded else "✗"
            print(f"    {status} {signal.signal_type}: {signal.score:.2f}")

        # Attack should trigger more signals
        attack_signals_exceeded = sum(1 for s in result.signals if s.exceeded)
        legit_signals_exceeded = sum(1 for s in result2.signals if s.exceeded)

        assert attack_signals_exceeded > legit_signals_exceeded, "Attack should trigger more signals"

        print(f"\n✓ Signal breakdown shows multi-signal approach discriminates attacks")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
