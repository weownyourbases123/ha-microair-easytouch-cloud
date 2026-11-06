"""Constants for the Micro-Air EasyTouch (Cloud) integration.

All cloud identifiers below come from static analysis of the official
Micro-Air "EasyTouch RV" Android app (see README.md). They are app-level
identifiers published inside the app itself — pool IDs, client IDs,
and the client secrets the SRP sign-in requires — not user credentials.
"""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "microair_cloud"

AWS_REGION = "us-east-1"
IOT_ENDPOINT = "al2tvpwq2i0lf-ats.iot.us-east-1.amazonaws.com"
IOT_PORT = 8883
IOT_POLICY_NAME = "MyAndroidPolicy"
DYNAMODB_TABLE = "User_Accounts"
DYNAMODB_KEY = "email_address"
DYNAMODB_THINGS_ATTR = "Thing_list"

# --- Cognito configuration generations -------------------------------------
# The app ships two generations of this configuration that disagree.
# "current" is what the sign-in code path (strings.xml) uses.
# "legacy" is the older awsconfiguration.json generation.
# IoT_Service additionally hard-codes the legacy pool id as the identity
# logins key while using the current identity pool; that combination is
# tried as "iot_service_key". config_flow/aws_auth try these in order and
# record which one worked.

POOL_CURRENT = {
    "label": "current",
    "user_pool_id": "us-east-1_M9Hs9kugG",
    "client_id": "2mfujqa7vidd2td9h08k9m061s",
    "client_secret": "bbq2v6edtn25j0pgdus7tsi4fq93rgiqfq0g0ener8m896o2euu",
    "identity_pool_id": "us-east-1:527ec272-ac1b-4301-8e46-464669790d6e",
}

POOL_LEGACY = {
    "label": "legacy",
    "user_pool_id": "us-east-1_Ah228JNHR",
    "client_id": "15h5f3h4pm3nhpk69ovt3dugcq",
    "client_secret": "18vv7vtbl96rv6pkohsp3t5a6abhu584t8he2a14pa5r6gfhrmbd",
    "identity_pool_id": "us-east-1:a9d9f10a-b65d-488c-b5ea-3cbe2a56f9b9",
}

# Sign-in pools, in the order they are attempted.
SIGNIN_POOLS = [POOL_CURRENT, POOL_LEGACY]


def logins_key(user_pool_id: str) -> str:
    """Cognito identity logins-map key for a user pool."""
    return f"cognito-idp.{AWS_REGION}.amazonaws.com/{user_pool_id}"


# Federation attempts, in order: (identity_pool_id, logins user pool id).
# Setup records which sign-in pool and federation combination worked;
# runtime re-derives the attempts from the recorded sign-in pool and
# tries them in this same order.
def federation_attempts(signin_pool: dict) -> list[dict]:
    """Return ordered federation combinations for a signed-in pool."""
    attempts = [
        {
            "label": f"{signin_pool['label']}",
            "identity_pool_id": signin_pool["identity_pool_id"],
            "logins_pool_id": signin_pool["user_pool_id"],
        },
    ]
    if signin_pool["label"] == "current":
        # IoT_Service.java hard-codes the legacy pool id as the logins key
        # while keeping the current identity pool. Try that combination too.
        attempts.append(
            {
                "label": "iot_service_key",
                "identity_pool_id": POOL_CURRENT["identity_pool_id"],
                "logins_pool_id": POOL_LEGACY["user_pool_id"],
            }
        )
    return attempts


# --- Runtime behaviour ------------------------------------------------------

POLL_INTERVAL = timedelta(seconds=60)
STATUS_WAIT_SECONDS = 25  # max wait for a thermostat status reply per poll

STORAGE_VERSION = 1

# Config-entry data keys
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_SERIAL = "serial"
CONF_CONTROL_TYPE = "control_type"
CONF_DEVICE_NAME = "device_name"
CONF_TOPIC = "topic"
CONF_SIGNIN_LABEL = "signin_config"
CONF_FEDERATION_LABEL = "federation_config"

# Thermostat operating modes (U_Change_Temperature.java enum).
MODE_NAMES = {
    0: "Off",
    1: "Fan Only",
    2: "Cool",
    3: "Heat",
    4: "Furnace",
    5: "Heat Pump",
    6: "Dry",
    7: "Heat Strip",
    8: "Auto",
    9: "Auto Heat Strip",
    10: "Auto Heat Pump",
    11: "Auto Furnace",
    12: "Electric Heat",
}
