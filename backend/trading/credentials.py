"""
Encrypted storage for Binance API key/secret pairs.

The secret is encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using a key
derived deterministically from the project's `SECRET_KEY` env var via PBKDF2.
This means:

* Rotating SECRET_KEY rotates the encryption — any stored secrets become
  unrecoverable. That's by design: SECRET_KEY rotation is treated as a full
  re-keying event. Document this clearly to the operator.
* The DB row alone (without SECRET_KEY) is useless to an attacker.

The api_key itself is NOT encrypted — it's not secret by itself; it's only
useful when paired with the secret.
"""
import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken

from config import settings
from database import get_db


_SALT = b"wohub-trading-fernet-v1"   # static salt; rotation handled by SECRET_KEY


def _fernet() -> Fernet:
    """Derive a Fernet key from SECRET_KEY using PBKDF2-HMAC-SHA256."""
    raw = settings.secret_key.encode("utf-8")
    key = hashlib.pbkdf2_hmac("sha256", raw, _SALT, 200_000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("decryption failed — SECRET_KEY changed or DB tampered") from e


def add_credential(label: str, env: str, api_key: str, api_secret: str) -> int:
    if env not in ("testnet", "mainnet"):
        raise ValueError(f"env must be 'testnet' or 'mainnet', got {env!r}")
    if env == "mainnet" and settings.insecure_defaults():
        raise ValueError(
            "拒绝在不安全的默认配置下新建主网凭据：" +
            "、".join(settings.insecure_defaults()) +
            " 仍为默认值。请设置强随机的 SECRET_KEY 与 APP_PASSWORD 后重启。"
        )
    if not api_key or not api_secret:
        raise ValueError("api_key and api_secret must be non-empty")
    enc = encrypt_secret(api_secret)
    db = get_db(settings.db_path)
    try:
        cursor = db.execute(
            "INSERT INTO trading_credentials (label, env, api_key, api_secret_enc, enabled) "
            "VALUES (?, ?, ?, ?, 1)",
            (label, env, api_key, enc),
        )
        db.commit()
        return cursor.lastrowid
    finally:
        db.close()


def list_credentials() -> list[dict]:
    """Returns rows without the encrypted secret — for UI display only."""
    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            "SELECT id, label, env, api_key, enabled, created_at "
            "FROM trading_credentials ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def get_credential(credential_id: int) -> tuple[str, str, str] | None:
    """Returns (env, api_key, api_secret) for the given credential, decrypted.

    Returns None if not found or disabled. The caller MUST not log the secret.
    """
    db = get_db(settings.db_path)
    try:
        row = db.execute(
            "SELECT env, api_key, api_secret_enc, enabled "
            "FROM trading_credentials WHERE id = ?",
            (credential_id,),
        ).fetchone()
    finally:
        db.close()
    if not row or not row["enabled"]:
        return None
    return row["env"], row["api_key"], decrypt_secret(row["api_secret_enc"])


def delete_credential(credential_id: int) -> bool:
    db = get_db(settings.db_path)
    try:
        cursor = db.execute(
            "DELETE FROM trading_credentials WHERE id = ?",
            (credential_id,),
        )
        db.commit()
        return cursor.rowcount > 0
    finally:
        db.close()


def set_enabled(credential_id: int, enabled: bool) -> bool:
    db = get_db(settings.db_path)
    try:
        cursor = db.execute(
            "UPDATE trading_credentials SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, credential_id),
        )
        db.commit()
        return cursor.rowcount > 0
    finally:
        db.close()
