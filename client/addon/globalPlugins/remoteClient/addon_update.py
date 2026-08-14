"""Automatic self-update, pushed by the control server.

The relay server can tell every connecting client "here is the version you
should be running, and where to get it" (the addon_update message - see
server.py's send_addon_update/ServerState.get_addon_release, sent
unconditionally on every connection, like motd, before join/authorization).
If that's newer than what this install last handled, download it and install
it automatically - no confirmation dialog, that's the point of "automatic".

Two things this deliberately does NOT do:
- Never downgrade automatically: only a strictly newer version is acted on -
  with one deliberate exception, _checkAndOffer's channel-switch case: a
  client that opted into beta, landed on a nightly build, and then unchecked
  the box must still be able to receive ordinary stable releases afterwards,
  even though a nightly always outranks a stable version by version number
  alone (see _parseVersion). Without that exception, trying beta once would
  be a one-way door out of all future stable updates.
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


NIGHTLY_PREFIX = "nightly-"  # see ../../../../make_nightly.sh


def _parseVersion(version):
	"""Best-effort version parse for comparison. Returns None if it doesn't
	look like either recognized shape, so callers can fall back to "don't
	act" instead of guessing at an ordering for e.g. a garbled version
	string.

	Two shapes, given a (rank, payload) tuple so they compare consistently
	against each other via plain tuple comparison (Python can't compare a
	str payload against an int-tuple payload directly, hence the rank):
	- Dotted-numeric stable releases, e.g. "3.2.3.1" -> (0, (3, 2, 3, 1)).
	- Nightly builds, e.g. "nightly-20260813203214" -> (1, "20260813203214").
	  Rank 1 always outranks rank 0: a nightly is built from the current
	  HEAD, which is always at least as new as the latest tagged stable
	  release, so any nightly counts as newer than any stable version. Two
	  nightlies compare by their zero-padded UTC timestamp suffix, which
	  sorts correctly as a plain string. This only ever matters for a
	  client that opted into beta updates (settings_panel.py) - a client
	  that didn't never receives a nightly version string to compare
	  against in the first place (see server.py's User.allow_beta_updates).
	"""
	s = str(version).strip()
	if s.startswith(NIGHTLY_PREFIX):
		suffix = s[len(NIGHTLY_PREFIX):]
		if suffix.isdigit():
			return (1, suffix)
		return None
	try:
		return (0, tuple(int(part) for part in s.split(".")))
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
	conf = configuration.get_config()
	state = conf["addon_update"]
	lastHandled = state["last_handled_version"]
	installedVersion = addonHandler.getCodeAddon().version
	# Explicit channel switch back to stable: _isNewer's rank tuple makes any
	# nightly outrank any stable version unconditionally (see _parseVersion),
	# which is correct while beta updates stay opted in but would otherwise
	# be a one-way door - a user who tries beta once and then unchecks the
	# box would never receive another stable update, ever, because the
	# nightly they're running (or last handled) always compares as "newer"
	# than every future stable release.
	#
	# This exception is attached to *each* gate separately, keyed on that
	# gate's own operand, rather than bypassing both gates together - the two
	# operands go stale independently. installedVersion keeps reporting the
	# nightly until NVDA actually restarts (see configuration.py's comment on
	# last_handled_version), which can be an arbitrarily long time after a
	# stable version was already downloaded and installed pending restart
	# (the restart offer can be declined). If the switch-back bypassed both
	# gates together, every reconnect in that window would re-download and
	# re-nag with another restart dialog, because installedVersion alone
	# would keep tripping the exception even though lastHandled already
	# proves this exact version was handled. Keying gate 1 on lastHandled's
	# own rank means _markHandled() (which stores the plain stable version
	# string) closes that gate for good once handled, while gate 2 staying
	# open on installedVersion's rank still lets a *later* stable release
	# install during that same pending-restart window.
	switchingBackToStable = (
		not state["allow_beta_updates"]
		and (_parseVersion(version) or (None,))[0] == 0
	)
	if lastHandled and not _isNewer(version, lastHandled):
		lastHandledRank = (_parseVersion(lastHandled) or (0,))[0]
		if not (switchingBackToStable and lastHandledRank == 1):
			# Already installed (or already tried and failed) this exact
			# version or newer - checked first and authoritative, regardless
			# of what the installed version currently reports. See the
			# module docstring.
			return
	installedRank = (_parseVersion(installedVersion) or (0,))[0]
	if not _isNewer(version, installedVersion):
		if not (switchingBackToStable and installedRank == 1):
			return
	_startDownload(version, url)


def _startDownload(version, url):
	global _checking
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
