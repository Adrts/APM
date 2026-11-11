"""Hash verification using hashlib or an external 7-Zip."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from . import messages as msg, tools
from .errors import VerifyError
from .output import console
from .util import lp

HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
HASH_LENGTHS = {"md5": 32, "sha1": 40, "sha256": 64, "sha512": 128}
SIXTY_FOUR_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


def parse_hash(value: str) -> tuple[str, str]:
    text = value.strip()
    if ":" in text:
        algorithm, digest = text.split(":", 1)
        algorithm = algorithm.lower()
        digest = digest.lower()
        if algorithm not in HASH_LENGTHS:
            raise VerifyError(
                msg.ERR_HASH_ALGORITHM.format(algorithm=algorithm)
            )
    else:
        algorithm = "sha256"
        digest = text.lower()
        if not SIXTY_FOUR_HEX.match(digest):
            raise VerifyError(msg.ERR_HASH_VALUE.format(value=value))
    if not HEX_RE.match(digest) or len(digest) != HASH_LENGTHS[algorithm]:
        raise VerifyError(msg.ERR_HASH_LENGTH.format(value=value))
    return algorithm, digest


def compute_hash(path: Path, algorithm: str) -> str:
    if hasattr(hashlib, "file_digest"):
        try:
            with open(lp(path), "rb") as fh:
                return hashlib.file_digest(fh, algorithm).hexdigest()
        except (TypeError, ValueError):
            pass
    try:
        digest = hashlib.new(algorithm, usedforsecurity=False)
    except ValueError as exc:
        raise VerifyError(
            msg.ERR_HASH_ALGORITHM.format(algorithm=algorithm)
        ) from exc
    with open(lp(path), "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _seven_zip_hash(tool: Path, path: Path, algorithm: str) -> str | None:
    proc = tools.run_tool([str(tool), "h", f"-scrc{algorithm.upper()}", str(path)])
    if proc.returncode != 0:
        return None
    match = re.search(
        rf"{algorithm.upper()}\s+for\s+data\s*:\s*([0-9A-Fa-f]+)",
        proc.stdout,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(
            rf"^\s*{algorithm.upper()}\s*=\s*([0-9A-Fa-f]+)\s*$",
            proc.stdout,
            re.MULTILINE,
        )
    return match.group(1).lower() if match else None


def _seven_zip_test(tool: Path, path: Path) -> tuple[bool, str | None]:
    proc = tools.run_tool([str(tool), "t", "-y", str(path)])
    output = f"{proc.stdout}\n{proc.stderr}"
    lowered = output.lower()
    if proc.returncode == 0:
        return True, None
    if proc.returncode == 1:
        return True, msg.MSG_SEVEN_ZIP_WARNING
    if "is not archive" in lowered or "cannot open the file as archive" in lowered:
        return True, msg.MSG_NOT_ARCHIVE
    return False, output.strip() or msg.ERR_SEVEN_ZIP_TEST.format(
        code=proc.returncode
    )


def verify_file(
    path: Path,
    hash_value: str | None,
    config: dict[str, Any],
    app_name: str,
) -> tuple[bool, str | None]:
    method = config.get("verify", {}).get("method", "hashlib")
    if method == "7zip":
        tool = tools.find_7z(config, "verify")
        if not tool:
            return False, msg.ERR_SEVEN_ZIP_MISSING_VERIFY
        if not hash_value:
            ok, message = _seven_zip_test(tool, path)
            if ok and message:
                console.warn(message)
            return ok, None if ok else message
        try:
            algorithm, expected = parse_hash(hash_value)
        except VerifyError as exc:
            return False, str(exc)
        actual = _seven_zip_hash(tool, path, algorithm)
        if actual is None:
            return False, msg.ERR_SEVEN_ZIP_HASH.format(
                algorithm=algorithm.upper()
            )
        if actual != expected:
            return False, msg.ERR_HASH_MISMATCH_NO_ALGORITHM.format(
                app=app_name, expected=expected, actual=actual
            )
        return True, None

    if not hash_value:
        actual = compute_hash(path, "sha256")
        console.warn(
            msg.MSG_HASH_NOT_PROVIDED.format(file=path.name, actual=actual)
        )
        return True, None
    try:
        algorithm, expected = parse_hash(hash_value)
    except VerifyError as exc:
        return False, str(exc)
    actual = compute_hash(path, algorithm)
    if actual != expected:
        return False, msg.ERR_HASH_MISMATCH.format(
            app=app_name,
            algorithm=algorithm.upper(),
            expected=expected,
            actual=actual,
        )
    return True, None
