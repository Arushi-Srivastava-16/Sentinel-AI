from google_auth_oauthlib.flow import InstalledAppFlow
import pathlib

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]

cred = pathlib.Path("secrets/gmail/credentials.json")
tok = pathlib.Path("secrets/gmail/token.json")

flow = InstalledAppFlow.from_client_secrets_file(str(cred), SCOPES)
creds = flow.run_local_server(host="localhost", port=8080)
tok.write_text(creds.to_json(), encoding="utf-8")
print("saved", tok.resolve())
