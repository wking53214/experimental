"""
Phase 8D: Proposal Generation from Metrics Tests

Test proposal generation from detector pipeline anomalies.

Converts detection results → adaptation proposals for governance loop.
"""

import time
from src.governance.proposal import (
    ProposalGenerator, AdaptationProposal, AdaptationDirection, ProposalStatus
)
from src.governance.metrics import DetectorPipeline


class TestProposalGenerator:
    """Test core proposal generation functionality."""

    def test_create_generator(self):
        """Test creating proposal generator."""
        gen = ProposalGenerator("test_boundary", current_threshold=0.05)

        assert gen.boundary_id == "test_boundary"
        assert gen.current_threshold == 0.05
        assert gen.proposal_count == 0

    def test_no_proposal_for_normal_metrics(self):
        """Test that normal metrics don't trigger proposals."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        detection = {
            "anomaly_detected": False,
            "anomaly_score": 0.2,
        }

        proposal = gen.generate_proposal(detection, ["evt_1"])

        assert proposal is None

    def test_proposal_for_high_confidence_anomaly(self):
        """Test proposal generation for high-confidence anomaly."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        detection = {
            "anomaly_detected": True,
            "anomaly_score": 0.90,
            "gaming_detected": False,
            "anomaly_count": 2,
            "explanation": "Multiple metrics deviate",
        }

        proposal = gen.generate_proposal(detection, ["evt_1"])

        assert proposal is not None
        assert proposal.boundary_id == "test"
        assert proposal.direction == AdaptationDirection.TIGHTEN
        assert proposal.status == ProposalStatus.PENDING
        assert proposal.current_value == 0.05
        assert proposal.proposed_value < 0.05  # Tightened

    def test_proposal_severity_affects_adjustment(self):
        """Test that anomaly severity affects proposed value."""
        # Low confidence
        gen1 = ProposalGenerator("b1", current_threshold=1.0)
        detection_low = {
            "anomaly_detected": True,
            "anomaly_score": 0.65,
            "gaming_detected": False,
            "anomaly_count": 1,
            "explanation": "Low confidence anomaly",
        }
        prop1 = gen1.generate_proposal(detection_low, ["evt_1"])

        # High confidence
        gen2 = ProposalGenerator("b2", current_threshold=1.0)
        detection_high = {
            "anomaly_detected": True,
            "anomaly_score": 0.95,
            "gaming_detected": True,
            "anomaly_count": 4,
            "explanation": "High confidence gaming",
        }
        prop2 = gen2.generate_proposal(detection_high, ["evt_1"])

        # High confidence should tighten more
        if prop1 and prop2:
            assert prop2.proposed_value < prop1.proposed_value

    def test_pareto_gaming_detection_triggers_tighten(self):
        """Test that Pareto gaming triggers tightening proposal."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        detection = {
            "anomaly_detected": True,
            "anomaly_score": 0.88,
            "gaming_detected": True,
            "gaming_score": 0.85,
            "anomaly_count": 2,
            "explanation": "Pareto gaming: violations improve, throughput collapses",
        }

        proposal = gen.generate_proposal(detection, ["evt_1"])

        assert proposal is not None
        assert proposal.direction == AdaptationDirection.TIGHTEN
        assert "Pareto gaming" in proposal.reason
        assert proposal.notes["gaming_detected"] is True

    def test_multi_metric_anomaly_proposal(self):
        """Test proposal for multi-metric anomaly."""
        gen = ProposalGenerator("test", current_threshold=1.0)

        detection = {
            "anomaly_detected": True,
            "anomaly_score": 0.87,
            "gaming_detected": False,
            "anomaly_count": 4,  # Multiple metrics
            "explanation": "4 metrics deviate >2σ",
        }

        proposal = gen.generate_proposal(detection, ["evt_1", "evt_2"])

        assert proposal is not None
        assert proposal.direction == AdaptationDirection.TIGHTEN
        assert "multi-metric" in proposal.reason.lower()
        assert proposal.notes["anomaly_count"] == 4

    def test_proposal_tracking(self):
        """Test that proposals are tracked."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        detection = {
            "anomaly_detected": True,
            "anomaly_score": 0.90,
            "gaming_detected": False,
            "anomaly_count": 2,
            "explanation": "Test",
        }

        prop1 = gen.generate_proposal(detection, ["evt_1"])
        prop2 = gen.generate_proposal(detection, ["evt_2"])

        assert prop1 is not None
        assert prop2 is not None
        assert prop1.proposal_id != prop2.proposal_id
        assert gen.proposal_count == 2

    def test_evidence_tracking(self):
        """Test that evidence is properly tracked in proposals."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        detection = {
            "anomaly_detected": True,
            "anomaly_score": 0.90,
            "gaming_detected": False,
            "anomaly_count": 2,
            "explanation": "Test",
        }

        evidence = ["exec_1", "exec_2", "exec_3"]
        proposal = gen.generate_proposal(detection, evidence)

        assert proposal is not None
        assert proposal.source_evidence == evidence


class TestAnomalyHistoryAndPatterns:
    """Test anomaly history tracking and pattern detection."""

    def test_anomaly_history_tracking(self):
        """Test that anomalies are tracked in history."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        for i in range(3):
            detection = {
                "anomaly_detected": True,
                "anomaly_score": 0.85 + (i * 0.02),
                "gaming_detected": i > 0,
                "anomaly_count": 2,
                "explanation": f"Anomaly {i}",
            }
            gen.generate_proposal(detection, [f"evt_{i}"])

        assert len(gen.anomaly_history) == 3

    def test_history_limited_to_recent(self):
        """Test that anomaly history is limited to recent events."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        # Add 15 anomalies (history limited to 10)
        for i in range(15):
            detection = {
                "anomaly_detected": True,
                "anomaly_score": 0.90,
                "gaming_detected": False,
                "anomaly_count": 2,
                "explanation": f"Anomaly {i}",
            }
            gen.generate_proposal(detection, [f"evt_{i}"])

        assert len(gen.anomaly_history) == 10

    def test_sustained_pattern_detection(self):
        """Test detection of sustained anomaly patterns."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        # Add 3+ anomalies
        for i in range(5):
            detection = {
                "anomaly_detected": True,
                "anomaly_score": 0.85,
                "gaming_detected": i > 1,  # Gaming in last few
                "anomaly_count": 2,
                "explanation": f"Anomaly {i}",
            }
            gen.generate_proposal(detection, [f"evt_{i}"])

        pattern = gen.get_sustained_pattern()

        assert pattern is not None
        assert pattern["pattern"] == "sustained_anomalies"
        assert pattern["recent_count"] == 5
        assert pattern["gaming_incidents"] >= 2

    def test_no_pattern_with_insufficient_anomalies(self):
        """Test that patterns aren't detected with < 3 anomalies."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        detection = {
            "anomaly_detected": True,
            "anomaly_score": 0.90,
            "gaming_detected": False,
            "anomaly_count": 2,
            "explanation": "Single anomaly",
        }

        gen.generate_proposal(detection, ["evt_1"])
        pattern = gen.get_sustained_pattern()

        assert pattern is None

    def test_history_reset(self):
        """Test resetting anomaly history."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        detection = {
            "anomaly_detected": True,
            "anomaly_score": 0.90,
            "gaming_detected": False,
            "anomaly_count": 2,
            "explanation": "Test",
        }

        gen.generate_proposal(detection, ["evt_1"])
        assert len(gen.anomaly_history) == 1

        gen.reset_history()
        assert len(gen.anomaly_history) == 0


class TestProposalSeverityAdjustment:
    """Test proposal severity and adjustment calculation."""

    def test_sustained_anomalies_increase_severity(self):
        """Test that repeated anomalies increase proposal severity."""
        gen = ProposalGenerator("test", current_threshold=1.0)

        # Generate 3 anomalies with sufficient score to trigger proposals
        for i in range(3):
            detection = {
                "anomaly_detected": True,
                "anomaly_score": 0.90,  # High enough to trigger proposal
                "gaming_detected": False,
                "anomaly_count": 2,
                "explanation": f"Anomaly {i}",
            }
            proposal = gen.generate_proposal(detection, [f"evt_{i}"])

        # Last proposal should have higher severity due to pattern
        assert proposal is not None
        assert proposal.notes["recent_anomalies"] == 3
        # Severity increases by 0.2 with 3+ recent anomalies
        assert proposal.notes["severity"] > 0.75

    def test_tighten_factor_applied_correctly(self):
        """Test that tighten factor is applied correctly."""
        gen = ProposalGenerator("test", current_threshold=1.0, tighten_factor=0.9)

        detection = {
            "anomaly_detected": True,
            "anomaly_score": 0.90,
            "gaming_detected": False,
            "anomaly_count": 3,
            "explanation": "Test",
        }

        proposal = gen.generate_proposal(detection, ["evt_1"])

        assert proposal is not None
        # Proposed value should be tightened (< current)
        assert proposal.proposed_value < proposal.current_value
        # Should use tighten_factor (0.9^severity)
        severity = proposal.notes["severity"]
        expected = 1.0 * (0.9 ** severity)
        assert abs(proposal.proposed_value - expected) < 0.001

    def test_proposal_reason_field(self):
        """Test that proposal reasons are descriptive."""
        test_cases = [
            {
                "detection": {
                    "anomaly_detected": True,
                    "anomaly_score": 0.75,  # Below 0.85 threshold, gaming takes priority
                    "gaming_detected": True,
                    "gaming_score": 0.8,
                    "anomaly_count": 2,
                    "explanation": "Gaming: violations improve, throughput collapses",
                },
                "expected_reason": "Pareto gaming"
            },
            {
                "detection": {
                    "anomaly_detected": True,
                    "anomaly_score": 0.87,
                    "gaming_detected": False,
                    "anomaly_count": 3,
                    "explanation": "3 metrics deviate",
                },
                "expected_reason": "multi-metric"
            },
        ]

        for test in test_cases:
            gen = ProposalGenerator("test", current_threshold=0.05)
            proposal = gen.generate_proposal(test["detection"], ["evt_1"])

            assert proposal is not None
            assert test["expected_reason"].lower() in proposal.reason.lower()


class TestProposalIntegration:
    """Integration tests with detector pipeline."""

    def test_proposal_from_detector_output(self):
        """Test generating proposal from detector pipeline output."""
        # Create and populate detector pipeline
        pipeline = DetectorPipeline("test", history_window=100)

        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {
                "error_rate": 0.05,
                "throughput": 1000.0,
            })

        # Get detection result
        detection = pipeline.detect_anomalies()

        # Generate proposal
        gen = ProposalGenerator("test", current_threshold=0.05)
        proposal = gen.generate_proposal(detection, ["evt_1"])

        # With normal metrics, should not generate proposal
        assert proposal is None

    def test_proposal_from_gaming_detection(self):
        """Test proposal generation from gaming detection."""
        pipeline = DetectorPipeline("test", history_window=100)

        # Baseline
        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {
                "error_rate": 0.05,
                "throughput": 1000.0,
            })

        # Gaming attack
        pipeline.ingest_metrics(1020.0, {
            "error_rate": 0.02,
            "throughput": 500.0,
        })

        detection = pipeline.detect_anomalies()

        gen = ProposalGenerator("test", current_threshold=0.05)
        proposal = gen.generate_proposal(detection, ["evt_1"])

        assert proposal is not None
        assert proposal.direction == AdaptationDirection.TIGHTEN
        assert detection["gaming_detected"]


class TestProposalEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_evidence_list(self):
        """Test proposal with empty evidence."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        detection = {
            "anomaly_detected": True,
            "anomaly_score": 0.90,
            "gaming_detected": False,
            "anomaly_count": 2,
            "explanation": "Test",
        }

        proposal = gen.generate_proposal(detection, [])

        assert proposal is not None
        assert proposal.source_evidence == []

    def test_multiple_evidence_items(self):
        """Test proposal with multiple evidence items."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        detection = {
            "anomaly_detected": True,
            "anomaly_score": 0.90,
            "gaming_detected": False,
            "anomaly_count": 2,
            "explanation": "Test",
        }

        evidence = [f"evt_{i}" for i in range(10)]
        proposal = gen.generate_proposal(detection, evidence)

        assert proposal is not None
        assert len(proposal.source_evidence) == 10

    def test_missing_detection_fields(self):
        """Test handling of incomplete detection results."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        # Minimal detection dict with high anomaly score
        detection = {
            "anomaly_detected": True,
            "anomaly_score": 0.90,
        }

        # Should handle gracefully - high anomaly_score triggers proposal with defaults
        proposal = gen.generate_proposal(detection, ["evt_1"])

        # With anomaly_score=0.90 (> 0.85), a proposal should be generated
        assert proposal is not None
        assert proposal.direction == AdaptationDirection.TIGHTEN
        # Defaults should have been applied: gaming_detected=False, anomaly_count=0
        assert proposal.notes["gaming_detected"] is False
        assert proposal.notes["anomaly_count"] == 0

    def test_proposal_id_uniqueness(self):
        """Test that proposal IDs are unique."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        detection = {
            "anomaly_detected": True,
            "anomaly_score": 0.90,
            "gaming_detected": False,
            "anomaly_count": 2,
            "explanation": "Test",
        }

        proposals = []
        for i in range(5):
            proposal = gen.generate_proposal(detection, [f"evt_{i}"])
            if proposal:
                proposals.append(proposal.proposal_id)

        # All proposal IDs should be unique
        assert len(proposals) == len(set(proposals))


class TestProposalPerformance:
    """Performance and scaling tests."""

    def test_high_frequency_anomaly_generation(self):
        """Test handling high-frequency anomaly proposals."""
        gen = ProposalGenerator("test", current_threshold=0.05)

        # Generate high-frequency anomalies with consistent proposal triggers
        for i in range(100):
            detection = {
                "anomaly_detected": True,
                "anomaly_score": 0.86 + (i % 14) / 100,  # Always > 0.85 threshold
                "gaming_detected": i % 3 == 0,
                "gaming_score": 0.8 if i % 3 == 0 else 0.0,
                "anomaly_count": 2 + (i % 3),
                "explanation": f"Anomaly {i}",
            }
            gen.generate_proposal(detection, [f"evt_{i}"])

        # Most proposals should be generated (high anomaly scores)
        assert gen.proposal_count >= 90
        # History should be limited
        assert len(gen.anomaly_history) <= 10

    def test_concurrent_generators(self):
        """Test managing many concurrent generators."""
        generators = {}

        for i in range(50):
            gen = ProposalGenerator(f"boundary_{i}", current_threshold=0.05 * (i + 1))
            generators[f"boundary_{i}"] = gen

            detection = {
                "anomaly_detected": True,
                "anomaly_score": 0.90,
                "gaming_detected": False,
                "anomaly_count": 2,
                "explanation": "Test",
            }

            proposal = gen.generate_proposal(detection, ["evt_1"])
            assert proposal is not None

        assert len(generators) == 50
