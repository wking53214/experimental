"""
Tests for telemetry ingestion framework.

Validates adapters, metric mapping, and integration with Governor signals.
"""

import pytest
import json
from datetime import datetime
from src.governance.telemetry import (
    MetricPoint,
    MetricStream,
    PrometheusAdapter,
    JSONAdapter,
    CSVAdapter,
    TelemetryIngestor,
    MetricMapper,
)


class TestMetricPoint:
    """Test basic metric point data structure."""

    def test_metric_point_creation(self):
        """MetricPoint stores timestamp, value, and labels."""
        point = MetricPoint(
            timestamp=1699000000.0,
            value=45.2,
            labels={"job": "api", "instance": "prod-1"},
            source="prometheus",
        )
        assert point.timestamp == 1699000000.0
        assert point.value == 45.2
        assert point.labels["job"] == "api"
        assert point.source == "prometheus"

    def test_metric_point_repr(self):
        """MetricPoint has readable string representation."""
        point = MetricPoint(
            timestamp=1699000000.0,
            value=45.2,
            source="prometheus",
        )
        repr_str = repr(point)
        assert "1699000000" in repr_str
        assert "45.2" in repr_str


class TestMetricStream:
    """Test metric stream collection."""

    def test_stream_creation(self):
        """MetricStream stores name and type."""
        stream = MetricStream(name="cpu_usage", metric_type="gauge", unit="percent")
        assert stream.name == "cpu_usage"
        assert stream.metric_type == "gauge"
        assert stream.unit == "percent"

    def test_stream_add_point(self):
        """Points are added and sorted by timestamp."""
        stream = MetricStream(name="cpu_usage")
        point1 = MetricPoint(timestamp=1699000010.0, value=50.0)
        point2 = MetricPoint(timestamp=1699000000.0, value=45.0)
        point3 = MetricPoint(timestamp=1699000020.0, value=55.0)

        stream.add_point(point1)
        stream.add_point(point2)
        stream.add_point(point3)

        assert len(stream.points) == 3
        assert stream.points[0].value == 45.0
        assert stream.points[1].value == 50.0
        assert stream.points[2].value == 55.0

    def test_stream_latest(self):
        """latest() returns most recent point."""
        stream = MetricStream(name="cpu_usage")
        point1 = MetricPoint(timestamp=1699000000.0, value=45.0)
        point2 = MetricPoint(timestamp=1699000010.0, value=50.0)

        stream.add_point(point1)
        stream.add_point(point2)

        assert stream.latest().value == 50.0

    def test_stream_time_range(self):
        """time_range() returns start and end timestamps."""
        stream = MetricStream(name="cpu_usage")
        stream.add_point(MetricPoint(timestamp=1699000000.0, value=45.0))
        stream.add_point(MetricPoint(timestamp=1699000100.0, value=50.0))

        start, end = stream.time_range()
        assert start == 1699000000.0
        assert end == 1699000100.0


class TestPrometheusAdapter:
    """Test Prometheus text format parsing."""

    def test_prometheus_parse_basic(self):
        """Parse basic Prometheus text format."""
        data = """
cpu_usage_percent{job="app",instance="host1"} 45.2 1699000000000
memory_bytes{job="app"} 1024000 1699000010000
network_errors_total 42 1699000020000
        """.strip()

        adapter = PrometheusAdapter()
        streams = adapter.parse(data)

        assert len(streams) == 3
        cpu_stream = next(s for s in streams if s.name == "cpu_usage_percent")
        assert cpu_stream.points[0].value == 45.2
        assert cpu_stream.points[0].labels["job"] == "app"
        assert cpu_stream.points[0].labels["instance"] == "host1"

    def test_prometheus_parse_no_timestamp(self):
        """Handle Prometheus format without explicit timestamp."""
        data = "cpu_usage_percent 45.2"
        adapter = PrometheusAdapter()
        streams = adapter.parse(data)

        assert len(streams) == 1
        assert streams[0].points[0].value == 45.2
        assert streams[0].points[0].timestamp > 0

    def test_prometheus_parse_skip_comments(self):
        """Comments are skipped."""
        data = """
# TYPE cpu_usage_percent gauge
# HELP cpu_usage_percent CPU usage
cpu_usage_percent 45.2 1699000000000
        """.strip()

        adapter = PrometheusAdapter()
        streams = adapter.parse(data)

        assert len(streams) == 1
        assert streams[0].points[0].value == 45.2

    def test_prometheus_parse_multiple_same_metric(self):
        """Multiple values for same metric are collected."""
        data = """
cpu_usage_percent{instance="host1"} 45.2 1699000000000
cpu_usage_percent{instance="host1"} 46.1 1699000010000
cpu_usage_percent{instance="host1"} 45.9 1699000020000
        """.strip()

        adapter = PrometheusAdapter()
        streams = adapter.parse(data)

        assert len(streams) == 1
        assert len(streams[0].points) == 3
        assert streams[0].points[0].value == 45.2
        assert streams[0].points[2].value == 45.9

    def test_prometheus_validate_success(self):
        """Validate accepts valid Prometheus data."""
        data = "cpu_usage 45.2 1699000000000"
        adapter = PrometheusAdapter()
        is_valid, error = adapter.validate(data)

        assert is_valid
        assert error == ""

    def test_prometheus_validate_failure(self):
        """Validate rejects invalid data."""
        adapter = PrometheusAdapter()

        is_valid, error = adapter.validate("")
        assert not is_valid

        is_valid, error = adapter.validate(123)
        assert not is_valid


class TestJSONAdapter:
    """Test JSON metric format parsing."""

    def test_json_parse_basic(self):
        """Parse basic JSON metric format."""
        data = {
            "metrics": [
                {
                    "name": "cpu_usage",
                    "type": "gauge",
                    "unit": "percent",
                    "points": [
                        {
                            "timestamp": 1699000000,
                            "value": 45.2,
                            "labels": {"instance": "host1"},
                        },
                        {
                            "timestamp": 1699000010,
                            "value": 46.1,
                            "labels": {"instance": "host1"},
                        },
                    ],
                }
            ]
        }

        adapter = JSONAdapter()
        streams = adapter.parse(data)

        assert len(streams) == 1
        assert streams[0].name == "cpu_usage"
        assert len(streams[0].points) == 2
        assert streams[0].points[0].value == 45.2

    def test_json_parse_string(self):
        """Parse JSON from string."""
        data = json.dumps(
            {
                "metrics": [
                    {
                        "name": "memory_bytes",
                        "type": "gauge",
                        "points": [
                            {"timestamp": 1699000000, "value": 1024000}
                        ],
                    }
                ]
            }
        )

        adapter = JSONAdapter()
        streams = adapter.parse(data)

        assert len(streams) == 1
        assert streams[0].name == "memory_bytes"

    def test_json_validate_success(self):
        """Validate accepts valid JSON."""
        data = {"metrics": []}
        adapter = JSONAdapter()
        is_valid, error = adapter.validate(data)

        assert is_valid

    def test_json_validate_failure(self):
        """Validate rejects invalid JSON."""
        adapter = JSONAdapter()

        is_valid, error = adapter.validate({"data": []})
        assert not is_valid


class TestCSVAdapter:
    """Test CSV metric format parsing."""

    def test_csv_parse_basic(self):
        """Parse basic CSV format."""
        data = """timestamp,metric_name,value,unit
1699000000,cpu_usage,45.2,percent
1699000010,cpu_usage,46.1,percent
1699000000,memory_bytes,1024000,bytes"""

        adapter = CSVAdapter()
        streams = adapter.parse(data)

        assert len(streams) == 2
        cpu_stream = next(s for s in streams if s.name == "cpu_usage")
        assert len(cpu_stream.points) == 2
        assert cpu_stream.points[0].value == 45.2

    def test_csv_parse_with_labels(self):
        """Parse CSV with label columns."""
        data = """timestamp,metric_name,value,unit,instance,job
1699000000,cpu_usage,45.2,percent,host1,api
1699000010,cpu_usage,46.1,percent,host1,api"""

        adapter = CSVAdapter()
        streams = adapter.parse(data)

        assert len(streams) == 1
        assert streams[0].points[0].labels["instance"] == "host1"
        assert streams[0].points[0].labels["job"] == "api"

    def test_csv_validate_success(self):
        """Validate accepts valid CSV."""
        data = "timestamp,metric_name,value\n1699000000,cpu,45.2"
        adapter = CSVAdapter()
        is_valid, error = adapter.validate(data)

        assert is_valid

    def test_csv_validate_failure(self):
        """Validate rejects invalid CSV."""
        adapter = CSVAdapter()

        is_valid, error = adapter.validate("")
        assert not is_valid

        is_valid, error = adapter.validate("timestamp,metric_name,value")
        assert not is_valid


class TestTelemetryIngestor:
    """Test main ingestion engine."""

    def test_ingestor_ingest_prometheus(self):
        """Ingest Prometheus format metrics."""
        data = "cpu_usage 45.2 1699000000000\nmemory_usage 60.1 1699000000000"

        ingestor = TelemetryIngestor()
        streams = ingestor.ingest(data, format="prometheus")

        assert len(streams) == 2
        assert ingestor.get_stream("cpu_usage") is not None

    def test_ingestor_ingest_json(self):
        """Ingest JSON format metrics."""
        data = {
            "metrics": [
                {
                    "name": "requests_per_sec",
                    "type": "gauge",
                    "points": [{"timestamp": 1699000000, "value": 1234.5}],
                }
            ]
        }

        ingestor = TelemetryIngestor()
        streams = ingestor.ingest(data, format="json")

        assert len(streams) == 1
        assert ingestor.get_stream("requests_per_sec") is not None

    def test_ingestor_ingest_csv(self):
        """Ingest CSV format metrics."""
        data = """timestamp,metric_name,value,unit
1699000000,disk_usage,80.5,percent
1699000010,disk_usage,81.2,percent"""

        ingestor = TelemetryIngestor()
        streams = ingestor.ingest(data, format="csv")

        assert len(streams) == 1
        assert len(ingestor.get_stream("disk_usage").points) == 2

    def test_ingestor_accumulates_metrics(self):
        """Multiple ingests accumulate metrics."""
        ingestor = TelemetryIngestor()

        ingestor.ingest("cpu_usage 45.2 1699000000000", format="prometheus")
        ingestor.ingest("cpu_usage 46.1 1699000010000", format="prometheus")

        stream = ingestor.get_stream("cpu_usage")
        assert len(stream.points) == 2

    def test_ingestor_get_latest_values(self):
        """get_latest_values() returns current values."""
        ingestor = TelemetryIngestor()
        ingestor.ingest(
            "cpu_usage 45.2 1699000000000\nmemory_usage 1024 1699000000000",
            format="prometheus",
        )

        latest = ingestor.get_latest_values()
        assert latest["cpu_usage"] == 45.2
        assert latest["memory_usage"] == 1024.0

    def test_ingestor_filter_by_time_range(self):
        """Filter metrics by time range."""
        ingestor = TelemetryIngestor()
        ingestor.ingest(
            """cpu_usage 45.2 1699000000000
cpu_usage 46.1 1699000010000
cpu_usage 47.0 1699000020000
cpu_usage 45.5 1699000030000""",
            format="prometheus",
        )

        filtered = ingestor.filter_by_time_range(
            start_ts=1699000005.0, end_ts=1699000025.0
        )

        assert len(filtered) == 1
        assert len(filtered[0].points) == 2  # Only middle 2 points
        assert filtered[0].points[0].value == 46.1

    def test_ingestor_invalid_format_raises(self):
        """Reject unknown format."""
        ingestor = TelemetryIngestor()

        with pytest.raises(ValueError, match="Unknown format"):
            ingestor.ingest("data", format="unknown")

    def test_ingestor_invalid_data_raises(self):
        """Reject invalid data for format."""
        ingestor = TelemetryIngestor()

        # Empty string should fail
        with pytest.raises(ValueError, match="Invalid"):
            ingestor.ingest("", format="prometheus")

        # JSON format with invalid JSON
        with pytest.raises(ValueError, match="Invalid"):
            ingestor.ingest("{broken json", format="json")


class TestMetricMapper:
    """Test metric to boundary signal mapping."""

    def test_mapper_register_mapping(self):
        """Register metric to boundary mapping."""
        mapper = MetricMapper()
        mapper.register_mapping(
            metric_name="cpu_usage",
            boundary_id="cpu_limit",
            aggregation="max",
            threshold=80.0,
        )

        assert "cpu_usage" in mapper.mappings
        assert mapper.mappings["cpu_usage"]["boundary_id"] == "cpu_limit"

    def test_mapper_max_aggregation(self):
        """Map metric with max aggregation."""
        mapper = MetricMapper()
        mapper.register_mapping(
            metric_name="cpu_usage",
            boundary_id="cpu_limit",
            aggregation="max",
            threshold=80.0,
        )

        stream = MetricStream(name="cpu_usage")
        stream.add_point(MetricPoint(timestamp=1699000000.0, value=45.0))
        stream.add_point(MetricPoint(timestamp=1699000010.0, value=75.0))
        stream.add_point(MetricPoint(timestamp=1699000020.0, value=85.0))

        signals = mapper.map_streams_to_signals([stream])

        assert "cpu_limit" in signals
        assert signals["cpu_limit"][0] == 85.0
        assert signals["cpu_limit"][1] is True  # Should tighten

    def test_mapper_mean_aggregation(self):
        """Map metric with mean aggregation."""
        mapper = MetricMapper()
        mapper.register_mapping(
            metric_name="latency_ms",
            boundary_id="latency_slo",
            aggregation="mean",
            threshold=200.0,
        )

        stream = MetricStream(name="latency_ms")
        stream.add_point(MetricPoint(timestamp=1699000000.0, value=100.0))
        stream.add_point(MetricPoint(timestamp=1699000010.0, value=150.0))
        stream.add_point(MetricPoint(timestamp=1699000020.0, value=200.0))

        signals = mapper.map_streams_to_signals([stream])

        assert signals["latency_slo"][0] == pytest.approx(150.0)
        assert signals["latency_slo"][1] is False  # Should not tighten

    def test_mapper_p95_aggregation(self):
        """Map metric with p95 aggregation."""
        mapper = MetricMapper()
        mapper.register_mapping(
            metric_name="response_time",
            boundary_id="response_slo",
            aggregation="p95",
        )

        stream = MetricStream(name="response_time")
        for i in range(100):
            stream.add_point(MetricPoint(timestamp=1699000000.0 + i, value=float(i)))

        signals = mapper.map_streams_to_signals([stream])

        # P95 of 0-99 should be around 95
        assert signals["response_slo"][0] == pytest.approx(95.0, abs=1.0)

    def test_mapper_no_threshold_means_no_tighten_signal(self):
        """Without threshold, should_tighten is always False."""
        mapper = MetricMapper()
        mapper.register_mapping(
            metric_name="disk_usage",
            boundary_id="disk_limit",
            aggregation="max",
            threshold=None,
        )

        stream = MetricStream(name="disk_usage")
        stream.add_point(MetricPoint(timestamp=1699000000.0, value=99.0))

        signals = mapper.map_streams_to_signals([stream])

        assert signals["disk_limit"][1] is False

    def test_mapper_unmapped_metrics_ignored(self):
        """Unmapped metrics are not included in signals."""
        mapper = MetricMapper()
        mapper.register_mapping("cpu_usage", "cpu_limit")

        cpu_stream = MetricStream(name="cpu_usage")
        cpu_stream.add_point(MetricPoint(timestamp=1699000000.0, value=50.0))

        memory_stream = MetricStream(name="memory_usage")
        memory_stream.add_point(MetricPoint(timestamp=1699000000.0, value=60.0))

        signals = mapper.map_streams_to_signals([cpu_stream, memory_stream])

        assert "cpu_limit" in signals
        assert len(signals) == 1


class TestEndToEndIntegration:
    """Integration tests: ingest real-world metrics and map to signals."""

    def test_end_to_end_prometheus_to_signals(self):
        """Ingest Prometheus metrics and map to Governor signals."""
        # Simulate real metric stream
        prometheus_data = """
cpu_usage_percent{instance="prod-1"} 85.2 1699000000000
memory_usage_percent{instance="prod-1"} 92.1 1699000000000
requests_per_sec{instance="prod-1"} 1200.5 1699000000000
        """.strip()

        ingestor = TelemetryIngestor()
        mapper = MetricMapper()

        mapper.register_mapping(
            "cpu_usage_percent", "cpu_boundary", aggregation="max", threshold=80.0
        )
        mapper.register_mapping(
            "memory_usage_percent",
            "memory_boundary",
            aggregation="max",
            threshold=90.0,
        )
        mapper.register_mapping(
            "requests_per_sec", "throughput_boundary", threshold=None
        )

        streams = ingestor.ingest(prometheus_data, format="prometheus")
        signals = mapper.map_streams_to_signals(streams)

        assert signals["cpu_boundary"] == (85.2, True)  # Should tighten
        assert signals["memory_boundary"] == (92.1, True)  # Should tighten
        assert signals["throughput_boundary"][1] is False  # No threshold

    def test_end_to_end_csv_with_labels(self):
        """Ingest CSV with labels, map to signals."""
        csv_data = """timestamp,metric_name,value,unit,job
1699000000,db_connection_pool,45,percent,backend
1699000010,db_connection_pool,52,percent,backend
1699000020,db_connection_pool,58,percent,backend"""

        ingestor = TelemetryIngestor()
        mapper = MetricMapper()

        mapper.register_mapping(
            "db_connection_pool",
            "connection_boundary",
            aggregation="max",
            threshold=50.0,
        )

        streams = ingestor.ingest(csv_data, format="csv")
        signals = mapper.map_streams_to_signals(streams)

        assert signals["connection_boundary"][0] == 58.0
        assert signals["connection_boundary"][1] is True

    def test_end_to_end_multiple_ingest_formats(self):
        """Mix multiple ingestion formats."""
        ingestor = TelemetryIngestor()
        mapper = MetricMapper()

        # Ingest from Prometheus
        ingestor.ingest("cpu 45.0 1699000000000", format="prometheus")

        # Ingest from JSON
        ingestor.ingest(
            {"metrics": [{"name": "memory", "points": [{"timestamp": 1699000000, "value": 60.0}]}]},
            format="json",
        )

        # Ingest from CSV
        ingestor.ingest(
            "timestamp,metric_name,value\n1699000000,disk,75.0",
            format="csv",
        )

        mapper.register_mapping("cpu", "cpu_boundary", threshold=50.0)
        mapper.register_mapping("memory", "memory_boundary", threshold=70.0)
        mapper.register_mapping("disk", "disk_boundary", threshold=80.0)

        all_streams = ingestor.get_all_streams()
        signals = mapper.map_streams_to_signals(all_streams)

        assert len(signals) == 3
        assert signals["cpu_boundary"][1] is False
        assert signals["memory_boundary"][1] is False
        assert signals["disk_boundary"][1] is False
