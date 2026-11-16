from datetime import timedelta

DOMAIN = "epark"
BASE_URL = "https://api.e-park.dk"
TOKEN_URL = f"{BASE_URL}/connect/token"
SCOPES = "openid offline_access"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_DEFAULT_RECIPIENT_EMAIL = "default_recipient_email"
CONF_DEFAULT_RECIPIENT_PHONE = "default_recipient_phone"

DATA_POOLS = "pools"
DATA_ACTIVE_PERMITS = "active_permits"
DATA_PLATES = "plates"

UPDATE_INTERVAL = timedelta(minutes=5)

SERVICE_ISSUE_PERMIT = "issue_permit"
SERVICE_STOP_PERMIT = "stop_permit"
SERVICE_LOOKUP_PLATE = "lookup_plate"

ATTR_LICENSE_PLATE = "license_plate"
ATTR_DURATION_MINUTES = "duration_minutes"
ATTR_EMAIL = "email"
ATTR_PHONE = "phone"
ATTR_NOTE = "note"
ATTR_POOL_ID = "pool_id"
ATTR_REFERENCE_NUMBER = "reference_number"
