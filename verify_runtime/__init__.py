"""verify-runtime: config-driven verification gate engine + plugin API."""
from verify_runtime.core import Context, RunResult, Target, EvalResult
from verify_runtime import ai

__version__ = "1.0.0"
__all__ = ["Context", "RunResult", "Target", "EvalResult", "ai", "__version__"]
