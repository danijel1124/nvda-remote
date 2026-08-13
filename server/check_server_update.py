#!/usr/bin/env python3
"""Manually trigger an immediate update check ("check right now"), without
waiting for the running server's own scheduled check (see server.py's
_scheduled_update_check / update_check.py). Runs both halves:

- the server's own self-update check (is there a newer server-vX.Y.Z
  release?) - check-only, logs/records the result, never applies
  anything.
- the client-release auto-detect check - if there's a newer *official*
  client vX.Y.Z release, this one *does* apply it: data/addon_release.json
  is atomically overwritten, same as running set_addon_release.py by hand.

Fully standalone - queries GitHub directly and prints the result. Does not
talk to the live server process (no IPC/signal needed); it just writes the
same data/server_update_check.json state file the running server's
scheduled check reads/writes, so the automatic timer won't immediately
redo the same check right after you've just run this by hand.

Usage (run from server/, e.g. via `docker exec <container> ...`, where
Twisted is already installed):
    python3 check_server_update.py [--data-dir data]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402 - only used for SERVER_VERSION, the single source of truth
import update_check  # noqa: E402


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--data-dir", default=server.DATA_DIR, help=f"default: {server.DATA_DIR}")
	args = parser.parse_args()

	if not os.path.isdir(args.data_dir):
		print(f"error: data dir {args.data_dir!r} does not exist - run from server/, or pass --data-dir", file=sys.stderr)
		sys.exit(1)

	server_result = update_check.check_for_update(server.SERVER_VERSION, args.data_dir, print)
	client_result = update_check.check_for_client_update(args.data_dir, print)
	sys.exit(1 if (server_result['error'] or client_result['error']) else 0)


if __name__ == "__main__":
	main()
