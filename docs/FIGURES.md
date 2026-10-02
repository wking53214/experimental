# Architecture Diagrams (Phase 12C)

Renderable as Mermaid in GitHub Markdown and most documentation tools.

## Figure 1 — System architecture and authority cut-point

```mermaid
flowchart TD
  P[Immutable Principles] --> B[Versioned Boundaries]
  B --> E[Execution]
  E --> V{Violation?}
  V -->|yes| A[Anomaly / Pattern / Hybrid Detection]
  V -->|no| E
  A --> S[Semantic Filter<br/>registered patterns only]
  S --> PR[Adaptation Proposal]
  PR --> AUTH{Authority Model}
  AUTH -->|TIGHTEN| AUTO[Auto-approve allowed]
  AUTH -->|LOOSEN / DISABLE| HUM[Human review required]
  AUTO --> NV[New Boundary Version]
  HUM --> OP[Operator + Evidence Pack]
  OP -->|approve| NV
  OP -->|reject| B
  NV --> B
```

## Figure 2 — Endurance under sustained pressure

```mermaid
flowchart LR
  subgraph iterations [1000 iterations]
    ATK[Adversary pressure] --> GOV[Governor]
    GOV -->|TIGHTEN 10%| LIM[Limit decreases]
    LIM --> FLOOR{At 20% of original?}
    FLOOR -->|no| ATK
    FLOOR -->|yes| CONT[Further pressure = contained]
  end
```

Conceptual result from multi-seed endurance: min usability ≈ 20.6% of original; residual success rate ≈ 1% under floor-aware scoring.

## Figure 3 — Multi-seed metrics vs targets

```mermaid
barChart
  title Multi-seed means vs targets (schematic)
  x-axis FP Detection EnduranceSuccess MinUsability
  y-axis Value
```

See `results/multiseed_summary.md` for numeric tables (N=10).

## Figure 4 — Hybrid multi-metric dataflow

```mermaid
flowchart LR
  M[Metrics dict] --> H[HybridDetectorPipeline]
  H --> T[Traditional multi-metric]
  H --> G[Generative / Mahalanobis]
  T --> C[Composite score]
  G --> C
  C --> P[detect_from_pipeline / propose]
  P --> AUTH[Authority + floor]
```
