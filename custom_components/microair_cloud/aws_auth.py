"""Blocking AWS chain for the Micro-Air cloud path (run in the executor).

Chain: Cognito SRP sign-in -> Cognito Identity federation -> DynamoDB
device list -> (setup only) IoT certificate provisioning.

The app ships two Cognito configuration generations that disagree.
This module tries them in a fixed order and reports which
combination actually worked, so setup can record it and runtime can
reuse the sign-in configuration that worked.

Nothing in this module logs passwords, tokens, session keys or private
keys. Email addresses may be logged (they are the account identity).
"""

from __future__ import annotations

import base64
import json
import logging
import time

import boto3
from botocore.config import Config as BotoConfig

from . import srp
from .const import (
    AWS_REGION,
    DYNAMODB_KEY,
    DYNAMODB_TABLE,
    DYNAMODB_THINGS_ATTR,
    IOT_POLICY_NAME,
    SIGNIN_POOLS,
    federation_attempts,
    logins_key,
)

_LOGGER = logging.getLogger(__name__)

_BOTO_CONFIG = BotoConfig(
    connect_timeout=10,
    read_timeout=15,
    retries={"max_attempts": 2, "mode": "standard"},
)


class AuthFailed(Exception):
    """Cognito rejected the credentials on every sign-in pool."""


class NoDevices(Exception):
    """The account's DynamoDB record lists no thermostats."""


class ChainFailed(Exception):
    """A non-credential step of the AWS chain failed."""

    def __init__(self, stage: str, detail: str = "") -> None:
        super().__init__(f"{stage}: {detail}" if detail else stage)
        self.stage = stage


def _idp_client():
    return boto3.client("cognito-idp", region_name=AWS_REGION, config=_BOTO_CONFIG)


def _identity_client():
    return boto3.client(
        "cognito-identity", region_name=AWS_REGION, config=_BOTO_CONFIG
    )


def sign_in(email: str, password: str) -> tuple[dict, dict]:
    """Try each sign-in pool in order. Returns (tokens, pool_dict)."""
    last_auth_error: srp.SrpAuthError | None = None
    last_chain_error: Exception | None = None
    for pool in SIGNIN_POOLS:
        try:
            tokens = srp.srp_sign_in(
                _idp_client(),
                pool["user_pool_id"],
                pool["client_id"],
                pool["client_secret"],
                email,
                password,
            )
        except srp.SrpAuthError as err:
            _LOGGER.warning(
                "Micro-Air sign-in via %s pool rejected: %s", pool["label"], err
            )
            last_auth_error = err
            continue
        except Exception as err:  # network / service errors are not auth failures
            _LOGGER.warning(
                "Micro-Air sign-in via %s pool errored: %s: %s",
                pool["label"],
                type(err).__name__,
                err,
            )
            last_chain_error = err
            continue
        _LOGGER.info(
            "Micro-Air sign-in succeeded via the %s Cognito pool", pool["label"]
        )
        return tokens, pool
    if last_auth_error is not None:
        raise AuthFailed(str(last_auth_error))
    if last_chain_error is not None:
        raise ChainFailed("signin", str(last_chain_error))
    raise AuthFailed("sign-in failed")


def refresh_session(email: str, pool: dict, refresh_token: str) -> dict:
    """Refresh tokens for a known pool; raises srp.SrpAuthError on failure."""
    return srp.refresh_tokens(
        _idp_client(),
        pool["client_id"],
        pool["client_secret"],
        email,
        refresh_token,
    )


def pool_by_label(label: str) -> dict:
    for pool in SIGNIN_POOLS:
        if pool["label"] == label:
            return pool
    return SIGNIN_POOLS[0]


def federate(id_token: str, pool: dict) -> tuple[dict, dict]:
    """Trade an ID token for temporary AWS credentials.

    Tries the federation combinations for the signed-in pool in order
    (including the IoT_Service hard-coded logins-key variant) and
    returns (credentials, attempt_dict) for the first that succeeds.
    Credentials: {"access_key", "secret_key", "session_token", "expiration"}.
    """
    client = _identity_client()
    last_error: Exception | None = None
    for attempt in federation_attempts(pool):
        logins = {logins_key(attempt["logins_pool_id"]): id_token}
        try:
            identity = client.get_id(
                IdentityPoolId=attempt["identity_pool_id"], Logins=logins
            )
            result = client.get_credentials_for_identity(
                IdentityId=identity["IdentityId"], Logins=logins
            )
        except Exception as err:  # wrong logins key etc. -> try next combo
            _LOGGER.debug(
                "Federation attempt %s failed: %s", attempt["label"], err
            )
            last_error = err
            continue
        creds = result["Credentials"]
        _LOGGER.info(
            "Micro-Air federation succeeded via combination: %s", attempt["label"]
        )
        return (
            {
                "access_key": creds["AccessKeyId"],
                "secret_key": creds["SecretKey"],
                "session_token": creds["SessionToken"],
                "expiration": creds["Expiration"].timestamp(),
            },
            attempt,
        )
    raise ChainFailed("federate", str(last_error) if last_error else "no combo worked")


def _session(creds: dict) -> boto3.session.Session:
    return boto3.session.Session(
        aws_access_key_id=creds["access_key"],
        aws_secret_access_key=creds["secret_key"],
        aws_session_token=creds["session_token"],
        region_name=AWS_REGION,
    )


def list_devices(creds: dict, email: str) -> list[dict]:
    """Read the account's thermostat list from DynamoDB.

    Returns [{"control_type", "serial", "name", "fw_rev"}]. Raises
    NoDevices when the record or its thing list is empty, ChainFailed
    when the read itself fails (bad credentials, wrong role, ...).
    """
    dynamodb = _session(creds).client("dynamodb", config=_BOTO_CONFIG)
    try:
        result = dynamodb.get_item(
            TableName=DYNAMODB_TABLE,
            Key={DYNAMODB_KEY: {"S": email}},
            ConsistentRead=True,
        )
    except Exception as err:
        raise ChainFailed("dynamodb", str(err)) from err
    item = result.get("Item")
    if not item:
        raise NoDevices("no User_Accounts record for this account")
    raw = (item.get(DYNAMODB_THINGS_ATTR) or {}).get("S")
    if not raw:
        raise NoDevices("account record has no thermostat list")
    try:
        things = json.loads(raw).get("Things") or []
    except (ValueError, AttributeError) as err:
        raise ChainFailed("dynamodb", f"unparsable thing list: {err}") from err
    devices = [
        {
            "control_type": str(thing.get("Control_Type", "")),
            "serial": str(thing.get("Serial_number", "")),
            "name": str(thing.get("Name") or thing.get("Serial_number", "")),
            "fw_rev": str(thing.get("FW_Rev", "")),
        }
        for thing in things
        if thing.get("Serial_number")
    ]
    if not devices:
        raise NoDevices("thermostat list is empty")
    return devices


def provision_certificate(creds: dict) -> dict:
    """Mint one IoT client certificate and attach the app policy.

    Mirrors what the app does on first connect (CreateKeysAndCertificate
    + AttachPolicy "MyAndroidPolicy"). Returns {"cert_pem", "key_pem",
    "cert_id", "cert_arn"}. The caller persists and reuses this one
    certificate instead of minting per session like the app does.
    """
    iot = _session(creds).client("iot", config=_BOTO_CONFIG)
    try:
        created = iot.create_keys_and_certificate(setAsActive=True)
        cert_arn = created["certificateArn"]
        iot.attach_policy(policyName=IOT_POLICY_NAME, target=cert_arn)
    except Exception as err:
        raise ChainFailed("iot_provision", str(err)) from err
    _LOGGER.info("Minted a Micro-Air IoT client certificate (id %s)",
                 created["certificateId"])
    return {
        "cert_pem": created["certificatePem"],
        "key_pem": created["keyPair"]["PrivateKey"],
        "cert_id": created["certificateId"],
        "cert_arn": cert_arn,
    }


def _token_email(id_token: str, fallback: str) -> str:
    """Email claim of an ID token — the address exactly as registered.

    Cognito preserves the case used at registration, and the DynamoDB
    account record is keyed by that exact string (an account whose
    address was registered in all caps is keyed by the all-caps
    rendering), so the email as typed cannot be used as the record key.
    """
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return str(claims.get("email") or fallback)
    except Exception:
        return fallback


def validate_account(email: str, password: str) -> dict:
    """Full setup chain up to the device list (blocking; executor only).

    Returns {"tokens", "pool", "federation", "credentials", "devices",
    "account_email"}. "account_email" is the registered (canonical)
    email from the ID token, used as the DynamoDB key and stored in
    the config entry.
    """
    tokens, pool = sign_in(email, password)
    account_email = _token_email(tokens["id_token"], email)
    creds, fed = federate(tokens["id_token"], pool)
    devices = list_devices(creds, account_email)
    return {
        "tokens": tokens,
        "pool": pool,
        "federation": fed,
        "credentials": creds,
        "devices": devices,
        "account_email": account_email,
        "validated_at": time.time(),
    }
