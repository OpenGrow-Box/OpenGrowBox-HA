"""
OpenGrowBox Premium WebSocket Crypto Module

Handles encryption and decryption of WebSocket messages using AES-GCM.
"""

import base64
import logging
import secrets
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_LOGGER = logging.getLogger(__name__)


class OGBPremWebSocketCrypto:
    """Handles encryption/decryption for secure WebSocket communication."""

    def __init__(self):
        """Initialize crypto handler."""
        self._session_key: Optional[bytes] = None
        self._aes_gcm: Optional[AESGCM] = None

    def set_session_key(self, session_key: bytes) -> bool:
        """
        Set the session key for encryption/decryption.

        Args:
            session_key: 32-byte AES key

        Returns:
            True if key was set successfully
        """
        try:
            if len(session_key) != 32:
                _LOGGER.error(
                    f"Invalid session key length: {len(session_key)}, expected 32"
                )
                return False

            self._session_key = session_key
            self._aes_gcm = AESGCM(session_key)
            return True

        except Exception as e:
            _LOGGER.error(f"Error setting session key: {e}")
            return False

    def clear_session_key(self) -> None:
        """Clear the session key."""
        self._session_key = None
        self._aes_gcm = None

    @property
    def has_key(self) -> bool:
        """Check if a session key is set."""
        return self._aes_gcm is not None

    def safe_b64_decode(self, encoded_data: str) -> bytes:
        """
        Safely decode base64 data with padding correction.

        Args:
            encoded_data: Base64 encoded string

        Returns:
            Decoded bytes
        """
        try:
            # Add padding if necessary
            missing_padding = len(encoded_data) % 4
            if missing_padding:
                encoded_data += "=" * (4 - missing_padding)
            return base64.b64decode(encoded_data)
        except Exception as e:
            _LOGGER.error(f"Base64 decode error: {e}")
            return b""

    def encrypt_message(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Encrypt a message using AES-GCM.

        Args:
            data: Dictionary to encrypt

        Returns:
            Dictionary with encrypted payload
        """
        if not self._aes_gcm:
            _LOGGER.error("Cannot encrypt: no session key set")
            return {}

        try:
            import json

            # Generate random nonce
            nonce = secrets.token_bytes(12)

            # Serialize and encrypt
            plaintext = json.dumps(data).encode("utf-8")
            ciphertext = self._aes_gcm.encrypt(nonce, plaintext, None)

            return {
                "nonce": base64.b64encode(nonce).decode("utf-8"),
                "ciphertext": base64.b64encode(ciphertext).decode("utf-8"),
            }

        except Exception as e:
            _LOGGER.error(f"Encryption error: {e}")
            return {}

    def decrypt_message(self, encrypted_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Decrypt a message using AES-GCM.

        Args:
            encrypted_data: Dictionary with 'nonce' and 'ciphertext'

        Returns:
            Decrypted dictionary
        """
        if not self._aes_gcm:
            _LOGGER.error("Cannot decrypt: no session key set")
            return {}

        try:
            import json

            nonce = base64.b64decode(encrypted_data["nonce"])
            ciphertext = base64.b64decode(encrypted_data["ciphertext"])

            plaintext = self._aes_gcm.decrypt(nonce, ciphertext, None)
            return json.loads(plaintext.decode("utf-8"))

        except Exception as e:
            _LOGGER.error(f"Decryption error: {e}")
            return {}

    def derive_session_key_from_b64(self, session_key_b64: str) -> bool:
        """
        Set session key from base64-encoded string.

        Args:
            session_key_b64: Base64 encoded session key

        Returns:
            True if successful
        """
        try:
            session_key = self.safe_b64_decode(session_key_b64)
            return self.set_session_key(session_key)
        except Exception as e:
            _LOGGER.error(f"Error deriving session key: {e}")
            return False
