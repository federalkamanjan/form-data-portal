import os
import tempfile

from portal.auth import AuthSetupError, AuthTokenError, parse_oauth_config, translate_refresh_error
from portal.auth.credentials import SCOPES, OAuthConfig, load_secrets

GOOD = {
    "google_oauth": {
        "client_id": "123-abc.apps.googleusercontent.com",
        "client_secret": "GOCSPX-realsecretvalue",
        "refresh_token": "1//0gRealRefreshToken",
    }
}


def _raises(exc_type, fn, *args):
    try:
        fn(*args)
    except exc_type as exc:
        return str(exc)
    raise AssertionError(f"expected {exc_type.__name__}")


def test_valid_secrets_parse():
    cfg = parse_oauth_config(GOOD)
    assert cfg.client_id.endswith(".apps.googleusercontent.com")
    assert cfg.refresh_token == "1//0gRealRefreshToken"


def test_missing_section_gives_setup_hint():
    msg = _raises(AuthSetupError, parse_oauth_config, {})
    assert "setup_auth.py" in msg


def test_placeholder_values_rejected():
    bad = {"google_oauth": dict(GOOD["google_oauth"], refresh_token="filled in by setup_auth.py (Milestone 1)")}
    msg = _raises(AuthSetupError, parse_oauth_config, bad)
    assert "refresh token" in msg


def test_missing_key_rejected():
    partial = {"google_oauth": {"client_id": "x.apps.googleusercontent.com", "client_secret": "s"}}
    msg = _raises(AuthSetupError, parse_oauth_config, partial)
    assert "refresh token" in msg


def test_secrets_never_appear_in_repr():
    text = repr(parse_oauth_config(GOOD))
    assert "GOCSPX" not in text and "1//0g" not in text


def test_scopes_are_read_only():
    assert SCOPES and all(s.endswith(".readonly") for s in SCOPES)


def test_invalid_grant_becomes_admin_message():
    err = translate_refresh_error(Exception("invalid_grant: Token has been expired or revoked."))
    assert isinstance(err, AuthTokenError)
    assert "re-run the one-time setup" in str(err)


def test_bad_client_secret_message():
    err = translate_refresh_error(Exception("invalid_client: Unauthorized"))
    assert "client ID or client secret" in str(err)


def test_missing_secrets_file_gives_setup_error():
    msg = _raises(AuthSetupError, load_secrets, "/no/such/secrets.toml")
    assert "No secrets were found" in msg


def test_secrets_file_is_read():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "secrets.toml")
        with open(p, "w") as fh:
            fh.write('[google_oauth]\nclient_id="a.apps.googleusercontent.com"\nclient_secret="s3cret"\nrefresh_token="1//tok"\n')
        cfg = parse_oauth_config(load_secrets(p))
        assert isinstance(cfg, OAuthConfig) and cfg.client_secret == "s3cret"
