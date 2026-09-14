"""Tests for scripts/apple_dev_token.py (Apple Music developer token generator)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "apple_dev_token.py"
_spec = importlib.util.spec_from_file_location("apple_dev_token", _SCRIPT)
apple_dev_token = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("apple_dev_token", apple_dev_token)
_spec.loader.exec_module(apple_dev_token)

TEAM_ID = "TEAM123456"
KEY_ID = "KEY9876543"


@pytest.fixture()
def p8_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")


@pytest.fixture()
def public_key(p8_key: str) -> str:
    key = serialization.load_pem_private_key(p8_key.encode("utf-8"), password=None)
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def test_token_decodes_with_expected_header_and_claims(p8_key: str, public_key: str) -> None:
    token = apple_dev_token.build_developer_token(TEAM_ID, KEY_ID, p8_key, now=1_700_000_000)
    header = pyjwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == KEY_ID
    claims = pyjwt.decode(token, public_key, algorithms=["ES256"], options={"verify_exp": False})
    assert claims["iss"] == TEAM_ID
    assert claims["iat"] == 1_700_000_000
    assert claims["exp"] == 1_700_000_000 + apple_dev_token.MAX_TTL_S
    assert "origin" not in claims


def test_origin_claim_included_when_given(p8_key: str, public_key: str) -> None:
    token = apple_dev_token.build_developer_token(
        TEAM_ID, KEY_ID, p8_key, origins=["http://10.0.0.5:8123"], now=1_700_000_000
    )
    claims = pyjwt.decode(token, public_key, algorithms=["ES256"], options={"verify_exp": False})
    assert claims["origin"] == ["http://10.0.0.5:8123"]


def test_rejects_bad_ids_and_ttl(p8_key: str) -> None:
    with pytest.raises(ValueError):
        apple_dev_token.build_developer_token("SHORT", KEY_ID, p8_key)
    with pytest.raises(ValueError):
        apple_dev_token.build_developer_token(TEAM_ID, "SHORT", p8_key)
    with pytest.raises(ValueError):
        apple_dev_token.build_developer_token(TEAM_ID, KEY_ID, p8_key, ttl_s=apple_dev_token.MAX_TTL_S + 1)
