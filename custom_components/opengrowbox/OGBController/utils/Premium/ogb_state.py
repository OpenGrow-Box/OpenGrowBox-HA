from cryptography.fernet import Fernet, InvalidToken
from datetime import datetime
import json
import logging
import os

_LOGGER = logging.getLogger(__name__)

def _get_secure_path(hass, filename: str) -> str:
    """Returns a secure file path relative to the Home Assistant configuration."""
    subdir = hass.config.path(".ogb_premium")
    os.makedirs(subdir, exist_ok=True)
    return os.path.join(subdir, filename)

async def _load_or_create_key(hass):
    """Loads or creates an encryption key."""
    key_path = _get_secure_path(hass, 'ogb_premium_secret.key')

    def write_key(path):
        key = Fernet.generate_key()
        with open(path, 'wb') as f:
            f.write(key)
        return key

    def read_key(path):
        with open(path, 'rb') as f:
            return f.read()

    if not os.path.exists(key_path):
        key = await hass.async_add_executor_job(write_key, key_path)
        _LOGGER.debug("New encryption key generated")
    else:
        key = await hass.async_add_executor_job(read_key, key_path)
        _LOGGER.debug("Encryption key loaded")

    return key

def _write_file(path, data: bytes):
    """Writes bytes to a file."""
    with open(path, 'wb') as f:
        f.write(data)

def _read_file(path: str) -> bytes:
    """Reads bytes from a file."""
    with open(path, 'rb') as f:
        return f.read()

async def _remove_state_file(hass, room: str = None):
    """Deletes the saved encrypted state file(s). If room is None, deletes all."""
    try:
        subdir = hass.config.path(".ogb_premium")
        if not os.path.exists(subdir):
            return

        if room:
            # nur eine bestimmte Raumdatei löschen
            file_path = os.path.join(subdir, f"ogb_premium_state_{room.lower()}.enc")
            if os.path.exists(file_path):
                await hass.async_add_executor_job(os.remove, file_path)
                _LOGGER.debug(f"Premium file for room '{room}' deleted")
        else:
            # alle Premium-Dateien löschen
            for fname in os.listdir(subdir):
                if fname.startswith("ogb_premium_state_") and fname.endswith(".enc"):
                    file_path = os.path.join(subdir, fname)
                    await hass.async_add_executor_job(os.remove, file_path)
                    _LOGGER.debug(f"Premium file '{fname}' deleted")
    except Exception as e:
        _LOGGER.error(f"Error while deleting Premium file(s): {e}")

async def _save_state_securely(hass, state_data: dict, room: str):
    """Saves Premium data securely (encrypted)."""
    try:
        data_to_save = state_data.copy()

        # Serialize datetime objects
        for key, value in data_to_save.items():
            if isinstance(value, datetime):
                data_to_save[key] = value.isoformat()
        
        data_to_save["saved_at"] = datetime.now().isoformat()
        data_to_save["version"] = "1.0"
        _LOGGER.warning(f"SAVED DATA: {data_to_save}")

        key = await _load_or_create_key(hass)
        fernet = Fernet(key)
        encoded = json.dumps(data_to_save, indent=2).encode()
        encrypted = fernet.encrypt(encoded)

        file_path = _get_secure_path(hass, f"ogb_premium_state_{room.lower()}.enc")
        await hass.async_add_executor_job(_write_file, file_path, encrypted)
        _LOGGER.debug(f"{room} - User session securely saved")

    except Exception as e:
        _LOGGER.error(f"Error while saving state: {e}")
        raise

async def _load_state_securely(hass, room:str):
    """Loads and decrypts saved state data."""
    try:
        file_path = _get_secure_path(hass, f"ogb_premium_state_{room.lower()}.enc")
        if not os.path.exists(file_path):
            return None

        key = await _load_or_create_key(hass)
        fernet = Fernet(key)

        encrypted = await hass.async_add_executor_job(_read_file, file_path)
        decrypted = fernet.decrypt(encrypted)
        state_data = json.loads(decrypted.decode())

        # Parse datetime fields
        for key, value in state_data.items():
            if isinstance(value, str) and key.endswith('_at'):
                try:
                    state_data[key] = datetime.fromisoformat(value)
                except ValueError:
                    try:
                        state_data[key] = datetime.fromtimestamp(float(value))
                    except (ValueError, TypeError):
                        _LOGGER.error(f"Could not parse datetime field {key}: {value}")
                        state_data[key] = None
        return state_data

    except InvalidToken:
        _LOGGER.warning("Invalid encryption key or tampered state file – state will be reset")
        await _remove_state_file(hass)
        return None

    except json.JSONDecodeError:
        _LOGGER.warning("Corrupted state file – state will be reset")
        await _remove_state_file(hass)
        return None

    except Exception as e:
        _LOGGER.error(f"Error while loading state: {e}")
        await _remove_state_file(hass)
        return None
