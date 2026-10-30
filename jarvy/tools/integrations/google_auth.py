from __future__ import annotations

import asyncio
import json
from typing import Any

KEYRING_SERVICE = "jarvy.google.workspace"
KEYRING_USER = "default"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


class GoogleCredentialStore:
    """Store refresh credentials in the OS keyring; never write token files to the repo."""

    def _keyring(self) -> Any:
        try:
            import keyring
        except ImportError as exc:
            raise RuntimeError(
                "Google integrations require: pip install '.[integrations]'"
            ) from exc
        return keyring

    def _secure_keyring(self) -> Any:
        keyring = self._keyring()
        backend = keyring.get_keyring()
        backend_name = backend.__class__.__module__.lower()
        if "plaintext" in backend_name or "fail" in backend_name or backend.priority <= 0:
            raise RuntimeError(
                "No secure OS keyring is available. Configure Keychain, Credential Manager, "
                "or Secret Service before connecting Google."
            )
        return keyring

    def connected(self) -> bool:
        try:
            return bool(self._secure_keyring().get_password(KEYRING_SERVICE, KEYRING_USER))
        except Exception:
            return False

    def save(self, credentials_json: str) -> None:
        self._secure_keyring().set_password(KEYRING_SERVICE, KEYRING_USER, credentials_json)

    def delete(self) -> bool:
        keyring = self._secure_keyring()
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
            return True
        except Exception:
            return False

    def _credentials(self) -> Any:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise RuntimeError(
                "Google integrations require: pip install '.[integrations]'"
            ) from exc
        stored = self._keyring().get_password(KEYRING_SERVICE, KEYRING_USER)
        if not stored:
            raise RuntimeError("Google is not connected. Run `jarvy connect-google CLIENT_JSON`.")
        credentials = Credentials.from_authorized_user_info(json.loads(stored), GOOGLE_SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self.save(credentials.to_json())
        if not credentials.valid or not credentials.token:
            raise RuntimeError(
                "Google credentials are invalid; reconnect with `jarvy connect-google`."
            )
        return credentials

    async def access_token(self) -> str:
        # Credential refresh is blocking I/O; protect keyring refresh from concurrent API calls.
        async with self._lock:
            credentials = await asyncio.to_thread(self._credentials)
            return str(credentials.token)

    def __init__(self) -> None:
        self._lock = asyncio.Lock()


def connect_google(client_secrets_file: str) -> None:
    """Run Google's installed-app consent flow and persist the resulting token in keyring."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("Google integrations require: pip install '.[integrations]'") from exc
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, GOOGLE_SCOPES)
    credentials = flow.run_local_server(
        port=0, open_browser=True, access_type="offline", prompt="consent"
    )
    GoogleCredentialStore().save(credentials.to_json())
