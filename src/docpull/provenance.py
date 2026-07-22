"""Cryptographic provenance for docpull context packs.

Sealing walks a pack, records a sha256 digest for every file in
``pack.digests.json``, and, when a local ed25519 signing key exists, wraps
those digest bytes in a DSSE envelope (``pack.provenance.dsse.json``) so a
third party can verify the pack was not edited after capture. Digest
verification is pure stdlib; signing and signature verification use the
optional ``cryptography`` dependency via lazy imports.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .time_utils import utc_now_iso

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DIGESTS_SCHEMA_VERSION = 1
SEAL_SCHEMA_VERSION = 1
VERIFY_SCHEMA_VERSION = 1
DIGEST_ALGORITHM = "sha256"
DIGESTS_FILENAME = "pack.digests.json"
ENVELOPE_FILENAME = "pack.provenance.dsse.json"
SEAL_ARTIFACT_NAMES = frozenset({DIGESTS_FILENAME, ENVELOPE_FILENAME})
DSSE_PAYLOAD_TYPE = "application/vnd.docpull.pack-digests+json"
SIGNING_KEY_ENV = "DOCPULL_SIGNING_KEY"
SIGNING_PUB_ENV = "DOCPULL_SIGNING_PUB"
PROVENANCE_INSTALL_HINT = "pip install 'docpull[provenance]'"

_READ_CHUNK_BYTES = 1024 * 1024


class ProvenanceError(RuntimeError):
    """User-facing pack provenance error."""


def default_keys_dir() -> Path:
    xdg_home = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg_home:
        return Path(xdg_home) / "docpull" / "keys"
    return Path.home() / ".config" / "docpull" / "keys"


def signing_key_path() -> Path:
    override = (os.environ.get(SIGNING_KEY_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    return default_keys_dir() / "signing.key"


def public_key_path() -> Path:
    override = (os.environ.get(SIGNING_PUB_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    return default_keys_dir() / "signing.pub"


def dsse_pae(payload_type: str, payload: bytes) -> bytes:
    """Return the DSSE Pre-Authentication Encoding for one payload.

    ``PAE(type, body) = "DSSEv1" SP len(type) SP type SP len(body) SP body``
    with lengths as ASCII decimals over the UTF-8 byte forms.
    """
    type_bytes = payload_type.encode("utf-8")
    return b" ".join(
        (
            b"DSSEv1",
            str(len(type_bytes)).encode("ascii"),
            type_bytes,
            str(len(payload)).encode("ascii"),
            payload,
        )
    )


def compute_pack_digests(pack_dir: Path) -> dict[str, Any]:
    """Compute the deterministic ``pack.digests.json`` payload for a pack."""
    root = pack_dir.resolve()
    if not root.is_dir():
        raise ProvenanceError(f"Pack directory not found: {root}")
    files = _file_entries(root)
    if not files:
        raise ProvenanceError(f"Pack has no files to seal: {root}")
    return {
        "schema_version": DIGESTS_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "algorithm": DIGEST_ALGORITHM,
        "root_hash": _root_hash(files),
        "files": files,
    }


def seal_pack(pack_dir: Path) -> dict[str, Any]:
    """Write ``pack.digests.json`` and, when a signing key exists, a DSSE envelope."""
    root = pack_dir.resolve()
    digests = compute_pack_digests(root)
    digests_bytes = (json.dumps(digests, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    (root / DIGESTS_FILENAME).write_bytes(digests_bytes)

    payload: dict[str, Any] = {
        "schema_version": SEAL_SCHEMA_VERSION,
        "generated_at": digests["generated_at"],
        "pack_dir": str(root),
        "algorithm": DIGEST_ALGORITHM,
        "file_count": len(digests["files"]),
        "root_hash": digests["root_hash"],
        "signed": False,
        "artifacts": {"digests": DIGESTS_FILENAME},
    }

    envelope_path = root / ENVELOPE_FILENAME
    signing_key = signing_key_path()
    if not signing_key.exists():
        payload["signing_skipped"] = (
            f"No signing key at {signing_key}. Run `docpull pack keygen` to create one."
        )
        envelope_path.unlink(missing_ok=True)
        return payload
    if not _crypto_available():
        payload["signing_skipped"] = (
            "Signing key found but the cryptography package is not installed. "
            f"Install it with {PROVENANCE_INSTALL_HINT}."
        )
        envelope_path.unlink(missing_ok=True)
        return payload

    private_key = _load_signing_key(signing_key)
    keyid = _public_key_fingerprint(private_key.public_key())
    signature = private_key.sign(dsse_pae(DSSE_PAYLOAD_TYPE, digests_bytes))
    envelope = {
        "payloadType": DSSE_PAYLOAD_TYPE,
        "payload": base64.b64encode(digests_bytes).decode("ascii"),
        "signatures": [{"keyid": keyid, "sig": base64.b64encode(signature).decode("ascii")}],
    }
    envelope_path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    payload["signed"] = True
    payload["keyid"] = keyid
    payload["artifacts"]["envelope"] = ENVELOPE_FILENAME
    return payload


def verify_pack_seal(pack_dir: Path, *, public_key: Path | None = None) -> dict[str, Any]:
    """Recompute pack digests and verify them against the recorded seal."""
    root = pack_dir.resolve()
    if not root.is_dir():
        raise ProvenanceError(f"Pack directory not found: {root}")
    digests_path = root / DIGESTS_FILENAME
    if not digests_path.exists():
        raise ProvenanceError(
            f"Pack is not sealed: {digests_path} is missing. Run `docpull pack seal` first."
        )
    digests_bytes = digests_path.read_bytes()
    stored = _parse_digests(digests_path, digests_bytes)

    stored_files = {str(entry["path"]): entry for entry in stored["files"]}
    current_files = {str(entry["path"]): entry for entry in _file_entries(root)}
    added = sorted(set(current_files) - set(stored_files))
    removed = sorted(set(stored_files) - set(current_files))
    modified = sorted(
        path
        for path in set(stored_files) & set(current_files)
        if stored_files[path].get("sha256") != current_files[path]["sha256"]
        or stored_files[path].get("size") != current_files[path]["size"]
    )
    files_ok = not (added or removed or modified)

    computed_root_hash = _root_hash(stored["files"])
    root_hash_ok = stored.get("root_hash") == computed_root_hash

    signature = _verify_envelope(root, digests_bytes, public_key)
    signature_ok = signature["status"] in {"absent", "skipped", "valid"}

    return {
        "schema_version": VERIFY_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(root),
        "ok": files_ok and root_hash_ok and signature_ok,
        "algorithm": DIGEST_ALGORITHM,
        "file_count": len(current_files),
        "files": {"ok": files_ok, "added": added, "removed": removed, "modified": modified},
        "root_hash": {
            "ok": root_hash_ok,
            "stored": stored.get("root_hash"),
            "computed": computed_root_hash,
        },
        "signature": signature,
    }


def generate_signing_keypair(*, force: bool = False) -> dict[str, Any]:
    """Generate a local ed25519 keypair for pack sealing. Never returns key material."""
    if not _crypto_available():
        raise ProvenanceError(
            "docpull pack keygen requires the optional cryptography dependency. "
            f"Install it with {PROVENANCE_INSTALL_HINT}."
        )
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    private_path = signing_key_path()
    public_path = public_key_path()
    existing = [str(path) for path in (private_path, public_path) if path.exists()]
    if existing and not force:
        raise ProvenanceError(
            "Signing keys already exist (" + ", ".join(existing) + "). Rerun with --force to overwrite."
        )

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    public_pem = private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)

    private_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    _write_private_bytes(private_path, private_pem)
    public_path.write_bytes(public_pem)
    return {
        "private_key": str(private_path),
        "public_key": str(public_path),
        "keyid": _public_key_fingerprint(private_key.public_key()),
    }


def _file_entries(pack_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in pack_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(pack_dir).as_posix()
        if relative in SEAL_ARTIFACT_NAMES:
            continue
        entries.append({"path": relative, "sha256": _sha256_file(path), "size": path.stat().st_size})
    entries.sort(key=lambda entry: str(entry["path"]))
    return entries


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _root_hash(files: list[dict[str, Any]]) -> str:
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_digests(path: Path, raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ProvenanceError(f"Invalid JSON in {path}: {err}") from err
    if not isinstance(parsed, dict):
        raise ProvenanceError(f"Invalid digest file {path}: expected a JSON object.")
    if parsed.get("algorithm") != DIGEST_ALGORITHM:
        raise ProvenanceError(f"Unsupported digest algorithm in {path}: {parsed.get('algorithm')!r}")
    files = parsed.get("files")
    if not isinstance(files, list) or not all(
        isinstance(entry, dict) and isinstance(entry.get("path"), str) for entry in files
    ):
        raise ProvenanceError(f"Invalid digest file {path}: 'files' must be a list of path objects.")
    return parsed


def _verify_envelope(root: Path, digests_bytes: bytes, public_key: Path | None) -> dict[str, Any]:
    envelope_path = root / ENVELOPE_FILENAME
    if not envelope_path.exists():
        return {"status": "absent", "detail": f"No {ENVELOPE_FILENAME} found; digest-only verification."}

    envelope, envelope_error = _parse_envelope(envelope_path)
    if envelope is None:
        return {"status": "invalid", "detail": envelope_error}
    if envelope.get("payloadType") != DSSE_PAYLOAD_TYPE:
        return {"status": "invalid", "detail": f"Unexpected payloadType {envelope.get('payloadType')!r}."}
    try:
        payload_bytes = base64.b64decode(str(envelope["payload"]), validate=True)
    except (binascii.Error, ValueError) as err:
        return {"status": "invalid", "detail": f"DSSE payload is not valid base64: {err}"}
    if payload_bytes != digests_bytes:
        return {"status": "invalid", "detail": f"DSSE payload does not match {DIGESTS_FILENAME}."}
    if not _crypto_available():
        return {
            "status": "skipped",
            "detail": (
                "Signature present but the cryptography package is not installed. "
                f"Install it with {PROVENANCE_INSTALL_HINT} to verify it."
            ),
        }

    key_path = public_key if public_key is not None else public_key_path()
    if not key_path.exists():
        return {
            "status": "error",
            "detail": (f"No public key found at {key_path}. Pass --public-key or run `docpull pack keygen`."),
        }
    verifier = _load_public_key(key_path)
    keyid = _public_key_fingerprint(verifier)
    pae = dsse_pae(DSSE_PAYLOAD_TYPE, payload_bytes)
    for entry in envelope["signatures"]:
        try:
            signature = base64.b64decode(str(entry.get("sig", "")), validate=True)
        except (binascii.Error, ValueError):
            continue
        if _signature_valid(verifier, signature, pae):
            return {"status": "valid", "keyid": keyid, "public_key": str(key_path)}
    return {
        "status": "invalid",
        "detail": "Ed25519 signature verification failed.",
        "keyid": keyid,
        "public_key": str(key_path),
    }


def _parse_envelope(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        return None, f"Invalid JSON in {path}: {err}"
    if not isinstance(parsed, dict) or not isinstance(parsed.get("payload"), str):
        return None, f"Invalid DSSE envelope {path}: expected an object with a payload string."
    signatures = parsed.get("signatures")
    if (
        not isinstance(signatures, list)
        or not signatures
        or not all(isinstance(entry, dict) for entry in signatures)
    ):
        return None, f"Invalid DSSE envelope {path}: 'signatures' must be a non-empty list of objects."
    return parsed, None


def _crypto_available() -> bool:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return False
    return True


def _load_signing_key(path: Path) -> Ed25519PrivateKey:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        key = load_pem_private_key(path.read_bytes(), password=None)
    except (ValueError, OSError) as err:
        raise ProvenanceError(f"Could not load signing key {path}: {err}") from err
    if not isinstance(key, Ed25519PrivateKey):
        raise ProvenanceError(f"Signing key {path} is not an ed25519 private key.")
    return key


def _load_public_key(path: Path) -> Ed25519PublicKey:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    try:
        key = load_pem_public_key(path.read_bytes())
    except (ValueError, OSError) as err:
        raise ProvenanceError(f"Could not load public key {path}: {err}") from err
    if not isinstance(key, Ed25519PublicKey):
        raise ProvenanceError(f"Public key {path} is not an ed25519 public key.")
    return key


def _public_key_fingerprint(key: Ed25519PublicKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    der = key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


def _signature_valid(key: Ed25519PublicKey, signature: bytes, message: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature

    try:
        key.verify(signature, message)
    except InvalidSignature:
        return False
    return True


def _write_private_bytes(path: Path, payload: bytes) -> None:
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)
