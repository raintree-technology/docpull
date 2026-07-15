#!/usr/bin/env python3
"""Build release artifacts with deterministic timestamps and optional replay verification."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

MINIMUM_ZIP_EPOCH = 315532800  # 1980-01-01T00:00:00Z


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=Path("dist"))
    parser.add_argument("--epoch", type=int, help="Override SOURCE_DATE_EPOCH")
    parser.add_argument(
        "--verify-reproducible",
        action="store_true",
        help="Build a second time and require byte-identical artifacts",
    )
    return parser


def _source_date_epoch(repo: Path, requested: int | None = None) -> int:
    candidate: str | int | None = requested
    if candidate is None:
        candidate = os.environ.get("SOURCE_DATE_EPOCH")
    if candidate is None:
        try:
            result = subprocess.run(
                ["git", "show", "-s", "--format=%ct", "HEAD"],
                cwd=repo,
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0 and result.stdout.strip():
            candidate = result.stdout.strip()
    if candidate is None:
        candidate = MINIMUM_ZIP_EPOCH
    try:
        epoch = int(candidate)
    except (TypeError, ValueError) as error:
        raise ValueError("SOURCE_DATE_EPOCH must be an integer Unix timestamp") from error
    if epoch < MINIMUM_ZIP_EPOCH:
        raise ValueError("SOURCE_DATE_EPOCH must be at least 1980-01-01 for wheel compatibility")
    return epoch


def _build(repo: Path, output_dir: Path, *, epoch: int) -> dict[str, str]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"release output directory must be empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(epoch)
    result = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(output_dir)],
        cwd=repo,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"release build failed with exit code {result.returncode}")
    entries = sorted(output_dir.iterdir())
    if (
        len(entries) != 2
        or any(path.is_symlink() or not path.is_file() for path in entries)
        or sum(path.name.endswith(".whl") for path in entries) != 1
        or sum(path.name.endswith(".tar.gz") for path in entries) != 1
    ):
        raise RuntimeError("release build must produce exactly one wheel and one source distribution")
    sdist = next(path for path in entries if path.name.endswith(".tar.gz"))
    _canonicalize_sdist(sdist, epoch=epoch)
    for path in entries:
        path.chmod(0o644)
    artifacts = _artifact_hashes(output_dir)
    return artifacts


def _canonicalize_sdist(path: Path, *, epoch: int) -> None:
    """Rewrite build-backend tar metadata into a portable canonical form."""
    temporary: Path | None = None
    try:
        with (
            tarfile.open(path, mode="r:gz") as source,
            tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix=f".{path.name}.canonical-",
                dir=path.parent,
                delete=False,
            ) as raw_output,
        ):
            temporary = Path(raw_output.name)
            with (
                gzip.GzipFile(filename="", mode="wb", fileobj=raw_output, mtime=epoch) as compressed,
                tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as target,
            ):
                seen: set[str] = set()
                for member in source:
                    if member.name in seen:
                        raise RuntimeError(f"duplicate path in source distribution: {member.name}")
                    seen.add(member.name)
                    if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                        raise RuntimeError(f"unsupported special file in source distribution: {member.name}")
                    member.uid = 0
                    member.gid = 0
                    member.uname = ""
                    member.gname = ""
                    member.mtime = epoch
                    member.pax_headers = {}
                    if member.isdir():
                        member.mode = 0o755
                    elif member.isfile():
                        member.mode = 0o755 if member.mode & 0o111 else 0o644
                    else:
                        member.mode = 0o777
                    content = source.extractfile(member) if member.isfile() else None
                    target.addfile(member, content)
            raw_output.flush()
            os.fsync(raw_output.fileno())
        assert temporary is not None
        temporary.chmod(0o644)
        os.replace(temporary, path)
    except (OSError, tarfile.TarError) as error:
        raise RuntimeError(f"could not canonicalize source distribution: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _artifact_hashes(output_dir: Path) -> dict[str, str]:
    return {path.name: _file_sha256(path) for path in sorted(output_dir.iterdir()) if path.is_file()}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    output_dir = args.outdir.expanduser().resolve()
    epoch = _source_date_epoch(repo, args.epoch)
    try:
        artifacts = _build(repo, output_dir, epoch=epoch)
        if args.verify_reproducible:
            with tempfile.TemporaryDirectory(prefix="docpull-release-replay-") as temporary:
                replay = _build(repo, Path(temporary), epoch=epoch)
            if artifacts != replay:
                raise RuntimeError(
                    f"release artifacts are not reproducible: first={artifacts} replay={replay}"
                )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"release build: {error}", file=sys.stderr)
        return 1
    print(f"SOURCE_DATE_EPOCH={epoch}")
    for name, digest in artifacts.items():
        print(f"{digest}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
