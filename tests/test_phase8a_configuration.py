"""
Phase 8A: Configuration Management Tests

Test configuration loading, validation, and updates for production deployments.
"""

import pytest
import json
import tempfile
import os
from src.governance.config import (
    DetectorConfig,
    GovernorConfig,
    ConfigManager,
)


class TestDetectorConfiguration:
    """Test anomaly detector configuration."""

    def test_default_detector_config(self):
        """Test default detector configuration values."""
        config = DetectorConfig()

        assert config.learning_window == 5
        assert config.burn_in_period == 25
        assert config.anomaly_threshold == 0.85
        assert config.min_signals_for_detection == 2

    def test_detector_config_validation(self):
        """Test detector configuration validation."""
        # Valid config
        config = DetectorConfig()
        assert config.validate() is True

        # Invalid: learning_window too small
        config.learning_window = 2
        assert config.validate() is False

        # Invalid: threshold out of range
        config = DetectorConfig()
        config.anomaly_threshold = 1.5
        assert config.validate() is False

    def test_detector_config_custom_thresholds(self):
        """Test custom detector thresholds."""
        config = DetectorConfig(
            anomaly_threshold=0.75,
            burn_in_threshold=0.65,
            deviation_threshold=0.4,
            min_signals_for_detection=3,
        )

        assert config.anomaly_threshold == 0.75
        assert config.burn_in_threshold == 0.65
        assert config.deviation_threshold == 0.4
        assert config.min_signals_for_detection == 3
        assert config.validate() is True


class TestGovernorConfiguration:
    """Test governor-wide configuration."""

    def test_default_governor_config(self):
        """Test default governor configuration."""
        config = GovernorConfig()

        assert config.use_semantic_layer is True
        assert config.adaptive_threshold_enabled is True
        assert config.anomaly_detection_enabled is True
        assert config.tighten_factor == 0.9
        assert config.loosen_factor == 1.1

    def test_governor_config_validation(self):
        """Test governor configuration validation."""
        config = GovernorConfig()
        assert config.validate() is True

        # Invalid: tighten_factor out of range
        config.tighten_factor = 1.2
        assert config.validate() is False

        # Invalid: loosen_factor too small
        config = GovernorConfig()
        config.loosen_factor = 0.9
        assert config.validate() is False

    def test_per_boundary_detector_config(self):
        """Test per-boundary detector configurations."""
        config = GovernorConfig()

        # Get default for unknown boundary
        default = config.get_detector_config("unknown_boundary")
        assert default.learning_window == 5

        # Set custom config for specific boundary
        custom = DetectorConfig(learning_window=10, anomaly_threshold=0.8)
        config.set_detector_config("cpu_boundary", custom)

        # Verify it's used
        retrieved = config.get_detector_config("cpu_boundary")
        assert retrieved.learning_window == 10
        assert retrieved.anomaly_threshold == 0.8

        # Other boundaries still use default
        assert config.get_detector_config("memory_boundary").learning_window == 5


class TestConfigurationManagement:
    """Test configuration manager."""

    def test_json_serialization(self):
        """Test configuration serialization to JSON."""
        config = GovernorConfig()
        config.detector_configs["test_boundary"] = DetectorConfig(
            learning_window=8,
            anomaly_threshold=0.75,
        )

        manager = ConfigManager(config)
        json_str = manager.save_to_json()

        # Verify it's valid JSON
        data = json.loads(json_str)
        assert "default_detector_config" in data
        assert "detector_configs" in data
        assert "test_boundary" in data["detector_configs"]
        assert data["detector_configs"]["test_boundary"]["learning_window"] == 8

    def test_json_deserialization(self):
        """Test configuration loading from JSON."""
        config_json = json.dumps({
            "use_semantic_layer": True,
            "tighten_factor": 0.85,
            "loosen_factor": 1.15,
            "default_detector_config": {
                "learning_window": 7,
                "anomaly_threshold": 0.8,
            },
            "detector_configs": {
                "cpu": {
                    "learning_window": 10,
                    "anomaly_threshold": 0.75,
                },
            },
        })

        manager = ConfigManager()
        assert manager.load_from_json(config_json) is True

        assert manager.config.tighten_factor == 0.85
        assert manager.config.default_detector_config.learning_window == 7
        assert manager.config.get_detector_config("cpu").learning_window == 10

    def test_file_operations(self):
        """Test configuration file save and load."""
        config = GovernorConfig(
            tighten_factor=0.85,
            anomaly_detection_enabled=True,
        )
        config.default_detector_config = DetectorConfig(learning_window=8)

        manager = ConfigManager(config)

        # Save to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = f.name

        try:
            # Save
            assert manager.save_to_file(temp_path) is True
            assert os.path.exists(temp_path)

            # Load into new manager
            new_manager = ConfigManager()
            assert new_manager.load_from_file(temp_path) is True

            # Verify settings transferred
            assert new_manager.config.tighten_factor == 0.85
            assert new_manager.config.default_detector_config.learning_window == 8
        finally:
            # Cleanup
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_update_detector_config(self):
        """Test dynamic detector configuration updates."""
        manager = ConfigManager()

        # Update default config
        assert manager.update_detector_config(
            "default",
            learning_window=12,
            anomaly_threshold=0.78,
        ) is True

        # Get default for unknown boundary (which now uses updated defaults)
        config = manager.config.get_detector_config("some_boundary")
        assert config.learning_window == 12
        assert config.anomaly_threshold == 0.78

    def test_invalid_configuration_rejection(self):
        """Test that invalid configurations are rejected."""
        manager = ConfigManager()

        # Try to set invalid configuration
        assert manager.update_detector_config(
            "boundary",
            learning_window=2,  # Too small
        ) is False

        # Original should be unchanged
        config = manager.config.get_detector_config("boundary")
        assert config.learning_window == 5  # Default


class TestConfigurationIntegration:
    """Test configuration integration with detector."""

    def test_config_values_match_detector_usage(self):
        """Verify configuration values align with detector requirements."""
        config = DetectorConfig()

        # These should match detector's default initialization
        assert config.learning_window > 0
        assert config.burn_in_period >= config.learning_window
        assert 0 < config.anomaly_threshold <= 1.0
        assert config.min_signals_for_detection >= 1

    def test_production_configuration_example(self):
        """Test a realistic production configuration."""
        # Create production-oriented config
        config = GovernorConfig()

        # Default for most boundaries
        config.default_detector_config = DetectorConfig(
            learning_window=10,
            burn_in_period=30,
            anomaly_threshold=0.82,
            min_signals_for_detection=2,
        )

        # Custom configs for critical boundaries
        config.set_detector_config("database_cpu", DetectorConfig(
            learning_window=15,  # Longer learning for databases
            anomaly_threshold=0.75,  # More sensitive
            min_signals_for_detection=2,
        ))

        config.set_detector_config("cache_memory", DetectorConfig(
            learning_window=8,  # Shorter learning for cache
            anomaly_threshold=0.88,  # Less sensitive
            min_signals_for_detection=3,  # Require more agreement
        ))

        # Validate everything
        assert config.validate() is True

        # Verify configurations work as expected
        db_config = config.get_detector_config("database_cpu")
        assert db_config.anomaly_threshold == 0.75

        cache_config = config.get_detector_config("cache_memory")
        assert cache_config.min_signals_for_detection == 3

        default_config = config.get_detector_config("other_boundary")
        assert default_config.anomaly_threshold == 0.82


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
