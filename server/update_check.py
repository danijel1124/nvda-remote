"""Shared logic for checking whether a newer *server* release exists on
GitHub (as opposed to addon_update in server.py, which is the server
pushing a newer *client* add-on to connecting clients - this is the
server checking for its own updates).

Used both by the running server (server.py, scheduled via a LoopingCall
that wraps the blocking call in threads.deferToThread so it never stalls
the reactor - see server.py's _scheduled_update_check) and by the
standalone check_server_update.py CLI script for an on-demand "check right
now" run by an admin.

Deliberately has no Twisted dependency, so the CLI script can run without
`pip install -r requirements.txt`.

This module only checks and reports (log line + a small state file) - it
never downloads or applies anything automatically. Unlike the client
add-on (which at least auto-installs, just never auto-restarts NVDA), the
server is a live network daemon relaying active remote-support sessions;
touching the running process automatically is out of scope entirely here.
"""
import json
import os
import tempfile
import time
import urllib.error
import urllib.request

GITHUB_REPO = "danijel1124/nvda-remote"
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
# Distinct tag namespace from the client's plain "vX.Y.Z" tags, since both
# live in the same repo (see server/CLAUDE.md's Versioning section).
TAG_PREFIX = "server-v"
DEFAULT_INTERVAL_HOURS = 24

CONFIG_FILENAME = "server_config.json"
STATE_FILENAME = "server_update_check.json"


def _parse_version(v):
	"""'1.2.3' -> (1, 2, 3). Non-numeric/missing parts become 0, same
	tolerant spirit as the client's addon_update.py version parser."""
	parts = []
	for p in v.strip().split('.'):
		try:
			parts.append(int(p))
		except ValueError:
			parts.append(0)
	return tuple(parts)


def _atomic_write_json(path, data_dir, data):
	fd, tmp_path = tempfile.mkstemp(dir=data_dir, prefix=".update_check_", suffix=".tmp")
	try:
		with os.fdopen(fd, 'w', encoding='utf-8') as f:
			json.dump(data, f)
		os.replace(tmp_path, path)
	except BaseException:
		if os.path.exists(tmp_path):
			os.remove(tmp_path)
		raise


def get_configured_interval_hours(data_dir):
	"""Reads data/server_config.json fresh on every call - never raises,
	defaults to DEFAULT_INTERVAL_HOURS if the file is missing, malformed,
	or has a non-positive value. Same "just a file write, no restart
	needed" pattern as server.py's ServerState.get_addon_release()."""
	path = os.path.join(data_dir, CONFIG_FILENAME)
	try:
		with open(path, 'r', encoding='utf-8') as f:
			data = json.load(f)
		hours = float(data.get('update_check_interval_hours', DEFAULT_INTERVAL_HOURS))
		if hours <= 0:
			return DEFAULT_INTERVAL_HOURS
		return hours
	except Exception:
		return DEFAULT_INTERVAL_HOURS


def read_last_check(data_dir):
	"""Returns the last-persisted check result dict, or None if there has
	never been one (or the state file is missing/corrupt)."""
	path = os.path.join(data_dir, STATE_FILENAME)
	try:
		with open(path, 'r', encoding='utf-8') as f:
			return json.load(f)
	except Exception:
		return None


def is_check_due(data_dir):
	"""True if there is no previous check on record, or the configured
	interval has elapsed since the last one."""
	last = read_last_check(data_dir)
	if not last or 'checked_at' not in last:
		return True
	interval_seconds = get_configured_interval_hours(data_dir) * 3600
	return (time.time() - last['checked_at']) >= interval_seconds


def check_for_update(current_version, data_dir, log=None):
	"""Blocking - performs a real network call. Callers running inside the
	Twisted reactor thread must wrap this in threads.deferToThread.

	Never raises: any failure (network error, malformed API response) is
	caught and reported as a non-fatal result with an 'error' field, since
	this must never be able to take down the server process or the
	calling script.
	"""
	def _log(msg):
		if log is not None:
			log(msg)

	result = {
		'checked_at': time.time(),
		'current_version': current_version,
		'latest_version': None,
		'update_available': False,
		'url': None,
		'error': None,
	}
	try:
		req = urllib.request.Request(
			GITHUB_RELEASES_API,
			headers={
				'User-Agent': 'nvda-remote-server-update-check',
				'Accept': 'application/vnd.github+json',
			},
		)
		with urllib.request.urlopen(req, timeout=10) as resp:
			releases = json.load(resp)
		best_version = None
		best_version_str = None
		best_url = None
		for rel in releases:
			tag = rel.get('tag_name') or ''
			if not tag.startswith(TAG_PREFIX):
				continue
			version_str = tag[len(TAG_PREFIX):]
			parsed = _parse_version(version_str)
			if best_version is None or parsed > best_version:
				best_version = parsed
				best_version_str = version_str
				best_url = rel.get('html_url')
		if best_version is not None:
			result['latest_version'] = best_version_str
			result['url'] = best_url
			result['update_available'] = best_version > _parse_version(current_version)
	except Exception as e:
		# Deliberately catches everything (network errors, JSON errors,
		# unexpected API shape) - a failed update check is not something
		# that should ever propagate and disrupt the caller.
		result['error'] = str(e)

	try:
		_atomic_write_json(os.path.join(data_dir, STATE_FILENAME), data_dir, result)
	except Exception:
		pass  # persisting is best-effort; still log/return the result below

	if result['error']:
		_log(f"Server update check failed: {result['error']}")
	elif result['latest_version'] is None:
		_log(f"Server update check: no {TAG_PREFIX}X.Y.Z release found on GitHub ({GITHUB_REPO})")
	elif result['update_available']:
		_log(f"Server update available: {current_version} -> {result['latest_version']} ({result['url']})")
	else:
		_log(f"Server update check: up to date ({current_version})")

	return result
