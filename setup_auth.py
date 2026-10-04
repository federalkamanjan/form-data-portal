"""One-time administrator setup: approve read-only Google Forms access.

Run this ONCE on your own computer (not on the hosted app):

    python setup_auth.py                          # asks for client ID and secret
    python setup_auth.py --client-secrets client_secret.json   # or use the downloaded file

A browser window opens. Sign in with the Google account that OWNS the forms and approve
read-only access. The script then prints the three values to paste into the app's Secrets.

Your OAuth client in Google Cloud must be of type "Desktop app".
"""
from __future__ import annotations

import argparse
import getpass
import sys

from portal.auth.credentials import SCOPES, SECRETS_SECTION, TOKEN_URI


def _client_config_from_prompt(client_id: str | None) -> dict:
    client_id = client_id or input("OAuth client ID: ").strip()
    client_secret = getpass.getpass("OAuth client secret (hidden as you type): ").strip()
    if not client_id or not client_secret:
        sys.exit("Both the client ID and the client secret are needed.")
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="One-time Google Forms approval for the portal.")
    parser.add_argument("--client-secrets", help="Path to the client_secret.json downloaded from Google Cloud.")
    parser.add_argument("--client-id", help="OAuth client ID (the secret is then asked for privately).")
    args = parser.parse_args()

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        sys.exit("Please run `pip install -r requirements.txt` first.")

    if args.client_secrets:
        flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, SCOPES)
    else:
        flow = InstalledAppFlow.from_client_config(_client_config_from_prompt(args.client_id), SCOPES)

    # offline + consent forces Google to hand back a refresh token every time.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        sys.exit(
            "Google did not return a refresh token. Remove this app at "
            "https://myaccount.google.com/permissions and run the setup again."
        )

    print("\nApproved. Paste this into the app's Secrets (Streamlit Cloud > Settings > Secrets),")
    print("and into .streamlit/secrets.toml for local use. Keep it private.\n")
    print(f"[{SECRETS_SECTION}]")
    print(f'client_id = "{creds.client_id}"')
    print(f'client_secret = "{creds.client_secret}"')
    print(f'refresh_token = "{creds.refresh_token}"')


if __name__ == "__main__":
    main()
