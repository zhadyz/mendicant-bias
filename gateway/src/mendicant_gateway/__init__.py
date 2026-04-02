"""
Mendicant Bias V5 — Gateway
============================

FastAPI REST API for the Mendicant Bias intelligence system.

Provides HTTP endpoints for:
  - Health checks and system status
  - Middleware configuration inspection
  - Task classification (SmartTaskRouter)
  - Tool routing (SemanticToolRouter)
  - Quality verification (VerificationMiddleware)
  - Strategy recommendation (AdaptiveLearning)
  - Named agent management

Run with::

    mendicant-gateway

Or programmatically::

    from mendicant_gateway import app
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

Named after Mendicant Bias, the Forerunner Contender-class AI from Halo.
"""

__version__ = "5.0.0"

from mendicant_gateway.app import app

__all__ = ["__version__", "app"]
