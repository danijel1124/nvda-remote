"""Shared logic for checking GitHub for newer releases of both halves of
this repo:

- the *server* checking its own server-vX.Y.Z releases (as opposed to
  addon_update in server.py, which is the server pushing a newer *client*
  add-on to connecting clients - this module is the server checking for
  its own updates), and
- the latest *official* client vX.Y.Z release, so data/addon_release.json
  (what the server pushes to clients via addon_update) can be kept
  up to date automatically instead of requiring a manual
  set_addon_release.py run after every client release.

Used both by the running server (server.py, scheduled via a LoopingCall
that wraps the blocking call in threads.deferToThread so it never stalls
the reactor - see server.py's _scheduled_update_check) and by the
standalone check_server_update.py CLI script for an on-demand "check right
now" run by an admin.

Deliberately has no Twisted dependency, so the CLI script can run without
`pip install -r requirements.txt`.

The server-version check only checks and reports (log line + a small
state file) - it never downloads or applies anything automatically.
Unlike the client add-on (which at least auto-installs, just never
auto-restarts NVDA), the server is a live network daemon relaying active
remote-support sessions; touching the running process automatically is
out of scope entirely here. The client-release check is different in
one respect: it *does* automatically write addon_release.json when it
finds a newer official release - that file existing specifically to be
overwritten by a plain file write (no server restart, see
ServerState.get_addon_release) is the whole point of that mechanism, and
every safety net around actually applying an update already lives
client-side (strictly-newer-only, never auto-downgrade, never
auto-restart NVDA - see client/addon/globalPlugins/remoteClient/addon_update.py).
"""
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request

GITHUB_REPO = "danijel1124/nvda-remote"
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
# Distinct tag namespace from the client's plain "vX.Y.Z" tags, since both
# live in the same repo (see server/CLAUDE.md's Versioning section).
TAG_PREFIX = "server-v"
CLIENT_TAG_PREFIX = "v"
DEFAULT_INTERVAL_HOURS = 24

CONFIG_FILENAME = "server_config.json"
STATE_FILENAME = "server_update_check.json"
ADDON_RELEASE_FILENAME = "addon_release.json"  # same file server.py's ADDON_RELEASE_FILE points at
# Same file server.py's ADDON_BETA_RELEASE_FILE points at - the rolling
# nightly build, only ever pushed to a connection that opted into
# allow_beta_updates (see make_nightly.sh, server.py's
# User.allow_beta_updates).
ADDON_BETA_RELEASE_FILENAME = "addon_beta_release.json"
NIGHTLY_TAG = "nightly"


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


def _fetch_releases():
	"""Blocking. Returns the repo's full release list (all tags, both
	server-vX.Y.Z and client vX.Y.Z share this one list) as parsed JSON.
	Raises on any failure - callers are expected to catch broadly, same as
	check_for_update already did before this was factored out."""
	req = urllib.request.Request(
		GITHUB_RELEASES_API,
		headers={
			'User-Agent': 'nvda-remote-server-update-check',
			'Accept': 'application/vnd.github+json',
		},
	)
	with urllib.request.urlopen(req, timeout=10) as resp:
		return json.load(resp)


def _best_release(releases, prefix, exclude_prefixes=(), official_only=False):
	"""Highest-semver release whose tag starts with `prefix` (and none of
	`exclude_prefixes`). With official_only, skips prerelease/draft
	releases - relevant for the client check, where a deliberately
	pre-release tag (e.g. an early v3.2) must never get auto-pushed to
	every connected client. Returns (version_str, release_dict) or None.
	"""
	best = None
	for rel in releases:
		tag = rel.get('tag_name') or ''
		if not tag.startswith(prefix):
			continue
		if any(tag.startswith(p) for p in exclude_prefixes):
			continue
		if official_only and (rel.get('prerelease') or rel.get('draft')):
			continue
		version_str = tag[len(prefix):]
		parsed = _parse_version(version_str)
		if best is None or parsed > best[0]:
			best = (parsed, version_str, rel)
	if best is None:
		return None
	return best[1], best[2]


def _find_addon_asset_url(release):
	"""First .nvda-addon asset's direct download URL on a release, or
	None if it has no such asset (e.g. still building, or a source-only
	release)."""
	for asset in release.get('assets') or []:
		name = asset.get('name') or ''
		if name.endswith('.nvda-addon'):
			return asset.get('browser_download_url')
	return None


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
		releases = _fetch_releases()
		found = _best_release(releases, TAG_PREFIX)
		if found is not None:
			version_str, rel = found
			result['latest_version'] = version_str
			result['url'] = rel.get('html_url')
			result['update_available'] = _parse_version(version_str) > _parse_version(current_version)
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


def read_current_addon_release(data_dir):
	"""Returns the {'version', 'url'} currently in data/addon_release.json,
	or None if it's missing/malformed/never set - same "never raise"
	tolerance as everything else reading that file (see server.py's
	ServerState.get_addon_release, which this deliberately doesn't import
	to keep this module Twisted-free)."""
	path = os.path.join(data_dir, ADDON_RELEASE_FILENAME)
	try:
		with open(path, 'r', encoding='utf-8') as f:
			data = json.load(f)
		if not data.get('version') or not data.get('url'):
			return None
		return data
	except Exception:
		return None


def check_for_client_update(data_dir, log=None):
	"""Blocking - performs a real network call, same threading rules as
	check_for_update. Looks for the latest *official* (non-prerelease,
	non-draft) client vX.Y.Z release with a .nvda-addon asset, and - unlike
	check_for_update - actually applies it: if it's strictly newer than
	what's currently in data/addon_release.json, atomically overwrites
	that file, the same write set_addon_release.py performs by hand. Never
	downgrades (mirrors the client's own addon_update.py rule); never
	raises.

	Deliberately does not persist its own state/'checked_at' file - it
	piggybacks on the server-version check's is_check_due() gate (see
	run_scheduled_checks) rather than tracking a second independent
	schedule, since one config interval is easier to reason about than
	two silently-different ones.
	"""
	def _log(msg):
		if log is not None:
			log(msg)

	result = {
		'latest_version': None,
		'updated': False,
		'url': None,
		'error': None,
	}
	try:
		releases = _fetch_releases()
		found = _best_release(releases, CLIENT_TAG_PREFIX, exclude_prefixes=(TAG_PREFIX,), official_only=True)
		if found is None:
			_log(f"Client update check: no official {CLIENT_TAG_PREFIX}X.Y.Z release found on GitHub ({GITHUB_REPO})")
			return result
		version_str, rel = found
		url = _find_addon_asset_url(rel)
		if url is None:
			_log(f"Client update check: latest release {version_str} has no .nvda-addon asset - skipping")
			return result
		result['latest_version'] = version_str
		result['url'] = url

		current = read_current_addon_release(data_dir)
		is_newer = current is None or _parse_version(version_str) > _parse_version(current['version'])
		if not is_newer:
			_log(f"Client update check: addon_release.json already up to date ({current['version']})")
			return result

		_atomic_write_json(os.path.join(data_dir, ADDON_RELEASE_FILENAME), data_dir, {'version': version_str, 'url': url})
		result['updated'] = True
		from_version = current['version'] if current else "(none)"
		_log(f"Client update detected - addon_release.json updated: {from_version} -> {version_str} ({url})")
	except Exception as e:
		result['error'] = str(e)
		_log(f"Client update check failed: {e}")

	return result


def check_for_client_beta_update(data_dir, log=None):
	"""Blocking - same threading rules as check_for_update. Mirrors
	check_for_client_update, but for the rolling "nightly" release
	(make_nightly.sh) instead of the latest official vX.Y.Z one.

	Unlike the stable check, this doesn't rank among releases by parsed
	semver - "nightly" isn't semantically versioned at all (its own
	manifest version is a build timestamp, e.g. "nightly-20260813203214",
	which _best_release's numeric parser can't meaningfully rank anyway).
	Instead it's identified by tag name, and the actual version string to
	advertise is extracted from its .nvda-addon asset's filename
	(remote-<version>.nvda-addon), which make_nightly.sh names to match
	the manifest version it built.

	Always overwrites data/addon_beta_release.json when a nightly release
	with a usable asset is found - there's no "is this newer" gate here
	(unlike check_for_client_update): the nightly tag is a single rolling
	build, not a sequence of releases to compare against each other. The
	actual "is this newer than what's installed" decision happens
	client-side (addon_update.py), and only for a client that opted in
	via allow_beta_updates in the first place - a stable client never
	reads this file at all (see server.py's User.send_addon_update).
	"""
	def _log(msg):
		if log is not None:
			log(msg)

	result = {'version': None, 'updated': False, 'url': None, 'error': None}
	try:
		releases = _fetch_releases()
		nightly = next((r for r in releases if r.get('tag_name') == NIGHTLY_TAG), None)
		if nightly is None:
			_log(f"Beta update check: no '{NIGHTLY_TAG}' release found on GitHub ({GITHUB_REPO})")
			return result

		version_str = None
		url = None
		for asset in nightly.get('assets') or []:
			name = asset.get('name') or ''
			m = re.match(r'^remote-(.+)\.nvda-addon$', name)
			if m:
				version_str = m.group(1)
				url = asset.get('browser_download_url')
				break
		if version_str is None or url is None:
			_log(f"Beta update check: '{NIGHTLY_TAG}' release has no .nvda-addon asset - skipping")
			return result

		result['version'] = version_str
		result['url'] = url
		_atomic_write_json(os.path.join(data_dir, ADDON_BETA_RELEASE_FILENAME), data_dir, {'version': version_str, 'url': url})
		result['updated'] = True
		_log(f"Beta update available: {version_str} ({url})")
	except Exception as e:
		result['error'] = str(e)
		_log(f"Beta update check failed: {e}")

	return result


def run_scheduled_checks(server_version, data_dir, log=None):
	"""Entry point for the running server's scheduled LoopingCall (see
	server.py's _scheduled_update_check), the manual check_server_update.py
	CLI script, and the admin-triggered do_admin_check_for_updates command:
	runs the server's own self-update check, the stable client-release
	auto-detect/apply check, and the beta (nightly) one, under the one
	configured interval (is_check_due, gated by the caller - this function
	itself doesn't check due-ness, it just performs all three
	unconditionally when called - an explicit "check now" request should
	never be silently skipped because the timer isn't due yet). The beta
	check runs regardless of whether any client is currently opted in, so
	data/addon_beta_release.json is already fresh the moment someone
	does opt in.

	Returns {'server': ..., 'client': ..., 'client_beta': ...} - see
	check_for_update/check_for_client_update/check_for_client_beta_update
	respectively - so callers that need the fresh results (the admin
	GUI's "check now" button) don't have to re-read state files
	themselves.
	"""
	server_result = check_for_update(server_version, data_dir, log)
	client_result = check_for_client_update(data_dir, log)
	client_beta_result = check_for_client_beta_update(data_dir, log)
	return {'server': server_result, 'client': client_result, 'client_beta': client_beta_result}
