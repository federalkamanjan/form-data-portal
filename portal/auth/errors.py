"""Admin-facing auth errors.

These are different from per-link failures: when one of these is raised the whole
run cannot proceed, and the message tells the administrator what to do.
"""


class AuthSetupError(RuntimeError):
    """The OAuth secrets are missing, incomplete, or still contain placeholders."""


class AuthTokenError(RuntimeError):
    """Google refused the stored refresh token (expired, revoked, or wrong client)."""


SETUP_HINT = (
    "Run `python setup_auth.py` on your own computer, approve read-only access to Google Forms, "
    "then paste the three values it prints into the app's Secrets."
)

TOKEN_EXPIRED_MESSAGE = (
    "Google no longer accepts the saved sign-in for this portal. It has expired or been revoked. "
    "The administrator needs to re-run the one-time setup. " + SETUP_HINT
)
