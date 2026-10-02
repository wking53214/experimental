"""
Configuration Management for Anomaly Detection and Governance.

Provides:
- Default and per-boundary detector configuration
- Configuration validation
- Dynamic configuration updates
- Configuration serialization
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any
import json


@dataclass
class DetectorConfig:
    """Configuration for AdaptiveAnomalyDetector."""

    # Learning parameters
    learning_window: int = 5  # Observations before baseline locks
    burn_in_period: int = 25  # Observations requiring multi-signal confirmation

    # Baseline learning
    min_observations_for_baseline: int = 5  # Minimum to initialize baseline
    baseline_update_enabled: bool = False  # Lock baseline after learning_window

    # Thresholds
    anomaly_threshold: float = 0.85  # Post burn-in threshold
    burn_in_threshold: float = 0.70  # Burn-in phase threshold
    min_signals_for_detection: int = 2  # Required exceeded signals

    # Signal-specific thresholds
    deviation_threshold: float = 0.35
    acceleration_threshold: float = 0.5
    persistence_threshold: float = 0.3
    variance_spike_threshold: float = 0.2

    # Window sizes
    value_history_window: int = 20
    anomaly_history_window: int = 10

    # Detection strategy
    adaptive_burn_in: bool = True  # Adapt threshold based on observed variance

    def validate(self) -> bool:
        """Validate configuration consistency."""
        issues = []

        if self.learning_window < 3:
            issues.append("learning_window must be >= 3")
        if self.burn_in_period < self.learning_window:
            issues.append("burn_in_period must be >= learning_window")
        if not (0 < self.anomaly_threshold <= 1.0):
            issues.append("anomaly_threshold must be in (0, 1]")
        if not (0 < self.burn_in_threshold <= 1.0):
            issues.append("burn_in_threshold must be in (0, 1]")
        if self.min_signals_for_detection < 1:
            issues.append("min_signals_for_detection must be >= 1")

        if issues:
            print("Configuration validation errors:")
            for issue in issues:
                print(f"  - {issue}")
            return False
        return True


@dataclass
class GovernorConfig:
    """Configuration for Governor system."""

    # Core parameters
    use_semantic_layer: bool = True
    adaptive_threshold_enabled: bool = True
    anomaly_detection_enabled: bool = True

    # Detector configurations per boundary type
    detector_configs: Dict[str, DetectorConfig] = field(default_factory=dict)
    default_detector_config: DetectorConfig = field(default_factory=DetectorConfig)

    # Proposal parameters
    tighten_factor: float = 0.9  # Multiply limit by this to tighten
    loosen_factor: float = 1.1  # Multiply limit by this to loosen

    # Constraint-set management
    max_constraint_set_size: int = 100
    constraint_growth_warning_threshold: int = 80

    def get_detector_config(self, boundary_id: str) -> DetectorConfig:
        """Get detector configuration for a boundary, with fallback to default."""
        if boundary_id in self.detector_configs:
            return self.detector_configs[boundary_id]
        return self.default_detector_config

    def set_detector_config(self, boundary_id: str, config: DetectorConfig) -> None:
        """Set detector configuration for a specific boundary."""
        if config.validate():
            self.detector_configs[boundary_id] = config
        else:
            raise ValueError(f"Invalid configuration for boundary {boundary_id}")

    def validate(self) -> bool:
        """Validate entire governance configuration."""
        issues = []

        if not self.default_detector_config.validate():
            issues.append("Default detector config is invalid")

        for boundary_id, config in self.detector_configs.items():
            if not config.validate():
                issues.append(f"Detector config for {boundary_id} is invalid")

        if not (0 < self.tighten_factor < 1.0):
            issues.append("tighten_factor must be in (0, 1)")
        if self.loosen_factor <= 1.0:
            issues.append("loosen_factor must be > 1")

        if issues:
            print("Configuration validation errors:")
            for issue in issues:
                print(f"  - {issue}")
            return False
        return True


class ConfigManager:
    """Manages configuration loading, validation, and updates."""

    def __init__(self, default_config: Optional[GovernorConfig] = None):
        self.config = default_config or GovernorConfig()

    def load_from_json(self, json_str: str) -> bool:
        """Load configuration from JSON string."""
        try:
            data = json.loads(json_str)

            # Load default detector config
            if "default_detector_config" in data:
                detector_data = data["default_detector_config"]
                self.config.default_detector_config = DetectorConfig(**detector_data)

            # Load boundary detector configs
            if "detector_configs" in data:
                configs_data = data["detector_configs"]
                self.config.detector_configs = {}
                for bid, cfg_data in configs_data.items():
                    self.config.detector_configs[bid] = DetectorConfig(**cfg_data)

            # Load governor config (skip detector_configs which we already handled)
            for key, value in data.items():
                if key not in ("default_detector_config", "detector_configs"):
                    if hasattr(self.config, key):
                        setattr(self.config, key, value)

            return self.config.validate()
        except Exception as e:
            print(f"Error loading configuration: {e}")
            return False

    def load_from_file(self, filepath: str) -> bool:
        """Load configuration from JSON file."""
        try:
            with open(filepath, 'r') as f:
                return self.load_from_json(f.read())
        except Exception as e:
            print(f"Error reading configuration file: {e}")
            return False

    def save_to_json(self) -> str:
        """Export configuration to JSON string."""
        config_dict = {}

        # Save detector configs
        config_dict["default_detector_config"] = asdict(self.config.default_detector_config)
        config_dict["detector_configs"] = {
            bid: asdict(cfg) for bid, cfg in self.config.detector_configs.items()
        }

        # Save governor config (non-detector fields)
        config_dict["use_semantic_layer"] = self.config.use_semantic_layer
        config_dict["adaptive_threshold_enabled"] = self.config.adaptive_threshold_enabled
        config_dict["anomaly_detection_enabled"] = self.config.anomaly_detection_enabled
        config_dict["tighten_factor"] = self.config.tighten_factor
        config_dict["loosen_factor"] = self.config.loosen_factor
        config_dict["max_constraint_set_size"] = self.config.max_constraint_set_size
        config_dict["constraint_growth_warning_threshold"] = self.config.constraint_growth_warning_threshold

        return json.dumps(config_dict, indent=2)

    def save_to_file(self, filepath: str) -> bool:
        """Save configuration to JSON file."""
        try:
            with open(filepath, 'w') as f:
                f.write(self.save_to_json())
            return True
        except Exception as e:
            print(f"Error saving configuration: {e}")
            return False

    def update_detector_config(self, boundary_id: str, **kwargs) -> bool:
        """Update specific detector configuration fields."""
        config = self.config.get_detector_config(boundary_id)

        # Create a copy to avoid modifying shared defaults
        updated_config = DetectorConfig(**asdict(config))

        # Apply updates
        for key, value in kwargs.items():
            if hasattr(updated_config, key):
                setattr(updated_config, key, value)
            else:
                print(f"Unknown detector config field: {key}")
                return False

        # Validate and store
        if updated_config.validate():
            if boundary_id == "default":
                self.config.default_detector_config = updated_config
            else:
                self.config.set_detector_config(boundary_id, updated_config)
            return True
        return False
