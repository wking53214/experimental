# Telemetry Integration Framework

**Status:** Phase 13 — Real Traffic Validation  
**Date:** 2026-11-15

This document describes how to ingest real-world telemetry into the Governor for production validation.

## Overview

The telemetry framework enables:
- **Multi-format ingestion** (Prometheus, JSON, CSV)
- **Metric normalization** across different sources
- **Boundary signal mapping** (telemetry → Governor constraints)
- **Real traffic validation** against simulated performance claims

## Motivation

STATUS.md issue #1: Nothing has run on real traffic. All 756 tests use simulated workloads. The 22% savings claim and drift/rate rules need validation against production metrics.

## Architecture

```
Raw Metrics (Prometheus / JSON / CSV)
       ↓
  MetricAdapter (format-specific parsing)
       ↓
  MetricStream (normalized points + labels)
       ↓
  TelemetryIngestor (accumulate + query)
       ↓
  MetricMapper (metric → boundary signals)
       ↓
  Governor (boundary tightening decisions)
```

## Quick Start

### 1. Ingest Prometheus Metrics

```python
from src.governance.telemetry import TelemetryIngestor, MetricMapper

# Create ingestor
ingestor = TelemetryIngestor()

# Read Prometheus text format
with open("testdata/sample_prometheus.txt") as f:
    prometheus_data = f.read()

# Ingest
streams = ingestor.ingest(prometheus_data, format="prometheus")

# Query latest values
latest = ingestor.get_latest_values()
print(latest)
# Output:
# {
#   'cpu_usage_percent': 45.2,
#   'memory_usage_percent': 72.4,
#   ...
# }
```

### 2. Map to Governor Signals

```python
# Create mapper
mapper = MetricMapper()

# Register metric → boundary mappings
mapper.register_mapping(
    metric_name="cpu_usage_percent",
    boundary_id="cpu_limit",
    aggregation="max",
    threshold=80.0,
)

mapper.register_mapping(
    metric_name="memory_usage_percent",
    boundary_id="memory_limit",
    aggregation="max",
    threshold=85.0,
)

# Map to signals
signals = mapper.map_streams_to_signals(ingestor.get_all_streams())

# signals = {
#   'cpu_limit': (45.2, False),  # value, should_tighten
#   'memory_limit': (72.4, False),
# }
```

### 3. Feed to Governor

```python
from src.governance.governor import Governor

governor = Governor()

for boundary_id, (value, should_tighten) in signals.items():
    if should_tighten:
        governor.propose_tightening(
            boundary_id=boundary_id,
            reason=f"Metric {boundary_id} exceeded threshold: {value:.1f}",
            magnitude=0.1,
        )
```

## Supported Formats

### Prometheus Text Format

```
cpu_usage_percent{job="api",instance="prod-1"} 45.2 1699000000000
memory_bytes{job="api"} 1024000 1699000000000
network_errors_total 42 1699000020000
```

**Features:**
- Automatic label extraction
- Comment support (`# TYPE`, `# HELP`)
- Millisecond timestamps
- Optional timestamp (defaults to now)

**Adapter:** `PrometheusAdapter`

### JSON Format

```json
{
  "metrics": [
    {
      "name": "cpu_usage",
      "type": "gauge",
      "unit": "percent",
      "points": [
        {
          "timestamp": 1699000000,
          "value": 45.2,
          "labels": {"instance": "prod-1"}
        }
      ]
    }
  ]
}
```

**Features:**
- Structured metadata (unit, type)
- Labeled points
- Easy parsing in any language

**Adapter:** `JSONAdapter`

### CSV Format

```csv
timestamp,metric_name,value,unit,instance,job
1699000000,cpu_usage,45.2,percent,prod-1,api
1699000010,cpu_usage,46.1,percent,prod-1,api
```

**Features:**
- Dynamic label columns
- No schema required
- Excel-compatible

**Adapter:** `CSVAdapter`

## Aggregation Functions

When mapping metrics to boundaries, choose an aggregation:

| Function | Use Case | Example |
|----------|----------|---------|
| `max` | Peak resource usage | CPU spikes |
| `mean` | Average load | Sustained CPU |
| `min` | Minimum acceptable | Available capacity |
| `p95` | Percentile (tunable) | Latency SLOs |
| `latest` | Current value | Real-time signals |

```python
mapper.register_mapping(
    metric_name="latency_ms",
    boundary_id="latency_slo",
    aggregation="p95",
    threshold=200.0,  # Tighten if p95 > 200ms
)
```

## Real-World Integration Examples

### Example 1: Validate Drift Rule Against Production

Hypothesis from rounds 1-13: A drift rule with `drift_factor=1.5` catches runaways without false-positiving on legitimate slowdowns.

```python
import json
from src.governance.telemetry import TelemetryIngestor, MetricMapper
from src.governance.governor import Governor

# 1. Ingest production query latencies (from your metrics database)
ingestor = TelemetryIngestor()
with open("prod_query_latencies.csv") as f:
    streams = ingestor.ingest(f.read(), format="csv")

# 2. Map to boundary signals
mapper = MetricMapper()
mapper.register_mapping(
    metric_name="query_latency_p95_ms",
    boundary_id="query_deadline",
    aggregation="p95",
    threshold=None,  # No auto-tighten, just signal
)
signals = mapper.map_streams_to_signals(streams)

# 3. Extract time series for analysis
latency_stream = ingestor.get_stream("query_latency_p95_ms")
latencies = [p.value for p in latency_stream.points]

# 4. Validate drift rule: does it detect runs but not legitimate slowdowns?
from src.governance.governor import TrapPolicy
policy = TrapPolicy(drift_factor=1.5)

# Analyze: where does drift rule trigger?
for i in range(1, len(latencies)):
    drift = (latencies[i] - latencies[i-1]) / latencies[i-1]
    if abs(drift) > 0.5:  # 50% jump
        print(f"Potential runaway at {i}: {drift:.1%} increase")
```

### Example 2: Multi-Metric Anomaly Detection

Combine multiple metrics to detect problems:

```python
# Ingest multi-metric telemetry
ingestor = TelemetryIngestor()
prometheus_data = """
cpu_usage 85.2 1699000000000
memory_usage 92.1 1699000000000
requests_per_sec 1200.5 1699000000000
error_rate_percent 2.3 1699000000000
"""
ingestor.ingest(prometheus_data, format="prometheus")

# Map each metric to a signal
mapper = MetricMapper()
mapper.register_mapping("cpu_usage", "compute", threshold=80.0)
mapper.register_mapping("memory_usage", "memory", threshold=85.0)
mapper.register_mapping("error_rate_percent", "errors", threshold=1.0)

signals = mapper.map_streams_to_signals(ingestor.get_all_streams())

# Decision: tighten only if multiple metrics signal
tighten_count = sum(1 for _, should_tighten in signals.values() if should_tighten)
if tighten_count >= 2:
    print("Anomaly detected across multiple dimensions")
    # Trigger Governor tightening
```

### Example 3: Time-Series Replay Against Governor

Replay a recorded production time series through the Governor to validate behavior:

```python
from src.governance.governor import Governor

# Load time-series data
ingestor = TelemetryIngestor()
with open("production_traces.json") as f:
    streams = ingestor.ingest(json.load(f), format="json")

# Time-ordered replay
latency_stream = ingestor.get_stream("latency_ms")
governor = Governor()

results = {
    "tightenings": 0,
    "false_positives": 0,
    "decisions": [],
}

for point in latency_stream.points:
    # Simulate: is this latency anomalous?
    is_anomalous = point.value > 150.0  # Example threshold
    
    if is_anomalous:
        result = governor.propose_tightening(
            boundary_id="latency_boundary",
            reason=f"Latency spike: {point.value:.1f}ms",
            magnitude=0.1,
        )
        results["tightenings"] += 1
        results["decisions"].append({
            "timestamp": point.timestamp,
            "value": point.value,
            "decision": result,
        })

# Analyze: did tightening help? Did false positives waste budget?
print(f"Tightenings: {results['tightenings']}")
print(f"Budget used: {results['tightenings'] * 10}%")
```

## API Reference

### TelemetryIngestor

```python
ingestor = TelemetryIngestor()

# Ingest data
streams = ingestor.ingest(data, format="prometheus")  # CSV, JSON also supported

# Query
stream = ingestor.get_stream("metric_name")
latest_values = ingestor.get_latest_values()
all_streams = ingestor.get_all_streams()

# Filter by time
filtered = ingestor.filter_by_time_range(start_ts=..., end_ts=...)
```

### MetricMapper

```python
mapper = MetricMapper()

# Register mappings
mapper.register_mapping(
    metric_name="cpu_usage",
    boundary_id="cpu_limit",
    aggregation="max",
    threshold=80.0,
)

# Generate signals
signals = mapper.map_streams_to_signals(streams)
# Returns: {boundary_id: (aggregated_value, should_tighten), ...}
```

### MetricStream

```python
stream = MetricStream(name="cpu_usage", metric_type="gauge", unit="percent")

# Add points (automatically sorted by timestamp)
stream.add_point(point)

# Query
latest_point = stream.latest()
start_ts, end_ts = stream.time_range()
print(len(stream.points))
```

## Sample Data

Three sample datasets are included:

- `testdata/sample_prometheus.txt` — Prometheus format (API server metrics)
- `testdata/sample_metrics.json` — JSON format (database metrics)
- `testdata/sample_metrics.csv` — CSV format (time-series with labels)

To use:

```python
from src.governance.telemetry import TelemetryIngestor

ingestor = TelemetryIngestor()

# Load any format
with open("testdata/sample_prometheus.txt") as f:
    ingestor.ingest(f.read(), format="prometheus")

# Or
import json
with open("testdata/sample_metrics.json") as f:
    ingestor.ingest(json.load(f), format="json")

# Or
with open("testdata/sample_metrics.csv") as f:
    ingestor.ingest(f.read(), format="csv")
```

## Extending with Custom Adapters

To add support for a new format:

```python
from src.governance.telemetry import MetricAdapter, MetricStream

class CustomAdapter(MetricAdapter):
    def parse(self, data):
        # Convert your format to MetricStream list
        return [MetricStream(...), ...]
    
    def validate(self, data):
        # Return (bool, error_msg)
        return (True, "")

# Register
ingestor = TelemetryIngestor()
ingestor.register_adapter("custom", CustomAdapter())

# Use
streams = ingestor.ingest(data, format="custom")
```

## Next Steps

1. **Connect to your metrics source** (Prometheus, CloudWatch, Datadog, etc.)
2. **Define boundary mappings** for your system (CPU → limit, latency → SLO, etc.)
3. **Replay production traces** through Governor
4. **Validate drift/rate rules** against real workloads
5. **Measure savings** (compare tightening decisions to retrospective analysis)

## Related Documents

- [STATUS.md](STATUS.md) — Issue #1: Real traffic validation
- [THREAT_MODEL.md](THREAT_MODEL.md) — Telemetry poisoning (T2, T15)
- [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md) — Production integration
