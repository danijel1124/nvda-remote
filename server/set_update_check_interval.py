#!/usr/bin/env python3
"""Write data/server_config.json's update_check_interval_hours - how often
the running server automatically checks GitHub for a newer server-vX.Y.Z
release (see update_check.py / server.py's _scheduled_update_check).
Default is 24 hours if this file doesn't exist or doesn't set the key.

Writes to a temp file in the same directory and os.replace()s it into
place, same atomic pattern as set_addon_release.py. No server restart
needed - the interval is read fresh on every scheduled check tick.

Usage:
    python3 set_update_check_interval.py 24
"""
import argparse
import json
import os
import sys
import tempfile

DATA_DIR = "data"
CONFIG_FILENAME = "server_config.json"


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("hours", type=float, help="e.g. 24 (must be > 0)")
	parser.add_argument("--data-dir", default=DATA_DIR, help=f"default: {DATA_DIR}")
	args = parser.parse_args()

	if args.hours <= 0:
		print("error: hours must be > 0", file=sys.stderr)
		sys.exit(1)

	if not os.path.isdir(args.data_dir):
		print(f"error: data dir {args.data_dir!r} does not exist - run from server/, or pass --data-dir", file=sys.stderr)
		sys.exit(1)

	target = os.path.join(args.data_dir, CONFIG_FILENAME)
	# Preserve any other existing keys already in server_config.json.
	existing = {}
	if os.path.exists(target):
		try:
			with open(target, 'r', encoding='utf-8') as f:
				existing = json.load(f)
		except Exception:
			existing = {}
	existing['update_check_interval_hours'] = args.hours

	fd, tmp_path = tempfile.mkstemp(dir=args.data_dir, prefix=".server_config_", suffix=".tmp")
	try:
		with os.fdopen(fd, 'w', encoding='utf-8') as f:
			json.dump(existing, f)
		os.replace(tmp_path, target)
	except BaseException:
		if os.path.exists(tmp_path):
			os.remove(tmp_path)
		raise

	print(f"Wrote {target}: update_check_interval_hours={args.hours}")
	print("No server restart needed - read fresh on every scheduled check tick.")


if __name__ == "__main__":
	main()
