from fastapi import FastAPI, Request, status, WebSocket, Query
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
from typing import Optional

from app.api.v1 import accelerators, infrastructure, hardware, clusters, monitoring, export, system
from app.api import system as legacy_system, power as legacy_power, cluster as legacy_cluster, gpu as legacy_gpu
from app.models.responses import ErrorResponse, ErrorDetail
from app.services.prometheus import PrometheusException
from app.services.stream import power_stream_handler, metrics_stream_handler
from app.middleware import MetricsMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("AI Accelerator & Infrastructure Monitoring API - Starting up")
    print("API Version: 2.0.0 (7-Domain Architecture)")
    print("Metrics middleware enabled - Prometheus metrics available at /api/v1/system/metrics")
    yield
    print("Application shutdown")

app = FastAPI(
    title="AI Accelerator & Infrastructure Monitoring API",
    description="Unified monitoring API for AI accelerators (GPU, NPU), infrastructure (Nodes, Pods, VMs), and hardware (IPMI)",
    version="2.0.0",
    lifespan=lifespan
)

# ============================================================================
# Middleware
# ============================================================================

# Add metrics middleware for request tracking
app.add_middleware(MetricsMiddleware)

# ============================================================================
# Exception Handlers
# ============================================================================

@app.exception_handler(PrometheusException)
async def prometheus_exception_handler(request: Request, exc: PrometheusException):
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=ErrorResponse(error=ErrorDetail(code="PROMETHEUS_ERROR", message=str(exc))).model_dump(mode='json')
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(error=ErrorDetail(code="VALIDATION_ERROR", message=str(exc))).model_dump(mode='json')
    )

# ============================================================================
# API v1 Routers (7-Domain Architecture)
# ============================================================================

# 1. Accelerators - GPU and NPU monitoring
app.include_router(accelerators.router, prefix="/api/v1", tags=["Accelerators"])

# 2. Infrastructure - Nodes, Pods, Containers, VMs
app.include_router(infrastructure.router, prefix="/api/v1", tags=["Infrastructure"])

# 3. Hardware - Physical hardware (IPMI)
app.include_router(hardware.router, prefix="/api/v1", tags=["Hardware"])

# 4. Clusters - Multi-cluster management
app.include_router(clusters.router, prefix="/api/v1", tags=["Clusters"])

# 5. Monitoring - Cross-domain monitoring (power, timeseries, streaming)
app.include_router(monitoring.router, prefix="/api/v1", tags=["Monitoring"])

# 6. Export - Data export and reporting
app.include_router(export.router, prefix="/api/v1", tags=["Export"])

# 7. System - Health, info, capabilities
app.include_router(system.router, prefix="/api/v1", tags=["System"])

# ============================================================================
# Legacy API Routers (Backward Compatibility - Deprecated)
# ============================================================================
# These will be removed in a future version. Use v1 routes instead.

app.include_router(legacy_system.router, prefix="/api/v1", tags=["Legacy-System"])
app.include_router(legacy_power.router, prefix="/api/v1", tags=["Legacy-Power"])
app.include_router(legacy_cluster.router, prefix="/api/v1", tags=["Legacy-Cluster"])
app.include_router(legacy_gpu.router, prefix="/api/v1", tags=["Legacy-GPU"])

# ============================================================================
# WebSocket Endpoints (Phase 7.3)
# ============================================================================

@app.websocket("/api/v1/monitoring/stream/power")
async def websocket_power_stream(
    websocket: WebSocket,
    cluster: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    interval: int = Query(5, ge=1, le=60)
):
    """
    WebSocket endpoint for real-time power consumption data.

    Query Parameters:
    - cluster: Cluster name filter
    - resource_type: Resource type filter (accelerators, infrastructure)
    - interval: Update interval in seconds (1-60, default: 5)

    Connection Example:
    ```javascript
    const ws = new WebSocket('ws://localhost:8000/api/v1/monitoring/stream/power?interval=5');
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      console.log('Power update:', data);
    };
    ```
    """
    await power_stream_handler(websocket, cluster, resource_type, interval)


@app.websocket("/api/v1/monitoring/stream/metrics")
async def websocket_metrics_stream(
    websocket: WebSocket,
    metric_name: str = Query("utilization"),
    resource_type: Optional[str] = Query(None),
    interval: int = Query(5, ge=1, le=60)
):
    """
    WebSocket endpoint for real-time performance metrics.

    Query Parameters:
    - metric_name: Metric name (utilization, temperature, memory_usage)
    - resource_type: Resource type filter (gpus, npus, nodes)
    - interval: Update interval in seconds (1-60, default: 5)

    Connection Example:
    ```javascript
    const ws = new WebSocket('ws://localhost:8000/api/v1/monitoring/stream/metrics?metric_name=utilization&resource_type=gpus');
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      console.log('Metrics update:', data);
    };
    ```
    """
    await metrics_stream_handler(websocket, metric_name, resource_type, interval)


# ============================================================================
# Root Endpoint
# ============================================================================

@app.get("/")
def read_root():
    return {
        "message": "AI Accelerator & Infrastructure Monitoring API",
        "version": "2.0.0",
        "architecture": "7-Domain Structure",
        "docs": "/docs",
        "domains": {
            "accelerators": "/api/v1/accelerators/*",
            "infrastructure": "/api/v1/infrastructure/*",
            "hardware": "/api/v1/hardware/*",
            "clusters": "/api/v1/clusters/*",
            "monitoring": "/api/v1/monitoring/*",
            "export": "/api/v1/export/*",
            "system": "/api/v1/system/*"
        },
        "legacy_endpoints": {
            "power": "/api/v1/power/* (deprecated)",
            "gpu": "/api/v1/gpu/* (deprecated)",
            "cluster": "/api/v1/cluster/* (deprecated)"
        }
    }
