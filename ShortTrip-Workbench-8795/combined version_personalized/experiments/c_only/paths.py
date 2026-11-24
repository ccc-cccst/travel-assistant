"""Shared locations for experiment source and generated run files."""

import os
from pathlib import Path


SOURCE = Path(__file__).resolve().parent
PROJECT = SOURCE.parents[1]
RUN = Path(os.environ.get("SHORTTRIP_C_RUN_DIR") or SOURCE).resolve()


def data_settings():
    """Record the profile inputs that the worker's fresh process will see."""
    return {
        key: os.environ[key] for key in (
            "CHINATRAVEL_PROFILE", "CHINATRAVEL_DATABASE_ROOT", "CHINATRAVEL_ZH_DATABASE",
            "CT_SANDBOX_PROFILE_ROOT", "CT_DYNAMIC_MODE", "CT_CATALOG_PATH",
        )
        if os.environ.get(key)
    }
