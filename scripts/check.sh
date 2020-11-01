#!/bin/bash
cd "$(dirname -- "$(dirname -- "$(readlink -f "$0")")")"

for cmd in flake8 isort mypy pylint pytype; do
    if [[ ! -x "$(which "$cmd")" ]]; then
        echo "Could not find $cmd. Please make sure that flake8, isort, mypy, pylint, and pytype are all installed."
        exit 1
    fi
done

flake8 nixutil tests && isort --check nixutil tests && mypy --strict -p nixutil -p tests && pytype nixutil tests && pylint nixutil tests
