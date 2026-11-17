from __future__ import annotations

import pytest

from roadiefm.auth.access import decide_access, decide_bootstrap
from roadiefm.config import AccessMode, Settings
from roadiefm.plex.server import connection_priority
from roadiefm.plex.tv import Connection, PlexAccount
from roadiefm.security.crypto import DecryptionError, Signer, TokenCipher

KEY = b"k" * 32


# --- crypto -------------------------------------------------------------------------


def test_cipher_roundtrip_and_purpose_binding() -> None:
    cipher = TokenCipher(KEY)
    enc = cipher.encrypt("plex-token", purpose="a")
    assert "plex-token" not in enc
    assert cipher.decrypt(enc, purpose="a") == "plex-token"
    with pytest.raises(DecryptionError):
        cipher.decrypt(enc, purpose="b")
    with pytest.raises(DecryptionError):
        TokenCipher(b"x" * 32).decrypt(enc, purpose="a")
    with pytest.raises(DecryptionError):
        cipher.decrypt("garbage", purpose="a")


def test_cipher_uses_fresh_nonces() -> None:
    cipher = TokenCipher(KEY)
    assert cipher.encrypt("t", purpose="p") != cipher.encrypt("t", purpose="p")


def test_signer() -> None:
    signer = Signer(KEY)
    signed = signer.sign("123", now=1000)
    assert signer.unsign(signed, max_age=60, now=1030) == "123"
    assert signer.unsign(signed, max_age=60, now=1100) is None  # expired
    assert signer.unsign(signed.replace("123", "124"), max_age=60, now=1030) is None
    assert signer.unsign("nonsense", max_age=60) is None
    assert Signer(b"x" * 32).unsign(signed, max_age=60, now=1030) is None


# --- config -------------------------------------------------------------------------


def make_settings(**kw: object) -> Settings:
    base: dict[str, object] = {"app_secret_key": "s" * 40, "database_url": None}
    return Settings(**(base | kw))  # type: ignore[arg-type]


def test_database_url_from_parts_escapes_password() -> None:
    s = make_settings(postgres_password="p@ss/word", postgres_host="db")
    assert s.sqlalchemy_url == "postgresql+psycopg://roadiefm:p%40ss%2Fword@db:5432/roadiefm"
    assert s.libpq_url.startswith("postgresql://")


def test_database_url_variants() -> None:
    s = make_settings(database_url="postgres://u:p@h/d")
    assert s.sqlalchemy_url == "postgresql+psycopg://u:p@h/d"


def test_client_identifier_is_stable_and_secret_dependent() -> None:
    a = make_settings().plex_client_identifier
    assert a == make_settings().plex_client_identifier
    assert a != make_settings(app_secret_key="t" * 40).plex_client_identifier


def test_app_url() -> None:
    s = make_settings(app_url="https://roadie.example.com/")
    assert s.app_url == "https://roadie.example.com"
    assert s.secure_cookies
    assert s.app_origin == "https://roadie.example.com"
    with pytest.raises(ValueError, match="APP_URL"):
        make_settings(app_url="roadie.example.com")


# --- access policy ------------------------------------------------------------------

ACCOUNT = PlexAccount(id=1, uuid="u", username="Roadie", email="roadie@example.com")


def test_bootstrap() -> None:
    assert decide_bootstrap(ACCOUNT, None).make_admin
    assert decide_bootstrap(ACCOUNT, "roadie").make_admin  # case-insensitive username
    assert decide_bootstrap(ACCOUNT, "ROADIE@example.com").allowed
    assert not decide_bootstrap(ACCOUNT, "someone-else").allowed


@pytest.mark.parametrize(
    ("mode", "allowlist", "approved", "accessible", "expected"),
    [
        (AccessMode.OPEN, [], set(), None, True),
        (AccessMode.ALLOWLIST, ["roadie"], set(), None, True),
        (AccessMode.ALLOWLIST, ["roadie@example.com"], set(), None, True),
        (AccessMode.ALLOWLIST, ["other"], set(), None, False),
        (AccessMode.SERVERS, [], {"srv1"}, {"srv1", "srv2"}, True),
        (AccessMode.SERVERS, [], {"srv1"}, {"srv2"}, False),
        (AccessMode.SERVERS, [], set(), set(), False),
    ],
)
def test_decide_access(
    mode: AccessMode,
    allowlist: list[str],
    approved: set[str],
    accessible: set[str] | None,
    expected: bool,
) -> None:
    decision = decide_access(
        ACCOUNT,
        is_admin=False,
        mode=mode,
        allowlist=allowlist,
        approved_server_ids=approved,
        accessible_server_ids=accessible,
    )
    assert decision.allowed is expected
    assert not decision.make_admin


def test_admin_always_allowed() -> None:
    decision = decide_access(
        ACCOUNT,
        is_admin=True,
        mode=AccessMode.ALLOWLIST,
        allowlist=[],
        approved_server_ids=set(),
        accessible_server_ids=None,
    )
    assert decision.allowed


# --- connection priority ------------------------------------------------------------


def conn(uri: str, **kw: object) -> Connection:
    return Connection.model_validate(
        {"protocol": "https", "address": "x", "port": 32400, "uri": uri} | kw
    )


def test_connection_priority() -> None:
    conns = [
        conn("relay", relay=True),
        conn("remote"),
        conn("local-v6", local=True, IPv6=True),
        conn("local-http", local=True, protocol="http"),
        conn("local", local=True),
    ]
    ordered = [c.uri for c in sorted(conns, key=connection_priority)]
    assert ordered == ["local", "local-http", "local-v6", "remote", "relay"]
