import json
import logging
from pathlib import Path

import ee

from app.config import settings

logger = logging.getLogger(__name__)


def initialize_ee() -> str:
    """Initialize Earth Engine. Returns mode used: 'service_account' | 'oauth' | 'failed'."""
    sa_key_path = settings.ee_service_account_key.strip()

    if sa_key_path:
        path = Path(sa_key_path)
        if not path.is_file():
            logger.warning("EE_SERVICE_ACCOUNT_KEY set but file not found: %s", sa_key_path)
        else:
            try:
                with path.open("r", encoding="utf-8") as fh:
                    sa_email = json.load(fh).get("client_email")
                if not sa_email:
                    raise ValueError("client_email missing from service account JSON")
                creds = ee.ServiceAccountCredentials(sa_email, str(path))
                ee.Initialize(credentials=creds, project=settings.gee_project)
                logger.info("Earth Engine initialized via service account (%s)", sa_email)
                return "service_account"
            except Exception as exc:
                logger.warning("Service account init failed (%s); falling back to OAuth", exc)

    try:
        ee.Initialize(project=settings.gee_project)
        return "oauth"
    except Exception as exc:
        logger.warning("Earth Engine OAuth init failed: %s", exc)
        return "failed"
