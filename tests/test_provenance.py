"""Pack provenance sealing, verification, and keygen tests."""

from __future__ import annotations

import base64
import json
import stat
from pathlib import Path

import pytest

import docpull.provenance as provenance_module
from docpull.pack_tools import run_pack_cli
from docpull.provenance import (
    DIGESTS_FILENAME,
    DSSE_PAYLOAD_TYPE,
    ENVELOPE_FILENAME,
    ProvenanceError,
    compute_pack_digests,
    dsse_pae,
    generate_signing_keypair,
    seal_pack,
    verify_pack_seal,
)


@pytest.fixture(autouse=True)
def keystore_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the signing keystore at an empty temp dir and widen console output."""
    keys_dir = tmp_path / "keystore"
    monkeypatch.setenv("DOCPULL_SIGNING_KEY", str(keys_dir / "signing.key"))
    monkeypatch.setenv("DOCPULL_SIGNING_PUB", str(keys_dir / "signing.pub"))
    monkeypatch.setenv("COLUMNS", "500")
    return keys_dir


def _write_pack(pack_dir: Path) -> None:
    sources = pack_dir / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    (pack_dir / "documents.ndjson").write_text('{"url": "https://example.com/doc"}\n', encoding="utf-8")
    (pack_dir / "corpus.manifest.json").write_text('{"record_count": 1}\n', encoding="utf-8")
    (sources / "01.md").write_text("# Example\n\nSeal me.\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def test_dsse_pae_golden_vector() -> None:
    assert DSSE_PAYLOAD_TYPE == "application/vnd.docpull.pack-digests+json"
    assert dsse_pae(DSSE_PAYLOAD_TYPE, b"{}") == (b"DSSEv1 41 application/vnd.docpull.pack-digests+json 2 {}")
    assert dsse_pae("t", b"") == b"DSSEv1 1 t 0 "


def test_seal_writes_deterministic_digests(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)

    first = seal_pack(pack)
    first_digests = _read_json(pack / DIGESTS_FILENAME)
    second = seal_pack(pack)
    second_digests = _read_json(pack / DIGESTS_FILENAME)

    assert first["signed"] is False
    assert second["signed"] is False
    assert first_digests["schema_version"] == 1
    assert first_digests["algorithm"] == "sha256"
    assert first_digests["files"] == second_digests["files"]
    assert first_digests["root_hash"] == second_digests["root_hash"]
    files = first_digests["files"]
    assert isinstance(files, list)
    paths = [entry["path"] for entry in files]
    assert paths == sorted(paths)
    assert all(set(entry) == {"path", "sha256", "size"} for entry in files)


def test_seal_excludes_its_own_artifacts(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)

    seal_pack(pack)
    seal_pack(pack)

    digests = _read_json(pack / DIGESTS_FILENAME)
    files = digests["files"]
    assert isinstance(files, list)
    paths = {entry["path"] for entry in files}
    assert DIGESTS_FILENAME not in paths
    assert ENVELOPE_FILENAME not in paths
    assert paths == {"corpus.manifest.json", "documents.ndjson", "sources/01.md"}


def test_seal_without_signing_key_skips_signature(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)

    assert run_pack_cli(["seal", str(pack), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["signed"] is False
    assert "keygen" in payload["signing_skipped"]
    assert payload["artifacts"] == {"digests": DIGESTS_FILENAME}
    assert (pack / DIGESTS_FILENAME).exists()
    assert not (pack / ENVELOPE_FILENAME).exists()


def test_verify_seal_passes_on_untouched_pack(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)

    assert run_pack_cli(["seal", str(pack)]) == 0
    capsys.readouterr()
    assert run_pack_cli(["verify-seal", str(pack), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["files"] == {"ok": True, "added": [], "removed": [], "modified": []}
    assert payload["root_hash"]["ok"] is True
    assert payload["signature"]["status"] == "absent"


def test_verify_seal_detects_modified_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)
    seal_pack(pack)

    (pack / "sources" / "01.md").write_text("# Tampered\n", encoding="utf-8")

    assert run_pack_cli(["verify-seal", str(pack), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["files"]["modified"] == ["sources/01.md"]
    assert payload["files"]["added"] == []
    assert payload["files"]["removed"] == []


def test_verify_seal_detects_added_and_removed_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)
    seal_pack(pack)

    (pack / "extra.txt").write_text("injected\n", encoding="utf-8")
    (pack / "documents.ndjson").unlink()

    assert run_pack_cli(["verify-seal", str(pack), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["files"]["added"] == ["extra.txt"]
    assert payload["files"]["removed"] == ["documents.ndjson"]

    capsys.readouterr()
    assert run_pack_cli(["verify-seal", str(pack)]) == 1
    output = capsys.readouterr().out
    assert "extra.txt" in output
    assert "documents.ndjson" in output


def test_verify_seal_detects_root_hash_tamper(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)
    seal_pack(pack)

    digests = _read_json(pack / DIGESTS_FILENAME)
    digests["root_hash"] = "0" * 64
    (pack / DIGESTS_FILENAME).write_text(json.dumps(digests, indent=2) + "\n", encoding="utf-8")

    payload = verify_pack_seal(pack)
    assert payload["ok"] is False
    assert payload["root_hash"]["ok"] is False
    assert payload["files"]["ok"] is True


def test_verify_seal_requires_a_seal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)

    assert run_pack_cli(["verify-seal", str(pack)]) == 1
    assert "not sealed" in capsys.readouterr().out

    with pytest.raises(ProvenanceError):
        compute_pack_digests(tmp_path / "missing")


def test_keygen_errors_without_cryptography(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(provenance_module, "_crypto_available", lambda: False)

    assert run_pack_cli(["keygen"]) == 1
    assert "docpull[provenance]" in capsys.readouterr().out


def test_seal_notes_skipped_signing_without_cryptography(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("cryptography")
    generate_signing_keypair()
    monkeypatch.setattr(provenance_module, "_crypto_available", lambda: False)

    pack = tmp_path / "pack"
    _write_pack(pack)
    assert run_pack_cli(["seal", str(pack)]) == 0
    output = capsys.readouterr().out

    assert "Signing skipped" in output
    assert "docpull[provenance]" in output
    assert (pack / DIGESTS_FILENAME).exists()
    assert not (pack / ENVELOPE_FILENAME).exists()


def test_keygen_creates_keys_with_private_mode(
    keystore_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("cryptography")

    assert run_pack_cli(["keygen"]) == 0
    private_key = keystore_dir / "signing.key"
    public_key = keystore_dir / "signing.pub"

    assert stat.S_IMODE(private_key.stat().st_mode) == 0o600
    assert b"BEGIN PRIVATE KEY" in private_key.read_bytes()
    assert b"BEGIN PUBLIC KEY" in public_key.read_bytes()
    assert "BEGIN PRIVATE KEY" not in capsys.readouterr().out

    assert run_pack_cli(["keygen"]) == 1
    assert "--force" in capsys.readouterr().out
    assert run_pack_cli(["keygen", "--force"]) == 0


def test_seal_and_verify_roundtrip_with_signature(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("cryptography")
    keys = generate_signing_keypair()

    pack = tmp_path / "pack"
    _write_pack(pack)
    assert run_pack_cli(["seal", str(pack), "--json"]) == 0
    sealed = json.loads(capsys.readouterr().out)

    assert sealed["signed"] is True
    assert sealed["keyid"] == keys["keyid"]
    envelope = _read_json(pack / ENVELOPE_FILENAME)
    assert envelope["payloadType"] == DSSE_PAYLOAD_TYPE
    assert base64.b64decode(str(envelope["payload"])) == (pack / DIGESTS_FILENAME).read_bytes()
    signatures = envelope["signatures"]
    assert isinstance(signatures, list)
    assert signatures[0]["keyid"] == keys["keyid"]

    assert run_pack_cli(["verify-seal", str(pack), "--json"]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["ok"] is True
    assert verified["signature"]["status"] == "valid"
    assert verified["signature"]["keyid"] == keys["keyid"]


def test_verify_seal_rejects_tampered_payload(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    generate_signing_keypair()

    pack = tmp_path / "pack"
    _write_pack(pack)
    seal_pack(pack)

    envelope_path = pack / ENVELOPE_FILENAME
    envelope = _read_json(envelope_path)
    payload_bytes = bytearray(base64.b64decode(str(envelope["payload"])))
    payload_bytes[0] ^= 0x01
    envelope["payload"] = base64.b64encode(bytes(payload_bytes)).decode("ascii")
    envelope_path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")

    payload = verify_pack_seal(pack)
    assert payload["ok"] is False
    assert payload["signature"]["status"] == "invalid"


def test_verify_seal_rejects_wrong_public_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    generate_signing_keypair()
    pack = tmp_path / "pack"
    _write_pack(pack)
    seal_pack(pack)

    wrong_key = tmp_path / "wrong.pub"
    wrong_key.write_bytes(
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )

    assert run_pack_cli(["verify-seal", str(pack), "--public-key", str(wrong_key), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["signature"]["status"] == "invalid"
    assert payload["files"]["ok"] is True


def test_verify_seal_degrades_without_cryptography(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("cryptography")
    generate_signing_keypair()

    pack = tmp_path / "pack"
    _write_pack(pack)
    seal_pack(pack)
    monkeypatch.setattr(provenance_module, "_crypto_available", lambda: False)

    payload = verify_pack_seal(pack)
    assert payload["ok"] is True
    assert payload["signature"]["status"] == "skipped"
    assert "docpull[provenance]" in payload["signature"]["detail"]


def test_verify_seal_requires_public_key_for_signed_pack(tmp_path: Path, keystore_dir: Path) -> None:
    pytest.importorskip("cryptography")
    generate_signing_keypair()

    pack = tmp_path / "pack"
    _write_pack(pack)
    seal_pack(pack)
    (keystore_dir / "signing.pub").unlink()

    payload = verify_pack_seal(pack)
    assert payload["ok"] is False
    assert payload["signature"]["status"] == "error"
