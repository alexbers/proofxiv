"""Unpack submission bundles (.zip or .tar[.gz]) into {path: bytes}, safely and in memory."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import PurePosixPath

from ..config import get_settings
from .errors import Invalid, TooLarge

MAX_FILES = 2000

# programs a reader could run by mistake: papers ship source code, never binaries
EXECUTABLE_SUFFIXES = (
    ".exe", ".dll", ".scr", ".com", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".lnk", ".jar", ".apk",
    ".app", ".dmg", ".deb", ".rpm", ".appimage", ".so", ".dylib", ".class", ".wasm",
)
_MACHO = (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe")
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")


def executable_kind(name: str, head: bytes) -> str | None:
    """What kind of program a file is ('ELF binary', '.exe file', ...), or None for other files.
    `head` is the start of the file (512 bytes are enough)."""
    lower = name.lower()
    for suffix in EXECUTABLE_SUFFIXES:
        if lower.endswith(suffix):
            return f"{suffix} file"
    if head.startswith(b"\x7fELF"):
        return "ELF binary"
    if head.startswith(_MACHO) or head.startswith(b"\xca\xfe\xba\xbe"):
        return "Mach-O binary or Java class"
    if head.startswith(b"\0asm"):
        return "WebAssembly module"
    if head.startswith(b"MZ") and len(head) >= 64:
        offset = int.from_bytes(head[60:64], "little")
        if offset + 4 <= len(head) and head[offset : offset + 4] == b"PE\0\0":
            return "Windows program"
        if b"This program cannot be run in DOS mode" in head[:512]:
            return "Windows program"
    return None


def _inside_archive(name: str, data: bytes) -> str | None:
    """A program inside an archive that sits in the bundle (one level deep)."""
    lower = name.lower()
    try:
        if lower.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if not info.is_dir():
                        with zf.open(info) as fh:
                            kind = executable_kind(info.filename, fh.read(512))
                        if kind:
                            return f"{info.filename} ({kind})"
        elif lower.endswith((".tar", ".tar.gz", ".tgz")):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                for member in tf.getmembers():
                    extracted = tf.extractfile(member) if member.isfile() else None
                    if extracted is not None:
                        kind = executable_kind(member.name, extracted.read(512))
                        if kind:
                            return f"{member.name} ({kind})"
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError):
        return None
    return None


def refuse_executable(name: str, data: bytes) -> None:
    kind = executable_kind(name, data[:4096])
    if kind is None and name.lower().endswith(ARCHIVE_SUFFIXES):
        inner = _inside_archive(name, data)
        kind = f"an archive with a program inside: {inner}" if inner else None
    if kind:
        raise Invalid(
            f"{name}: programs are not accepted ({kind}); include the source code instead, "
            "readers and checkers build what they need"
        )


def _clean(name: str) -> str | None:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts:
        raise Invalid(f"unsafe path in bundle: {name}")
    parts = [x for x in p.parts if x not in ("", ".")]
    if not parts or any(x.startswith("__MACOSX") for x in parts):
        return None
    return "/".join(parts)


def _strip_common_root(files: dict[str, bytes]) -> dict[str, bytes]:
    roots = {path.split("/", 1)[0] for path in files}
    if len(roots) == 1 and all("/" in path for path in files):
        root = roots.pop()
        if f"{root}/paper.json" in files:
            return {path.split("/", 1)[1]: data for path, data in files.items()}
    return files


def extract_bundle(data: bytes, filename: str) -> dict[str, bytes]:
    limit = get_settings().max_submission_mb * 1024 * 1024
    files: dict[str, bytes] = {}
    total = 0

    def add(name: str, content: bytes) -> None:
        nonlocal total
        clean = _clean(name)
        if clean is None:
            return
        total += len(content)
        if total > limit:
            raise TooLarge("bundle is too large when unpacked")
        if len(files) >= MAX_FILES:
            raise Invalid(f"bundle has more than {MAX_FILES} files")
        refuse_executable(clean, content)
        files[clean] = content

    lower = filename.lower()
    try:
        if lower.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    if info.file_size > limit:
                        raise TooLarge(f"{info.filename} is too large")
                    add(info.filename, zf.read(info))
        elif lower.endswith((".tar", ".tar.gz", ".tgz")):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue  # skip dirs, symlinks, devices
                    if member.size > limit:
                        raise TooLarge(f"{member.name} is too large")
                    extracted = tf.extractfile(member)
                    if extracted is not None:
                        add(member.name, extracted.read())
        else:
            raise Invalid("bundle must be .zip, .tar, .tar.gz or .tgz")
    except (zipfile.BadZipFile, tarfile.TarError) as exc:
        raise Invalid(f"cannot read bundle: {exc}") from exc
    return _strip_common_root(files)
