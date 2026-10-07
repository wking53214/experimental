"""
Phase 9B: Integrate Generative Anomaly Detection with DetectorPipeline

Adds multivariate Gaussian detection layer to existing DetectorPipeline,
creating a two-tier detection system:
  Tier 1: Multi-metric statistical anomalies (current system)
  Tier 2: Multivariate distribution anomalies (generative detection)

Both signals feed into a composite anomaly score.
"""

from typing import Dict, Optional
from src.governance.metrics import DetectorPipeline as BaseDetectorPipeline
from src.governance.multivariate import GenerativeAnomalyDetector


class HybridDetectorPipeline(BaseDetectorPipeline):
    """
    DetectorPipeline enhanced with generative anomaly detection.

    Runs both traditional multi-metric detection and multivariate Gaussian
    detection, combining signals for robust coverage of known and novel attacks.
    """

    def __init__(self, boundary_id: str, history_window: int = 100,
                 mahalanobis_threshold: Optional[float] = None):
        """Initialize hybrid detector."""
        super().__init__(boundary_id, history_window)
        self.generative_detector = GenerativeAnomalyDetector(
            boundary_id,
            min_observations=20,
            mahalanobis_threshold=mahalanobis_threshold
        )

    def ingest_metrics(self, timestamp: float, metrics: Dict[str, float]) -> None:
        """Ingest metrics into both detection layers."""
        # Layer 1: Traditional multi-metric detection
        super().ingest_metrics(timestamp, metrics)

        # Layer 2: Generative detection
        self.generative_detector.ingest_observation(metrics)

    def detect_anomalies(self) -> Dict:
        """
        Detect anomalies using both layers, return composite result.

        Returns:
            Dict with both traditional and generative detection results
        """
        # Layer 1: Traditional detection
        traditional = super().detect_anomalies()

        # Layer 2: Generative detection
        latest = self.stream.get_latest()
        latest_metrics = latest[1] if latest else {}
        generative = self.generative_detector.detect_anomaly(latest_metrics)

        # Composite scoring
        traditional_score = traditional.get("anomaly_score", 0.0)
        generative_score = generative.get("anomaly_score", 0.0)

        # Each layer keeps its own decision rule; alarm if either fires. A shared
        # score cutoff would override the traditional layer's stricter threshold.
        combined_score = max(traditional_score, generative_score)
        anomaly_detected = bool(traditional.get("anomaly_detected", False)
                                or generative.get("anomaly_detected", False))

        return {
            "anomaly_detected": anomaly_detected,
            "anomaly_score": combined_score,
            "traditional_score": traditional_score,
            "generative_score": generative_score,
            "traditional_detection": traditional,
            "generative_detection": generative,
            "detection_method": "hybrid",
            "mahalanobis_distance": generative.get("mahalanobis_distance", 0.0),
            "explanation": f"Traditional: {traditional.get('explanation', 'N/A')} | Generative: {generative.get('reason', 'N/A')}"
        }

    def get_detector_summary(self) -> Dict:
        """Get summary of both detection layers."""
        return {
            "boundary_id": self.boundary_id,
            "traditional_detector": {
                "baseline_locked": self.baseline_locked,
                "observation_count": self.observation_count,
                "baseline_stats": self.baseline.copy() if self.baseline else None,
            },
            "generative_detector": self.generative_detector.get_model_summary(),
        }
