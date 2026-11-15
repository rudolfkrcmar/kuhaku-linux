#!/bin/sh
# The assistant is Claude Code on the computer itself, outside the sandbox. Without it, Tegami is plain mail.
exec flatpak-spawn --host --watch-bus claude "$@"
