#!/bin/sh
# The assistant is Claude Code on the computer itself, outside the sandbox. Without it, Tegami is plain mail.
# The host does not see the sandbox's environment: the chosen Claude account is passed on by hand.
# shellcheck disable=SC2016  # $HOME and $PATH are the host's, expanded there
set -- ${CLAUDE_CONFIG_DIR:+"--env=CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR"} sh -c 'PATH="$HOME/.local/bin:$PATH"; exec claude "$@"' claude "$@"
exec flatpak-spawn --host --watch-bus "$@"
