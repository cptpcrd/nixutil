import contextlib
import os
import pathlib
import sys
from typing import Any, Generator

import pytest

import nixutil


@contextlib.contextmanager
def open_beneath_managed(*args: Any, **kwargs: Any) -> Generator[int, None, None]:
    fd = nixutil.open_beneath(*args, **kwargs)

    try:
        yield fd
    finally:
        os.close(fd)


def test_open_beneath_basic() -> None:
    # dir_fd must be an int or None
    with pytest.raises(TypeError):
        nixutil.open_beneath("/", os.O_RDONLY, dir_fd="a")  # type: ignore

    # Not inheritable
    with open_beneath_managed("/", os.O_RDONLY) as fd:
        assert not os.get_inheritable(fd)

    # Basic errors

    with pytest.raises(FileNotFoundError):
        nixutil.open_beneath("", os.O_RDONLY)

    with open(sys.executable) as file:
        with pytest.raises(NotADirectoryError):
            nixutil.open_beneath("a", os.O_RDONLY, dir_fd=file.fileno())


def test_open_beneath_resolve_in_root(tmp_path: pathlib.Path) -> None:
    os.mkdir(tmp_path / "a")

    with open(tmp_path / "b", "w"):
        pass

    os.symlink("b", tmp_path / "c")
    os.symlink("/b", tmp_path / "d")

    os.mkdir(tmp_path / "a/e")

    os.symlink("a/e", tmp_path / "f")

    os.symlink("recur", tmp_path / "recur")

    tmp_dfd = os.open(tmp_path, os.O_RDONLY)

    try:
        tmp_stat = os.stat(tmp_dfd)

        # Without ALLOW_PARENT_ELEMS
        with open_beneath_managed(
            "/",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), tmp_stat)

        # Trying to escape with "/" and ".."
        with open_beneath_managed(
            "/",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), tmp_stat)

        with open_beneath_managed(
            "..",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), tmp_stat)

        with open_beneath_managed(
            "/..",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), tmp_stat)

        with open_beneath_managed(
            "a/../..",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), tmp_stat)

        with open_beneath_managed(
            "/a/../..",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), tmp_stat)

        # Opening back to the parent
        with open_beneath_managed(
            "a/..",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), tmp_stat)

        with open_beneath_managed(
            "a/e/../..",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), tmp_stat)

        # Opening regular files/directories
        with open_beneath_managed(
            "a",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), os.stat("a", dir_fd=tmp_dfd))

        with open_beneath_managed(
            "a/.",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), os.stat("a", dir_fd=tmp_dfd))

        with open_beneath_managed(
            "a/e",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), os.stat("a/e", dir_fd=tmp_dfd))

        with open_beneath_managed(
            "a/e/../e",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), os.stat("a/e", dir_fd=tmp_dfd))

        with open_beneath_managed(
            "a/e/..",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), os.stat("a", dir_fd=tmp_dfd))

        with open_beneath_managed(
            "b",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), os.stat("b", dir_fd=tmp_dfd))

        # Opening a relative symlink works
        with open_beneath_managed(
            "c",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), os.stat("b", dir_fd=tmp_dfd))

        # Opening an absolute symlink is done relative to the initial parent
        with open_beneath_managed(
            "d",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), os.stat("b", dir_fd=tmp_dfd))

        # Opening a symlink to a directory works
        with open_beneath_managed(
            "f",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), os.stat("a/e", dir_fd=tmp_dfd))

        with open_beneath_managed(
            "f/..",
            os.O_RDONLY,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), os.stat("a", dir_fd=tmp_dfd))

        with open_beneath_managed(
            "f/..",
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=tmp_dfd,
        ) as fd:
            assert os.path.samestat(os.stat(fd), os.stat("a", dir_fd=tmp_dfd))

        with pytest.raises(FileNotFoundError):
            nixutil.open_beneath(
                "NOEXIST",
                os.O_RDONLY,
                dir_fd=tmp_dfd,
                no_symlinks=True,
            )

        with pytest.raises(NotADirectoryError):
            nixutil.open_beneath(
                "b/a",
                os.O_RDONLY,
                dir_fd=tmp_dfd,
                no_symlinks=True,
            )

        # Symlink loop
        with pytest.raises(OSError, match="[sS]ymbolic links"):
            nixutil.open_beneath(
                "recur",
                os.O_RDONLY,
                dir_fd=tmp_dfd,
            )

        # No symlinks allowed
        with pytest.raises(OSError, match="[sS]ymbolic links"):
            nixutil.open_beneath(
                "d",
                os.O_RDONLY,
                dir_fd=tmp_dfd,
                no_symlinks=True,
            )

        with pytest.raises(OSError, match="[sS]ymbolic links"):
            nixutil.open_beneath(
                "d",
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=tmp_dfd,
            )

        with pytest.raises(OSError, match="[sS]ymbolic links"):
            nixutil.open_beneath(
                "f/..",
                os.O_RDONLY,
                dir_fd=tmp_dfd,
                no_symlinks=True,
            )

    finally:
        os.close(tmp_dfd)
