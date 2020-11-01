import errno
import os
import pathlib
import socket

import pytest

import nixutil

from .util import managed_open


def test_recover_fd_path_dir(tmpdir: pathlib.Path) -> None:
    with managed_open(tmpdir, os.O_RDONLY) as fd:
        assert nixutil.recover_fd_path(fd) == os.path.realpath(tmpdir)

    with managed_open(tmpdir, os.O_RDONLY) as fd:
        assert nixutil.recover_fd_path(fd) == os.path.realpath(tmpdir)

    with managed_open("/", os.O_RDONLY) as fd:
        assert nixutil.recover_fd_path(fd) == "/"


def test_recover_fd_path_dir_deleted(tmpdir: pathlib.Path) -> None:
    os.mkdir(tmpdir / "a")

    with managed_open(tmpdir / "a", os.O_RDONLY) as fd:
        os.rmdir(tmpdir / "a")

        # Either it returns the correct path or raises a FileNotFoundError.
        try:
            assert nixutil.recover_fd_path(fd) == str(tmpdir.resolve() / "a")
        except FileNotFoundError:
            pass


def test_recover_fd_path_dir_fallback(tmpdir: pathlib.Path) -> None:
    old_func = nixutil.plat_util.try_recover_fd_path
    del nixutil.plat_util.try_recover_fd_path

    try:
        with managed_open(tmpdir, os.O_RDONLY) as fd:
            assert nixutil.recover_fd_path(fd) == os.path.realpath(tmpdir)

        # Without OS-specific help, ENOTSUP is raised for regular files
        with open(tmpdir / "a", "w") as file:
            with pytest.raises(OSError, match=r"[nN]ot supported"):
                nixutil.recover_fd_path(file.fileno())

    finally:
        nixutil.plat_util.try_recover_fd_path = old_func


def test_recover_fd_path_file(tmpdir: pathlib.Path) -> None:
    with open(tmpdir / "a", "w") as file:
        try:
            assert nixutil.recover_fd_path(file.fileno()) == os.path.realpath(tmpdir / "a")
        except OSError as ex:
            if ex.errno == errno.ENOTSUP:
                pytest.skip("Recovering paths of regular files not supported")
            else:
                raise


def test_recover_fd_path_bad_file() -> None:
    # Negative file descriptors always raise an error
    for i in range(-10, 0):
        with pytest.raises(OSError, match=r"[bB]ad file descriptor"):
            nixutil.recover_fd_path(i)

    # This is larger than any file descriptor that we should be allowed to open
    with pytest.raises(OSError, match=r"[bB]ad file descriptor"):
        nixutil.recover_fd_path(os.sysconf("SC_OPEN_MAX"))

    # Sockets and pipes aren't allowed

    with socket.socket() as sock:
        with pytest.raises(OSError, match=r"[nN]ot supported"):
            nixutil.recover_fd_path(sock.fileno())

    r_fd, w_fd = os.pipe()
    try:
        with pytest.raises(OSError, match=r"[nN]ot supported"):
            nixutil.recover_fd_path(r_fd)
        with pytest.raises(OSError, match=r"[nN]ot supported"):
            nixutil.recover_fd_path(w_fd)
    finally:
        os.close(r_fd)
        os.close(w_fd)
