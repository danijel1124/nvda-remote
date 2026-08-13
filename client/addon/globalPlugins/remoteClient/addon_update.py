"""Automatic self-update, pushed by the control server.

The relay server can tell every connecting client "here is the version you
should be running, and where to get it" (the addon_update message - see
server.py's send_addon_update/ServerState.get_addon_release, sent
unconditionally on every connection, like motd, before join/authorization).
If that's newer than what this install last handled, download it and install
it automatically - no confirmation dialog, that's the point of "automatic".

Two things this deliberately does NOT do:
- Never downgrade automatically: only a strictly newer version is acted on.
- Never restart NVDA automatically: installAddonBundle only marks the update
  as pending until NVDA restarts (NVDA can't hot-swap already-imported code),
  and silently restarting out from under a screen-reader user mid-task would
  be worse than a delayed update. We announce and *offer* an immediate
  restart (a Yes/No dialog, core.restart() only on an explicit Yes) rather
  than forcing one - the choice of when stays the user's, just one click
  away instead of a manual detour through NVDA's own restart command.

See client/CLAUDE.md for the full design (including why last_handled_version
must be checked *before* the installed version, not just as a dedup).
"""
import os
import tempfile
import threading
import urllib.error
import urllib.request

import addonHandler
import core
import gui
import ui
import wx
from logHandler import log

from . import configuration

# Guards against a second download+install starting while one is already
# in flight - a multi-second window (network download), not just the instant
# of the push itself: master and slave both receive addon_update
# independently (once each, and again on every silent ConnectorThread
# reconnect), and _checkAndOffer itself always runs serialized on the wx main
# thread (transport.py's parse() wraps every inbound handler in
# wx.CallAfter), so this is what actually prevents two overlapping downloads,
# not last_handled_version - that's only written once the first one finishes.
_checking = False


def _parseVersion(version):
	"""Best-effort dotted-numeric version parse for comparison. Returns None
	if it doesn't look like one, so callers can fall back to "don't act"
	instead of guessing at an ordering for e.g. a garbled version string."""
	try:
		return tuple(int(part) for part in str(version).strip().split("."))
	except (ValueError, AttributeError):
		return None


def _isNewer(candidate, baseline):
	"""True only if candidate is unambiguously newer than baseline. An
	unparsable version on either side never counts as "newer" - silently
	doing nothing is the safe failure mode here, a false-positive auto-update
	off a garbled version string is not."""
	candidateParsed = _parseVersion(candidate)
	baselineParsed = _parseVersion(baseline)
	if candidateParsed is None or baselineParsed is None:
		return False
	return candidateParsed > baselineParsed


def handleAddonUpdate(version=None, url=None):
	"""Registered on both MasterSession and SlaveSession (RemoteSession base,
	alongside handleMOTD) - already running on the wx main thread, transport.py's
	parse() wraps every inbound handler in wx.CallAfter."""
	if not version or not url:
		return
	_checkAndOffer(version, url)


def _checkAndOffer(version, url):
	global _checking
	conf = configuration.get_config()
	state = conf["addon_update"]
	lastHandled = state["last_handled_version"]
	if lastHandled and not _isNewer(version, lastHandled):
		# Already installed (or already tried and failed) this exact version
		# or newer - checked first and authoritative, regardless of what the
		# installed version currently reports. See the module docstring.
		return
	installedVersion = addonHandler.getCodeAddon().version
	if not _isNewer(version, installedVersion):
		return
	if _checking:
		return
	_checking = True
	try:
		thread = threading.Thread(
			target=_downloadAndInstall, args=(version, url), daemon=True
		)
		thread.start()
	except Exception:
		# If Thread()/.start() itself raises, _downloadAndInstall's own
		# finally (which normally clears this) never runs - clear it here so
		# a spawn failure doesn't permanently wedge this client out of ever
		# updating again for the rest of the NVDA session.
		_checking = False
		log.error(f"NVDA Remote: failed to start update thread for {version}", exc_info=True)


def _downloadAndInstall(version, url):
	global _checking
	tmpPath = None
	try:
		log.info(f"NVDA Remote: downloading update {version} from {url}")
		# Deliberately plain urllib with normal system cert validation - this
		# is a release-hosting URL, not the relay connection that announced
		# it, and must never inherit that connection's insecure/trust-
		# fingerprint handling (a stale/compromised relay config controlling
		# an admin-set url must not become "disable cert checks on whatever
		# host we download and auto-install from").
		with urllib.request.urlopen(url, timeout=60) as response:
			data = response.read()
		fd, tmpPath = tempfile.mkstemp(suffix=".nvda-addon")
		with os.fdopen(fd, "wb") as f:
			f.write(data)
		bundle = addonHandler.AddonBundle(tmpPath)
		# installAddonBundle() only extracts the new version to a pending-
		# install path - by itself it neither knows nor cares that an add-on
		# with the same ID is already installed (confirmed by reading its
		# source: this is documented as the caller's responsibility).
		# requestRemove() only flips a "pending remove on restart" flag, the
		# same pattern gui.addonGui.installAddon uses (prevAddon.requestRemove()
		# after a successful install) - without it, NVDA would restart into
		# two installed copies of this add-on.
		oldAddon = addonHandler.getCodeAddon()
		addonHandler.installAddonBundle(bundle)
		oldAddon.requestRemove()
		_markHandled(version, failed=False)
		wx.CallAfter(_announceInstalled, version)
	except Exception:
		# Covers network errors, a bundle NVDA refuses (bad file, version-
		# incompatible manifest, ...), and anything else - marking it handled
		# either way means a bad release costs each client one failed
		# attempt, not a retry loop on every reconnect.
		log.error(f"NVDA Remote: failed to download/install update {version}", exc_info=True)
		_markHandled(version, failed=True)
	finally:
		_checking = False
		if tmpPath and os.path.exists(tmpPath):
			try:
				os.remove(tmpPath)
			except OSError:
				pass


def _markHandled(version, failed):
	conf = configuration.get_config()
	conf["addon_update"]["last_handled_version"] = version
	conf["addon_update"]["last_handled_failed"] = failed
	conf.write()


def _announceInstalled(version):
	# Translators: presented after NVDA Remote silently downloaded and
	# installed a newer version pushed by the control server. Restarting is
	# required to actually run it; offered as a one-click choice rather than
	# forced or left as a manual "remember to restart later" task.
	msg = _(
		"NVDA Remote has been updated to version {version}. "
		"Do you want to restart NVDA now to start using it?"
	).format(version=version)
	ui.message(msg)
	result = gui.messageBox(msg, _("NVDA Remote Updated"), wx.YES_NO | wx.ICON_INFORMATION)
	if result == wx.YES:
		core.restart()
