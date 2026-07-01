#!/usr/bin/env python3
"""
One-time helper: authorize a Google account and print a Drive refresh token.

You run this ONCE on your laptop. It opens a browser, you log in with the
account whose Drive should own the uploaded interviews (your Stanford or
personal account), and it prints the three env vars to set on the server.

Prereqs:
  1. In Google Cloud Console (same project as your service account), go to
     APIs & Services -> Credentials -> Create credentials -> OAuth client ID.
       - If asked, configure the OAuth consent screen: User type "External",
         add your own email under "Test users".
       - Application type: "Desktop app".
     Download the client secret JSON.
  2. Make sure the Google Drive API is enabled in that project.

Usage:
    python scripts/get_oauth_token.py /path/to/client_secret.json
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    client_secret_path = sys.argv[1]

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    # Opens a browser; log in with the account that should OWN the files.
    creds = flow.run_local_server(
        port=0,
        access_type="offline",     # ensures we get a refresh token
        prompt="consent",
        authorization_prompt_message="Opening browser to authorize Drive access…",
    )

    print("\n" + "=" * 70)
    print("SUCCESS ✅  Set these environment variables on the server (Render):")
    print("=" * 70)
    print(f"GOOGLE_OAUTH_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 70)
    print("Also keep GDRIVE_FOLDER_ID set to your target Drive folder id.")
    print("You can delete the service-account env var (GOOGLE_SERVICE_ACCOUNT_JSON);")
    print("OAuth takes priority when these three are present.\n")


if __name__ == "__main__":
    main()
