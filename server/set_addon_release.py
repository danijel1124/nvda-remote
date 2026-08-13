#!/usr/bin/env python3
"""Write data/addon_release.json - the (version, url) the server pushes to
every connecting client via the addon_update message (see server.py's
ServerState.get_addon_release/User.send_addon_update).

Writes to a temp file in the same directory and os.replace()s it into place,
so a client connecting mid-write (ServerState re-reads this file on every
connection, deliberately not cached) never sees a truncated/partial file.

Usage:
    python3 set_addon_release.py 3.2 https://github.com/<owner>/<repo>/releases/download/v3.2/remote-3.2.nvda-addon

Run from the server/ directory (or pass --data-dir), typically right after a
GitHub release has been created by CI for a version tag - the url should
point at that release's actual .nvda-addon asset, which only exists once the
tag has been pushed and the release build has finished.
"""
import argparse
import json
import os
import sys
import tempfile

DATA_DIR = "data"
ADDON_RELEASE_FILE = "addon_release.json"


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("version", help='e.g. "3.2" - compared as a dotted-numeric tuple client-side')
	parser.add_argument("url", help="Direct download URL for the .nvda-addon file")
	parser.add_argument("--data-dir", default=DATA_DIR, help=f"default: {DATA_DIR}")
	args = parser.parse_args()

	if not os.path.isdir(args.data_dir):
		print(f"error: data dir {args.data_dir!r} does not exist - run from server/, or pass --data-dir", file=sys.stderr)
		sys.exit(1)

	target = os.path.join(args.data_dir, ADDON_RELEASE_FILE)
	fd, tmpPath = tempfile.mkstemp(dir=args.data_dir, prefix=".addon_release_", suffix=".tmp")
	try:
		with os.fdopen(fd, "w") as f:
			json.dump({"version": args.version, "url": args.url}, f)
		os.replace(tmpPath, target)
	except BaseException:
		if os.path.exists(tmpPath):
			os.remove(tmpPath)
		raise

	print(f"Wrote {target}: version={args.version} url={args.url}")
	print("No server restart needed - get_addon_release() reads this file fresh on every connection.")


if __name__ == "__main__":
	main()
