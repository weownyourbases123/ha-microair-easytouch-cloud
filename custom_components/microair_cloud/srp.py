"""Cognito USER_SRP_AUTH implementation (vendored, transport via boto3).

Why vendored: the maintained helper libraries for Cognito SRP (warrant,
pycognito) either bundle their own boto3 session handling that fights
Home Assistant's executor model, or are effectively unmaintained. The
SRP math itself is small, standardised (RFC 5054 group 3072 as used by
Amazon Cognito) and stable, so it lives here as one auditable module.
boto3 performs all HTTP transport; this module only computes values.

Security: the account password is used in memory to derive the SRP
verifier values and is never logged or returned.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_lib
import secrets
from datetime import datetime, timezone

# RFC 5054 3072-bit group prime, as used by Cognito.
_N_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74"
    "020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437"
    "4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05"
    "98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB"
    "9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718"
    "3995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D04507A33"
    "A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7"
    "ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864"
    "D87602733EC86A64521F2B18177B200CBBE117577A615D6C770988C0BAD946E2"
    "08E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF"
)
# Verbatim from the AWS SDK bundled in the Micro-Air app (CognitoUser.java HEX_N).

_G = 2
_INFO = b"Caldera Derived Key"


def _get_n() -> int:
    """Return the group prime, validated at import time by length."""
    n = int(_N_HEX, 16)
    if n.bit_length() < 3000:  # pragma: no cover - guards transcription slips
        raise ValueError("SRP group prime looks wrong")
    return n


_N = _get_n()
# k = H(0x00 || N || 0x02), exactly as the AWS SDKs compute it
# (hex string "00" + N_HEX + "0" + G_HEX). Verified against pycognito.
_K = int(
    hashlib.sha256(
        bytes.fromhex("00" + _N_HEX + "0" + format(_G, "x"))
    ).hexdigest(),
    16,
)


def _pad_hex(value: int) -> str:
    """Hex of value with Cognito's padding convention.

    Mirrors the AWS SDKs (and pycognito): make the length even, and if
    the top nibble is 8-F prefix "00" (Java BigInteger byte-array sign
    protection). Values are NOT padded to the full group width — an
    earlier full-width version produced hashes Cognito rejects.
    """
    text = format(value, "x")
    if len(text) % 2:
        text = "0" + text
    elif text[0] in "89abcdefABCDEF":
        text = "00" + text
    return text


_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _cognito_timestamp() -> str:
    """Cognito's timestamp format; the day is NOT zero-padded (required)."""
    now = datetime.now(timezone.utc)
    return (
        f"{_WEEKDAYS[now.weekday()]} {_MONTHS[now.month - 1]} {now.day} "
        f"{now.hour:02d}:{now.minute:02d}:{now.second:02d} UTC {now.year}"
    )


def _hash_hex(*hex_parts: str) -> str:
    digest = hashlib.sha256()
    for part in hex_parts:
        digest.update(bytes.fromhex(part))
    return digest.hexdigest()


def _secret_hash(username: str, client_id: str, client_secret: str) -> str:
    msg = (username + client_id).encode()
    sig = hmac_lib.new(client_secret.encode(), msg, hashlib.sha256).digest()
    return base64.b64encode(sig).decode()


class SrpAuthError(Exception):
    """Raised when Cognito rejects the credentials or challenges oddly."""


def srp_sign_in(client, pool_id: str, client_id: str, client_secret: str,
                username: str, password: str) -> dict:
    """Run USER_SRP_AUTH against a Cognito user pool via a boto3 client.

    Returns {"id_token", "access_token", "refresh_token", "expires_in"}.
    Raises SrpAuthError on any rejection or unexpected challenge.
    """
    pool_name = pool_id.split("_", 1)[1]
    a_private = int.from_bytes(secrets.token_bytes(128), "big")
    a_public = pow(_G, a_private, _N)
    if a_public % _N == 0:
        raise SrpAuthError("SRP ephemeral value invalid, retry")

    auth_params = {"USERNAME": username, "SRP_A": format(a_public, "x")}
    if client_secret:
        auth_params["SECRET_HASH"] = _secret_hash(username, client_id, client_secret)

    try:
        resp = client.initiate_auth(
            ClientId=client_id, AuthFlow="USER_SRP_AUTH", AuthParameters=auth_params
        )
    except client.exceptions.NotAuthorizedException as err:
        raise SrpAuthError("Credentials rejected by Cognito") from err
    except client.exceptions.UserNotFoundException as err:
        raise SrpAuthError("Account not found in this user pool") from err
    except client.exceptions.InvalidParameterException as err:
        raise SrpAuthError(f"Cognito rejected the request: {err}") from err
    except client.exceptions.ResourceNotFoundException as err:
        raise SrpAuthError("User pool client no longer exists") from err

    challenge = resp.get("ChallengeName")
    if challenge != "PASSWORD_VERIFIER":
        raise SrpAuthError(f"Unsupported Cognito challenge: {challenge!r}")
    params = resp["ChallengeParameters"]
    user_id = params["USER_ID_FOR_SRP"]
    salt = int(params["SALT"], 16)
    b_public = int(params["SRP_B"], 16)
    secret_block = params["SECRET_BLOCK"]

    u_value = int(_hash_hex(_pad_hex(a_public), _pad_hex(b_public)), 16)
    if u_value == 0:
        raise SrpAuthError("SRP u value is zero")
    inner = hashlib.sha256(
        f"{pool_name}{user_id}:{password}".encode()
    ).hexdigest()
    x_value = int(_hash_hex(_pad_hex(salt), inner), 16)
    s_value = pow(
        (b_public - _K * pow(_G, x_value, _N)) % _N,
        a_private + u_value * x_value,
        _N,
    )
    # HKDF as Cognito defines it (salt = u, ikm = S, info = Caldera key).
    prk = hmac_lib.new(
        bytes.fromhex(_pad_hex(u_value)),
        bytes.fromhex(_pad_hex(s_value)),
        hashlib.sha256,
    ).digest()
    derived = hmac_lib.new(prk, _INFO + b"\x01", hashlib.sha256).digest()[:16]

    timestamp = _cognito_timestamp()
    signature = base64.b64encode(
        hmac_lib.new(
            derived,
            pool_name.encode()
            + user_id.encode()
            + base64.b64decode(secret_block)
            + timestamp.encode(),
            hashlib.sha256,
        ).digest()
    ).decode()

    # The response USERNAME is the challenge's USERNAME when Cognito
    # supplies one, else the sign-in username (pycognito's behaviour);
    # USER_ID_FOR_SRP is used only inside the key/signature computation.
    internal_username = params.get("USERNAME", username)
    responses = {
        "USERNAME": internal_username,
        "PASSWORD_CLAIM_SECRET_BLOCK": secret_block,
        "TIMESTAMP": timestamp,
        "PASSWORD_CLAIM_SIGNATURE": signature,
    }
    if client_secret:
        responses["SECRET_HASH"] = _secret_hash(
            internal_username, client_id, client_secret
        )

    challenge_kwargs: dict = {
        "ClientId": client_id,
        "ChallengeName": "PASSWORD_VERIFIER",
        "ChallengeResponses": responses,
    }
    # Cognito omits Session in some pool configurations; boto3 rejects an
    # explicit None, so only pass it when the initiate response carried one.
    if resp.get("Session"):
        challenge_kwargs["Session"] = resp["Session"]

    try:
        result = client.respond_to_auth_challenge(**challenge_kwargs)
    except client.exceptions.NotAuthorizedException as err:
        raise SrpAuthError("Credentials rejected by Cognito") from err

    auth = result.get("AuthenticationResult")
    if not auth:
        raise SrpAuthError(
            f"Unexpected post-challenge state: {result.get('ChallengeName')!r}"
        )
    return {
        "id_token": auth["IdToken"],
        "access_token": auth["AccessToken"],
        "refresh_token": auth.get("RefreshToken"),
        "expires_in": auth.get("ExpiresIn", 3600),
    }


def refresh_tokens(client, client_id: str, client_secret: str,
                   username: str, refresh_token: str) -> dict:
    """Exchange a refresh token for fresh ID/access tokens via boto3."""
    params = {"REFRESH_TOKEN": refresh_token}
    if client_secret:
        params["SECRET_HASH"] = _secret_hash(username, client_id, client_secret)
    try:
        result = client.initiate_auth(
            ClientId=client_id, AuthFlow="REFRESH_TOKEN_AUTH", AuthParameters=params
        )
    except client.exceptions.NotAuthorizedException as err:
        raise SrpAuthError("Refresh token rejected") from err
    auth = result.get("AuthenticationResult") or {}
    if "IdToken" not in auth:
        raise SrpAuthError("Refresh did not return tokens")
    return {
        "id_token": auth["IdToken"],
        "access_token": auth["AccessToken"],
        "refresh_token": refresh_token,
        "expires_in": auth.get("ExpiresIn", 3600),
    }
