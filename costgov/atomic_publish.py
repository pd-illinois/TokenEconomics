"""Publish fully written evidence without ever replacing an existing record."""

import ctypes
import errno
import os
import sys


def _rename_no_replace_linux(source, destination):
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOTSUP, "Atomic no-replace rename is unavailable")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    # AT_FDCWD and RENAME_NOREPLACE preserve create-only semantics on CIFS.
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), os.fspath(destination))


def publish_immutable(source, destination):
    """Publish a closed staging file; callers may unlink staging with missing_ok.

    Hard-link publication leaves staging intact. The Azure Files-compatible
    no-replace rename consumes staging, but never exposes a partial final file.
    """
    try:
        os.link(source, destination)
    except OSError as error:
        if error.errno not in {errno.ENOTSUP, errno.EOPNOTSUPP}:
            raise
        if sys.platform == "linux":
            _rename_no_replace_linux(source, destination)
        elif os.name == "nt":
            os.rename(source, destination)  # Windows rename refuses an existing target.
        else:
            raise
