"""
Telemetry ingestion framework for real-world Governor integration.

Enables adapting metrics from any source (Prometheus, CloudWatch, logs, JSON, CSV)
into Governor boundary signals and anomaly detection.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple
import json


@dataclass
class MetricPoint:
    """A single telemetry data point."""
    timestamp: float
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    source: str = ""
    unit: Optional[str] = None

    def __repr__(self) -> str:
        return f"MetricPoint(ts={self.timestamp:.1f}, v={self.value}, src={self.source})"


@dataclass
class MetricStream:
    """A named stream of metric points over time."""
    name: str
    points: List[MetricPoint] = field(default_factory=list)
    metric_type: str = "gauge"  # gauge, counter, histogram
    unit: Optional[str] = None

    def add_point(self, point: MetricPoint) -> None:
        """Add a metric point, maintaining time order."""
        self.points.append(point)
        self.points.sort(key=lambda p: p.timestamp)

    def latest(self) -> Optional[MetricPoint]:
        """Get most recent metric point."""
        return self.points[-1] if self.points else None

    def time_range(self) -> Tuple[Optional[float], Optional[float]]:
        """Get (start_ts, end_ts) of metric stream."""
        if not self.points:
            return None, None
        return self.points[0].timestamp, self.points[-1].timestamp


class MetricAdapter(ABC):
    """Abstract base for metric format adapters."""

    @abstractmethod
    def parse(self, data: Any) -> List[MetricStream]:
        """
        Parse raw data into metric streams.

        Args:
            data: Raw input (dict, str, bytes, file-like)

        Returns:
            List of MetricStream objects
        """
        pass

    @abstractmethod
    def validate(self, data: Any) -> Tuple[bool, str]:
        """
        Validate that data can be parsed.

        Returns:
            (is_valid, error_message)
        """
        pass


class PrometheusAdapter(MetricAdapter):
    """Adapter for Prometheus text format and remote write protocol."""

    def parse(self, data: Any) -> List[MetricStream]:
        """
        Parse Prometheus text format or remote write JSON.

        Text format example:
            cpu_usage_percent{job="app",instance="host1"} 45.2 1699000000000
            memory_bytes{job="app"} 1024000 1699000000000
        """
        streams: Dict[str, MetricStream] = {}

        if isinstance(data, str):
            lines = data.strip().split("\n")
        elif isinstance(data, bytes):
            lines = data.decode("utf-8").strip().split("\n")
        else:
            raise ValueError(f"PrometheusAdapter: unsupported input type {type(data)}")

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            try:
                # Parse: metric_name{labels} value timestamp
                if "{" in line:
                    metric_part, rest = line.split("{", 1)
                    labels_part, value_part = rest.split("}", 1)
                    metric_name = metric_part.strip()

                    # Parse labels
                    labels = {}
                    if labels_part.strip():
                        for pair in labels_part.split(","):
                            k, v = pair.split("=")
                            labels[k.strip()] = v.strip().strip('"')
                else:
                    metric_name = line.split()[0]
                    labels = {}
                    value_part = " ".join(line.split()[1:])

                parts = value_part.split()
                value = float(parts[0])
                timestamp = float(parts[1]) / 1000.0 if len(parts) > 1 else datetime.now().timestamp()

                if metric_name not in streams:
                    streams[metric_name] = MetricStream(
                        name=metric_name,
                        metric_type="gauge",
                    )

                point = MetricPoint(
                    timestamp=timestamp,
                    value=value,
                    labels=labels,
                    source="prometheus",
                )
                streams[metric_name].add_point(point)

            except Exception:
                continue

        return list(streams.values())

    def validate(self, data: Any) -> Tuple[bool, str]:
        try:
            if not isinstance(data, (str, bytes)):
                return False, "Expected str or bytes"
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            if not data.strip():
                return False, "Empty input"
            return True, ""
        except Exception as e:
            return False, str(e)


class JSONAdapter(MetricAdapter):
    """Adapter for JSON metric format."""

    def parse(self, data: Any) -> List[MetricStream]:
        """
        Parse JSON metric format.

        Expected structure:
        {
            "metrics": [
                {
                    "name": "cpu_usage",
                    "type": "gauge",
                    "unit": "percent",
                    "points": [
                        {"timestamp": 1699000000, "value": 45.2, "labels": {...}}
                    ]
                }
            ]
        }
        """
        if isinstance(data, str):
            data = json.loads(data)
        elif isinstance(data, bytes):
            data = json.loads(data.decode("utf-8"))

        streams = []
        for metric_def in data.get("metrics", []):
            stream = MetricStream(
                name=metric_def["name"],
                metric_type=metric_def.get("type", "gauge"),
                unit=metric_def.get("unit"),
            )

            for point_def in metric_def.get("points", []):
                point = MetricPoint(
                    timestamp=point_def["timestamp"],
                    value=point_def["value"],
                    labels=point_def.get("labels", {}),
                    source="json",
                    unit=metric_def.get("unit"),
                )
                stream.add_point(point)

            if stream.points:
                streams.append(stream)

        return streams

    def validate(self, data: Any) -> Tuple[bool, str]:
        try:
            if isinstance(data, (str, bytes)):
                test = json.loads(data if isinstance(data, str) else data.decode("utf-8"))
            else:
                test = data
            if not isinstance(test.get("metrics"), list):
                return False, "Expected 'metrics' key with list value"
            return True, ""
        except Exception as e:
            return False, str(e)


class CSVAdapter(MetricAdapter):
    """Adapter for CSV metric format."""

    def parse(self, data: Any) -> List[MetricStream]:
        """
        Parse CSV format with columns: timestamp,metric_name,value,unit,labels...

        Example:
            timestamp,metric_name,value,unit
            1699000000,cpu_usage,45.2,percent
            1699000010,cpu_usage,46.1,percent
        """
        import csv
        from io import StringIO

        if isinstance(data, bytes):
            data = data.decode("utf-8")

        streams: Dict[str, MetricStream] = {}
        reader = csv.DictReader(StringIO(data))

        for row in reader:
            metric_name = row.get("metric_name", "unknown")
            timestamp = float(row.get("timestamp", 0))
            value = float(row.get("value", 0))
            unit = row.get("unit", None)

            # Extract label columns (anything not in standard columns)
            standard_cols = {"timestamp", "metric_name", "value", "unit"}
            labels = {k: v for k, v in row.items() if k not in standard_cols and v}

            if metric_name not in streams:
                streams[metric_name] = MetricStream(
                    name=metric_name,
                    metric_type="gauge",
                    unit=unit,
                )

            point = MetricPoint(
                timestamp=timestamp,
                value=value,
                labels=labels,
                source="csv",
                unit=unit,
            )
            streams[metric_name].add_point(point)

        return list(streams.values())

    def validate(self, data: Any) -> Tuple[bool, str]:
        try:
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            if not data.strip():
                return False, "Empty input"
            lines = data.strip().split("\n")
            if len(lines) < 2:
                return False, "CSV needs header + at least 1 data row"
            return True, ""
        except Exception as e:
            return False, str(e)


class TelemetryIngestor:
    """Main ingestion engine for telemetry from any source."""

    def __init__(self):
        self.adapters: Dict[str, MetricAdapter] = {
            "prometheus": PrometheusAdapter(),
            "json": JSONAdapter(),
            "csv": CSVAdapter(),
        }
        self.streams: Dict[str, MetricStream] = {}

    def register_adapter(self, name: str, adapter: MetricAdapter) -> None:
        """Register a custom adapter."""
        self.adapters[name] = adapter

    def ingest(self, data: Any, format: str) -> List[MetricStream]:
        """
        Ingest telemetry data in specified format.

        Args:
            data: Raw telemetry data
            format: Format name ('prometheus', 'json', 'csv', or custom)

        Returns:
            List of MetricStream objects
        """
        if format not in self.adapters:
            raise ValueError(f"Unknown format: {format}. Available: {list(self.adapters.keys())}")

        adapter = self.adapters[format]
        is_valid, error = adapter.validate(data)
        if not is_valid:
            raise ValueError(f"Invalid {format} data: {error}")

        streams = adapter.parse(data)
        for stream in streams:
            if stream.name not in self.streams:
                self.streams[stream.name] = stream
            else:
                for point in stream.points:
                    self.streams[stream.name].add_point(point)

        return streams

    def get_stream(self, metric_name: str) -> Optional[MetricStream]:
        """Retrieve a metric stream by name."""
        return self.streams.get(metric_name)

    def get_all_streams(self) -> List[MetricStream]:
        """Get all ingested metric streams."""
        return list(self.streams.values())

    def get_latest_values(self) -> Dict[str, float]:
        """Get latest value for each metric."""
        return {name: stream.latest().value for name, stream in self.streams.items() if stream.latest()}

    def filter_by_time_range(
        self, start_ts: float, end_ts: float
    ) -> List[MetricStream]:
        """Filter all streams to a time range."""
        filtered = []
        for stream in self.streams.values():
            filtered_points = [
                p for p in stream.points if start_ts <= p.timestamp <= end_ts
            ]
            if filtered_points:
                filtered_stream = MetricStream(
                    name=stream.name,
                    metric_type=stream.metric_type,
                    unit=stream.unit,
                )
                filtered_stream.points = filtered_points
                filtered.append(filtered_stream)
        return filtered


class MetricMapper:
    """Maps ingested metrics to Governor boundary signals."""

    def __init__(self):
        self.mappings: Dict[str, Dict[str, Any]] = {}

    def register_mapping(
        self,
        metric_name: str,
        boundary_id: str,
        aggregation: str = "max",
        threshold: Optional[float] = None,
    ) -> None:
        """
        Register a metric → boundary mapping.

        Args:
            metric_name: Name of ingested metric
            boundary_id: Governor boundary ID to signal
            aggregation: How to aggregate points ('max', 'mean', 'p95', etc.)
            threshold: Optional threshold for triggering tightening signal
        """
        self.mappings[metric_name] = {
            "boundary_id": boundary_id,
            "aggregation": aggregation,
            "threshold": threshold,
        }

    def map_streams_to_signals(
        self, streams: List[MetricStream]
    ) -> Dict[str, Tuple[float, bool]]:
        """
        Map metric streams to boundary signals.

        Returns:
            {boundary_id: (aggregated_value, should_tighten)}
        """
        signals = {}

        for stream in streams:
            if stream.name not in self.mappings:
                continue

            mapping = self.mappings[stream.name]
            boundary_id = mapping["boundary_id"]
            aggregation = mapping["aggregation"]
            threshold = mapping["threshold"]

            if not stream.points:
                continue

            values = [p.value for p in stream.points]
            if aggregation == "max":
                agg_value = max(values)
            elif aggregation == "mean":
                agg_value = sum(values) / len(values)
            elif aggregation == "min":
                agg_value = min(values)
            elif aggregation == "p95":
                sorted_vals = sorted(values)
                idx = int(len(sorted_vals) * 0.95)
                agg_value = sorted_vals[idx]
            else:
                agg_value = values[-1]

            should_tighten = threshold is not None and agg_value > threshold
            signals[boundary_id] = (agg_value, should_tighten)

        return signals
