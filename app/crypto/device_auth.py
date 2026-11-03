import base64
from typing import Any

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def build_auth_payload(
    session_id: str, pending_id: str, action_id: str, params_sha256: str
) -> bytes:
    """Builds the canonical UTF-8 payload signed by Android Keystore (v1.1):

    accessai-auth-v1|{session_id}|{pending_id}|{action_id}|{params_sha256}
    """
    return f"accessai-auth-v1|{session_id}|{pending_id}|{action_id}|{params_sha256}".encode()


def verify_device_auth(
    device_key: dict[str, Any] | None,
    auth: dict[str, Any] | None,
    session_id: str,
    pending_id: str,
    action_id: str,
    params_sha256: str,
) -> tuple[bool, str]:
    """Verifies ES256 ECDSA device authentication signature against session device_key.

    Parameters:
        device_key: Session's stored device key {kid, alg, public_key, ...}
        auth: Client auth payload {method, kid, alg, signature}
        session_id: Session identifier
        pending_id: Confirmation ID
        action_id: Manifest action name (e.g. transfer_money)
        params_sha256: SHA-256 digest of canonical parameters JSON

    Returns:
        (is_valid, error_reason)
    """
    if not device_key:
        return False, "Session has no device_key registered"

    if not auth:
        return False, "Missing device authentication"

    if auth.get("alg") != "ES256":
        return False, f"Unsupported algorithm '{auth.get('alg')}'; expected 'ES256'"

    if auth.get("kid") != device_key.get("kid"):
        return (
            False,
            f"Key ID mismatch: auth.kid '{auth.get('kid')}' != session.kid '{device_key.get('kid')}'",
        )

    sig_b64 = auth.get("signature")
    if not sig_b64:
        return False, "Missing signature in auth payload"

    pub_b64 = device_key.get("public_key")
    if not pub_b64:
        return False, "Missing public_key in session device_key"

    # Rebuild expected payload
    payload = build_auth_payload(
        session_id=session_id,
        pending_id=pending_id,
        action_id=action_id,
        params_sha256=params_sha256,
    )

    # Load DER SubjectPublicKeyInfo public key
    try:
        pub_der = base64.b64decode(pub_b64)
        public_key = serialization.load_der_public_key(pub_der)
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            return False, "Device public key is not an Elliptic Curve key"
    except (ValueError, TypeError, UnsupportedAlgorithm) as e:
        return False, f"Invalid device public key DER: {e}"

    # Verify ECDSA P-256 / SHA-256 DER signature
    try:
        sig_der = base64.b64decode(sig_b64)
        public_key.verify(sig_der, payload, ec.ECDSA(hashes.SHA256()))
        return True, ""
    except (InvalidSignature, ValueError, TypeError) as e:
        return False, f"Invalid device signature: {e}"


def verify_attestation_chain(chain: list[str] | None) -> bool:
    """Optionally validates X.509 attestation certificate chain if provided."""
    if not chain:
        return True
    try:
        from cryptography import x509

        for cert_b64 in chain:
            der = base64.b64decode(cert_b64)
            x509.load_der_x509_certificate(der)
        return True
    except (ValueError, TypeError):
        return False


def generate_test_device_key(
    kid: str = "test-device-key-1",
) -> tuple[dict[str, Any], ec.EllipticCurvePrivateKey]:
    """Helper to generate a test ECDSA P-256 key pair and device_key dict."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    pub_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    device_key = {
        "kid": kid,
        "alg": "ES256",
        "public_key": base64.b64encode(pub_der).decode("ascii"),
        "attestation_chain": [],
    }
    return device_key, private_key


def sign_device_auth(
    private_key: ec.EllipticCurvePrivateKey,
    session_id: str,
    pending_id: str,
    action_id: str,
    params_sha256: str,
    kid: str,
    method: str = "biometric",
) -> dict[str, Any]:
    """Helper to create an auth dict signed by a test private key."""
    payload = build_auth_payload(
        session_id=session_id,
        pending_id=pending_id,
        action_id=action_id,
        params_sha256=params_sha256,
    )
    signature = private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
    return {
        "method": method,
        "kid": kid,
        "alg": "ES256",
        "signature": base64.b64encode(signature).decode("ascii"),
    }
