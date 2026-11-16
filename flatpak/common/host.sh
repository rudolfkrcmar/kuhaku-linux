#!/bin/sh
# A tool that lives on the computer itself, outside the sandbox (udisksctl to mount and eject drives).
# Installed under the tool's own name; runs the host's tool of that name.
# shellcheck disable=SC2016  # $HOME and $PATH are the host's, expanded there
exec flatpak-spawn --host --watch-bus sh -c 'PATH="$HOME/.local/bin:$PATH"; exec "$@"' sh "$(basename "$0")" "$@"
