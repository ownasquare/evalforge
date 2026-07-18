"""API-only Streamlit dashboard for EvalForge."""

from evalforge.dashboard.client import ApiClient, ApiError

__all__ = ["ApiClient", "ApiError"]
