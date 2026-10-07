"""
Phase 9B: Integrate Generative Anomaly Detection with DetectorPipeline

Adds multivariate Gaussian detection layer to existing DetectorPipeline,
creating a two-tier detection system:
  Tier 1: Multi-metric statistical anomalies (current system)
  Tier 2: Multivariate distribution anomalies (generative detection)

Both signals feed into a composite anomaly score.
"""

from collections import deque
from typing import Dict, Optional
from src.governance.metrics import DetectorPipeline as BaseDetectorPipeline
from src.governance.multivariate import GenerativeAnomalyDetector
from src.governance.phase9d_concept_drift import ConceptDriftDetector
from src.governance.phase9f_temporal import TemporalShiftDetector


class HybridDetectorPipeline(BaseDetectorPipeline):
    """
    DetectorPipeline enhanced with generative anomaly detection.

    Runs both traditional multi-metric detection and multivariate Gaussian
    detection, combining signals for robust coverage of known and novel attacks.
    """

    def __init__(self, boundary_id: str, history_window: int = 100,
                 mahalanobis_threshold: Optional[float] = None,
                 temporal_shift: bool = False, temporal_smoothing: float = 0.2,
                 reanchor_on_drift: bool = True, baseline_observations: int = 15):
        """Initialize hybrid detector."""
        super().__init__(boundary_id, history_window, baseline_observations)
        self.generative_detector = GenerativeAnomalyDetector(
            boundary_id,
            min_observations=20,
            mahalanobis_threshold=mahalanobis_threshold
        )
        # Optional third layer: catches small shifts sustained over many steps, which
        # the per-step layers miss. Off by default.
        self.temporal_detector = (
            TemporalShiftDetector(smoothing=temporal_smoothing) if temporal_shift else None)
        # The temporal layer's reference is frozen, so legitimate long-term drift would
        # eventually look like an attack. The drift detector re-anchors it when it judges
        # a change gradual; abrupt shifts are held (the layer keeps alarming) until a
        # person calls acknowledge_shift().
        self.drift_detector = ConceptDriftDetector() if temporal_shift else None
        # True: follow gradual drift (fewer false alarms on legitimate change, but a slow
        # attack longer than the drift window can be absorbed). False: strict frozen reference.
        self.reanchor_on_drift = reanchor_on_drift
        self._recent = deque(maxlen=100)
        self._drift_reanchors = 0

    def ingest_metrics(self, timestamp: float, metrics: Dict[str, float]) -> None:
        """Ingest metrics into both detection layers."""
        # Layer 1: Traditional multi-metric detection
        super().ingest_metrics(timestamp, metrics)

        # Layer 2: Generative detection
        self.generative_detector.ingest_observation(metrics)

        # Layer 3 (optional): temporal shift detection
        self._temporal_result = (
            self.temporal_detector.update(metrics) if self.temporal_detector else None)
        if self.temporal_detector:
            self._recent.append(dict(metrics))
            self.drift_detector.update(metrics)
            if self.drift_detector.reanchor_count > self._drift_reanchors:
                self._drift_reanchors = self.drift_detector.reanchor_count
                if self.reanchor_on_drift:
                    self.temporal_detector.reanchor(list(self._recent))

    def acknowledge_shift(self) -> None:
        """A person has validated a flagged sudden shift: accept the current state as normal."""
        if self.drift_detector:
            self.drift_detector.acknowledge()
            self._drift_reanchors = self.drift_detector.reanchor_count
            self.temporal_detector.reanchor(list(self._recent))

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
        temporal = getattr(self, "_temporal_result", None)
        if temporal:
            combined_score = max(combined_score, temporal["anomaly_score"])
        anomaly_detected = bool(traditional.get("anomaly_detected", False)
                                or generative.get("anomaly_detected", False)
                                or (temporal or {}).get("anomaly_detected", False))

        return {
            "anomaly_detected": anomaly_detected,
            "anomaly_score": combined_score,
            "traditional_score": traditional_score,
            "generative_score": generative_score,
            "traditional_detection": traditional,
            "generative_detection": generative,
            "temporal_detection": temporal,
            "drift_status": self.drift_detector.get_drift_status() if self.drift_detector else None,
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


class GenerativePipeline:
    """Generative layer only, with the same ingest/detect interface as HybridDetectorPipeline.

    Each observation is scored against the model BEFORE it is added to it (the hybrid scores after,
    which lets the point partly explain itself). On real 30-metric telemetry this is the only layer
    of the hybrid that does not alarm on nearly every step; see docs/BASELINE_COMPARISON.md.
    """

    def __init__(self, boundary_id: str, mahalanobis_threshold: Optional[float] = None, **_ignored):
        self.boundary_id = boundary_id
        self.generative_detector = GenerativeAnomalyDetector(
            boundary_id, min_observations=20, mahalanobis_threshold=mahalanobis_threshold)
        self._last: Dict = {"anomaly_detected": False, "anomaly_score": 0.0}

    def ingest_metrics(self, timestamp: float, metrics: Dict[str, float]) -> None:
        result = self.generative_detector.detect_anomaly(dict(metrics))
        self.generative_detector.ingest_observation(dict(metrics))
        self._last = {
            "anomaly_detected": bool(result.get("anomaly_detected", False)),
            "anomaly_score": float(result.get("anomaly_score", 0.0)),
            "generative_detection": result,
        }

    def detect_anomalies(self) -> Dict:
        return dict(self._last)
