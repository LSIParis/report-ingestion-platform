# Importer les détecteurs les enregistre dans le registre.
from app.services.alerting.detectors import (  # noqa: F401
    domain_silent,
    never_reported,
    tls_failure,
)
