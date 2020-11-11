import errno
import os
import pathlib
import socket

import pytest

import nixutil

from .util import managed_open


def test_recover_fd_path_dir(tmp_path: pathlib.Path) -> None:
    with managed_open(tmp_path, os.O_RDONLY) as fd:
        assert nixutil.recover_fd_path(fd) == os.path.realpath(tmp_path)

    with managed_open(tmp_path, os.O_RDONLY) as fd:
        assert nixutil.recover_fd_path(fd) == os.path.realpath(tmp_path)

    with managed_open("/", os.O_RDONLY) as fd:
        assert nixutil.recover_fd_path(fd) == "/"


def test_recover_fd_path_link(tmp_path: pathlib.Path) -> None:
    os.mkdir(tmp_path / "dir")
    os.symlink("dir", tmp_path / "link")

    with managed_open(tmp_path / "link", os.O_RDONLY) as fd:
        # The path that's returned is the path *after* resolving symlinks
        assert nixutil.recover_fd_path(fd) == os.path.realpath(tmp_path / "dir")


def test_recover_fd_path_moved(tmp_path: pathlib.Path) -> None:
    os.mkdir(tmp_path / "dir")

    with managed_open(tmp_path / "dir", os.O_RDONLY) as fd:
        os.rename(tmp_path / "dir", tmp_path / "dir2")

        # The path that's returned is the *new* path
        assert nixutil.recover_fd_path(fd) == os.path.realpath(tmp_path / "dir2")


def test_recover_fd_path_dir_deleted(tmp_path: pathlib.Path) -> None:
    os.mkdir(tmp_path / "a")

    with managed_open(tmp_path / "a", os.O_RDONLY) as fd:
        os.rmdir(tmp_path / "a")

        # Either it returns the correct path or raises a FileNotFoundError.
        try:
            assert nixutil.recover_fd_path(fd) == os.path.join(os.path.realpath(tmp_path), "a")
        except FileNotFoundError:
            pass


def test_recover_fd_path_dir_fallback(tmp_path: pathlib.Path) -> None:
    old_func = getattr(nixutil.plat_util, "try_recover_fd_path", None)
    if old_func is not None:
        del nixutil.plat_util.try_recover_fd_path

    try:
        with managed_open(tmp_path, os.O_RDONLY) as fd:
            assert nixutil.recover_fd_path(fd) == os.path.realpath(tmp_path)

        # Without OS-specific help, ENOTSUP is raised for regular files
        with open(tmp_path / "a", "w") as file:
            with pytest.raises(OSError, match=r"[nN]ot supported"):
                nixutil.recover_fd_path(file.fileno())

    finally:
        if old_func is not None:
            nixutil.plat_util.try_recover_fd_path = old_func


def test_recover_fd_path_file(tmp_path: pathlib.Path) -> None:
    with open(tmp_path / "a", "w") as file:
        try:
            assert nixutil.recover_fd_path(file.fileno()) == os.path.realpath(tmp_path / "a")
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


def test_recover_fd_path_execute(tmp_path: pathlib.Path) -> None:
    os.mkdir(tmp_path / "a")
    os.mkdir(tmp_path / "a/b")

    old_func = getattr(nixutil.plat_util, "try_recover_fd_path", None)
    if old_func is not None:
        del nixutil.plat_util.try_recover_fd_path

    try:
        with managed_open(tmp_path / "a/b", os.O_RDONLY) as b_dfd:
            try:
                # 0o100 is "--x------"; i.e. execute permission but not read permission.
                # That allows us to access files in the directory, but not list it.
                os.chmod(tmp_path / "a", 0o100)

                # Fails with EACCES when trying to open the directory
                with pytest.raises(PermissionError):
                    nixutil.recover_fd_path(b_dfd)

            finally:
                # chmod() it back so pytest can remove it
                os.chmod(tmp_path / "a", 0o755)

    finally:
        if old_func is not None:
            nixutil.plat_util.try_recover_fd_path = old_func


def test_recover_fd_path_no_execute(tmp_path: pathlib.Path) -> None:
    os.mkdir(tmp_path / "a")
    os.mkdir(tmp_path / "a/b")

    old_func = getattr(nixutil.plat_util, "try_recover_fd_path", None)
    if old_func is not None:
        del nixutil.plat_util.try_recover_fd_path

    try:
        with managed_open(tmp_path / "a/b", os.O_RDONLY) as b_dfd:
            try:
                # 0o400 is "r--------"; i.e. read permission but not execute permission.
                # That allows us to list the directory, but not access files in it.
                os.chmod(tmp_path / "a", 0o400)

                # Fails with ENOENT after failing to stat() any of the entries and reaching the end
                with pytest.raises(FileNotFoundError):
                    nixutil.recover_fd_path(b_dfd)

            finally:
                # chmod() it back so pytest can remove it
                os.chmod(tmp_path / "a", 0o755)

    finally:
        if old_func is not None:
            nixutil.plat_util.try_recover_fd_path = old_func
