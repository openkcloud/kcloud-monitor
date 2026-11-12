# KCloud Monitor

> Unified Monitoring Platform for AI Semiconductors (v2)

KCloud Monitor is a FastAPI-based REST API service for unified monitoring of AI semiconductors (GPU, NPU) and cloud infrastructure (Kubernetes, OpenStack), covering resources and power attribution.

![GitHub license](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Python version](https://img.shields.io/badge/python-3.12-green.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.119%2B-teal.svg)
![API Version](https://img.shields.io/badge/API-v2%20scaffold-orange)

[![Korean](https://img.shields.io/badge/lang-한국어-red)](README.md)
[![English](https://img.shields.io/badge/lang-English-blue)](README_EN.md)

## ⚠️ Current Status: v2 Scaffold

The v1 API (prototype) has been retired (`docs/temp/02-decisions/design_contracts.md` §1).
This codebase currently defines the **v2 routing, authentication, and path structure only** —
every endpoint returns a **stub response** (`status: not_implemented`) that carries its
definition, planned data sources, and design references.

- The v1 implementation (DCGM/Kepler/IPMI query logic, exporters, etc.) remains available in git history for reuse during actual implementation.
- Path source of truth: `docs/temp/04-reference/sample_api.md` (Monitor 81 + Resource-Map 8) plus `docs/temp/01-domain-plans/openkcloud_storage_ceph_plan.md` (S1–S10) = **99 canonical routes** (+4 aliases, +3 auth).

## API Structure (`/api/v2`)

| Area | Path | Description | Count |
|------|------|-------------|-------|
| Clusters | `/clusters/{c}` | Cluster summary, topology, power [P1] | 5 |
| Nodes & Hardware | `/clusters/{c}/nodes/{n}/*` | Node metrics, power [P2], IPMI sensors [P3] | 13 |
| Accelerators & Partitions | `.../accelerators/{id}/*` | Unified GPU/NPU, partitions (MIG/vGPU/slice) [P4·P5] | 12 (+2 aliases) |
| Storage (Ceph) | `/clusters/{c}/storage/*` | Rook-Ceph S1–S10 (new v2 domain) | 10 |
| OpenStack | `/clusters/{c}/openstack/*` | Projects, hypervisors, VMs, power attribution [P6] | 13 |
| Workloads | `/clusters/{c}/workloads/*` | Pods/Containers/Namespaces [P7] | 11 (+2 aliases) |
| Workloads (Global) | `/workloads/*` | Portal-facing global entry (`_links.canonical`) | 11 |
| Monitoring | `/monitoring/*` | Cross-cluster power [P8], timeseries, SSE streams | 10 |
| Export | `/export/*` | Power/metrics/report export | 3 |
| Resource-Map | `/resource-map/*` | Resource lineage ledger (GPU→VM→Pod), discovery | 8 |
| System | `/system/*` | Health, version, self metrics (public, functional) | 3 |
| Auth | `/auth/*` | JWT issuance (dev-only until API Gateway) | 3 |

## Quick Start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py --port 8000
```

```bash
# Health check (public)
curl http://localhost:8000/api/v2/system/health

# Login → token
curl -X POST http://localhost:8000/api/v2/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"changeme"}'

# Inspect a stub response (definition, data sources, design refs)
curl -H "Authorization: Bearer <TOKEN>" \
  http://localhost:8000/api/v2/clusters/mgmt/nodes/w1/accelerators
```

- Swagger UI: http://localhost:8000/docs
- Tests: `pytest tests/ -v` (validates the 106-route inventory, auth, stub contract)

## Target v2 Architecture (to be implemented)

| Backend | Purpose | Status |
|---------|---------|--------|
| **Mimir** (PromQL) | Central metrics — per-cluster Alloy remote_write | config placeholder |
| **PostgreSQL** | Resource-map ledger (resource lineage) | config placeholder |
| **Redis (+Streams)** | Cache + inter-service event bus | config placeholder |
| **OpenStack API / libvirt** | VM mapping, GPU passthrough, power attribution | config placeholder |

## License

This project is distributed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

```
Copyright 2025 OpenKCloud Community

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
```

## Contact

- **Development**: OpenKCloud Community
- **Issues**: [GitHub Issues](https://github.com/openkcloud/kcloud-monitor/issues)

---

**KCloud Monitor v2 (scaffold)** | Unified Monitoring Platform for AI Semiconductors
