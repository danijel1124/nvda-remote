"""Logic-level regression tests for addon_update.py (server-pushed self-update).

Reuses the wx/gui/addonHandler/logHandler/globalVars stubs and the
bypass-remoteClient/__init__.py import trick from test_menu_and_dialogs.py -
see that file's module docstring for why this is necessary (client.py and
anything importing it cannot be loaded outside a real NVDA process).

Run with the system python3:
    python3 -m unittest discover -s tests -v
"""
import io
import sys
import types
import unittest
import urllib.error
from unittest import mock

from test_menu_and_dialogs import (  # noqa: F401 - side effect: installs stubs
	_install_stub_modules,
	configuration,
)


def _load_addon_update_module():
	_install_stub_modules()
	pkg = sys.modules["remoteClient"]

	if "ui" not in sys.modules:
		ui = types.ModuleType("ui")
		ui.message = lambda *a, **kw: None
		sys.modules["ui"] = ui

	if "core" not in sys.modules:
		core = types.ModuleType("core")
		core.restart = mock.MagicMock(name="restart")
		sys.modules["core"] = core

	addonHandler = sys.modules["addonHandler"]
	if not hasattr(addonHandler, "getCodeAddon"):
		class _FakeAddon:
			version = "3.1"

			def requestRemove(self):
				pass

		addonHandler._fakeInstalledVersion = "3.1"
		addonHandler.getCodeAddon = lambda: _FakeAddon()
		addonHandler.AddonBundle = mock.MagicMock(name="AddonBundle")
		addonHandler.installAddonBundle = mock.MagicMock(name="installAddonBundle")

	import remoteClient.addon_update as addon_update  # noqa: F401
	return addon_update


addon_update = _load_addon_update_module()


class _SyncThread:
	"""Stands in for threading.Thread: runs the target synchronously on
	.start() instead of spawning a real thread, so tests are deterministic
	and don't need to join()/poll for completion."""

	def __init__(self, target=None, args=(), kwargs=None, daemon=None):
		self._target = target
		self._args = args
		self._kwargs = kwargs or {}

	def start(self):
		self._target(*self._args, **self._kwargs)


class VersionComparisonTests(unittest.TestCase):
	def test_dotted_versions_compare_numerically_not_lexically(self):
		# Lexical string comparison would get "3.10" < "3.9" wrong.
		self.assertTrue(addon_update._isNewer("3.10", "3.9"))
		self.assertFalse(addon_update._isNewer("3.9", "3.10"))

	def test_equal_versions_are_not_newer(self):
		self.assertFalse(addon_update._isNewer("3.2", "3.2"))

	def test_unparsable_version_is_never_newer(self):
		self.assertFalse(addon_update._isNewer("not-a-version", "3.1"))
		self.assertFalse(addon_update._isNewer("3.2", "not-a-version"))


class CheckAndOfferGatingTests(unittest.TestCase):
	def setUp(self):
		# Fresh config state per test - get_config() is a module-level
		# singleton in configuration.py.
		conf = configuration.get_config()
		conf["addon_update"]["last_handled_version"] = ""
		conf["addon_update"]["last_handled_failed"] = False
		self._threadPatch = mock.patch.object(addon_update.threading, "Thread", _SyncThread)
		self._threadPatch.start()
		self.addCleanup(self._threadPatch.stop)
		self._checkingPatch = mock.patch.object(addon_update, "_checking", False)
		self._checkingPatch.start()
		self.addCleanup(self._checkingPatch.stop)

	def test_not_newer_than_installed_does_not_download(self):
		with mock.patch.object(addon_update, "_downloadAndInstall") as dl:
			addon_update._checkAndOffer("3.1", "https://example.org/x.nvda-addon")
			dl.assert_not_called()

	def test_newer_than_installed_downloads(self):
		with mock.patch.object(addon_update, "_downloadAndInstall") as dl:
			addon_update._checkAndOffer("3.2", "https://example.org/x.nvda-addon")
			dl.assert_called_once_with("3.2", "https://example.org/x.nvda-addon")

	def test_thread_spawn_failure_does_not_wedge_checking_flag(self):
		"""If Thread()/.start() itself raises, _downloadAndInstall's own
		finally (which normally clears _checking) never runs - _checkAndOffer
		must clear it itself, or this client would never attempt an update
		again for the rest of the NVDA session."""
		with mock.patch.object(addon_update.threading, "Thread", side_effect=RuntimeError("can't start thread")):
			addon_update._checkAndOffer("3.2", "https://example.org/x.nvda-addon")
		self.assertFalse(addon_update._checking)

	def test_already_handled_version_is_not_retried(self):
		configuration.get_config()["addon_update"]["last_handled_version"] = "3.2"
		with mock.patch.object(addon_update, "_downloadAndInstall") as dl:
			# Same version pushed again (e.g. on a reconnect) - even though
			# it's newer than the still-installed 3.1 (pending restart), this
			# must not re-trigger: last_handled_version is checked first.
			addon_update._checkAndOffer("3.2", "https://example.org/x.nvda-addon")
			dl.assert_not_called()

	def test_failed_attempt_is_not_retried_on_the_same_version(self):
		configuration.get_config()["addon_update"]["last_handled_version"] = "3.2"
		configuration.get_config()["addon_update"]["last_handled_failed"] = True
		with mock.patch.object(addon_update, "_downloadAndInstall") as dl:
			addon_update._checkAndOffer("3.2", "https://example.org/x.nvda-addon")
			dl.assert_not_called()

	def test_newer_release_after_a_failed_one_is_still_tried(self):
		configuration.get_config()["addon_update"]["last_handled_version"] = "3.2"
		configuration.get_config()["addon_update"]["last_handled_failed"] = True
		with mock.patch.object(addon_update, "_downloadAndInstall") as dl:
			addon_update._checkAndOffer("3.3", "https://example.org/x.nvda-addon")
			dl.assert_called_once_with("3.3", "https://example.org/x.nvda-addon")

	def test_missing_version_or_url_is_ignored(self):
		with mock.patch.object(addon_update, "_checkAndOffer") as check:
			addon_update.handleAddonUpdate(version=None, url="https://example.org/x.nvda-addon")
			addon_update.handleAddonUpdate(version="3.2", url=None)
			check.assert_not_called()


class DownloadAndInstallTests(unittest.TestCase):
	def setUp(self):
		conf = configuration.get_config()
		conf["addon_update"]["last_handled_version"] = ""
		conf["addon_update"]["last_handled_failed"] = False

	def test_successful_install_marks_version_handled_and_announces(self):
		fakeResponse = io.BytesIO(b"fake addon bytes")
		fakeResponse.__enter__ = lambda self=fakeResponse: fakeResponse
		fakeResponse.__exit__ = lambda self, *a: False
		with mock.patch.object(addon_update.urllib.request, "urlopen", return_value=fakeResponse), \
			mock.patch.object(addon_update.wx, "CallAfter", side_effect=lambda f, *a: f(*a)):
			addon_update._downloadAndInstall("3.2", "https://example.org/x.nvda-addon")
		conf = configuration.get_config()
		self.assertEqual(conf["addon_update"]["last_handled_version"], "3.2")
		self.assertFalse(conf["addon_update"]["last_handled_failed"])
		self.assertFalse(addon_update._checking)

	def test_offers_restart_and_restarts_on_yes(self):
		fakeResponse = io.BytesIO(b"fake addon bytes")
		with mock.patch.object(addon_update.urllib.request, "urlopen", return_value=fakeResponse), \
			mock.patch.object(addon_update.wx, "CallAfter", side_effect=lambda f, *a: f(*a)), \
			mock.patch.object(addon_update.gui, "messageBox", return_value=addon_update.wx.YES) as mockBox, \
			mock.patch.object(addon_update, "core") as mockCore:
			addon_update._downloadAndInstall("3.2", "https://example.org/x.nvda-addon")
		mockBox.assert_called_once()
		mockCore.restart.assert_called_once_with()

	def test_does_not_restart_on_no(self):
		"""The restart must stay opt-in - declining the offer must not
		restart NVDA out from under the user anyway."""
		fakeResponse = io.BytesIO(b"fake addon bytes")
		with mock.patch.object(addon_update.urllib.request, "urlopen", return_value=fakeResponse), \
			mock.patch.object(addon_update.wx, "CallAfter", side_effect=lambda f, *a: f(*a)), \
			mock.patch.object(addon_update.gui, "messageBox", return_value=addon_update.wx.NO), \
			mock.patch.object(addon_update, "core") as mockCore:
			addon_update._downloadAndInstall("3.2", "https://example.org/x.nvda-addon")
		mockCore.restart.assert_not_called()

	def test_successful_install_removes_the_old_add_on(self):
		"""installAddonBundle() only extracts the new version - by itself it
		doesn't know an add-on with the same ID is already installed. Without
		requestRemove() on the old one, NVDA would restart into two installed
		copies of this add-on."""
		fakeResponse = io.BytesIO(b"fake addon bytes")
		oldAddon = mock.MagicMock()
		with mock.patch.object(addon_update.urllib.request, "urlopen", return_value=fakeResponse), \
			mock.patch.object(addon_update.addonHandler, "getCodeAddon", return_value=oldAddon):
			addon_update._downloadAndInstall("3.2", "https://example.org/x.nvda-addon")
		oldAddon.requestRemove.assert_called_once_with()

	def test_download_failure_marks_version_handled_as_failed_not_looped(self):
		with mock.patch.object(
			addon_update.urllib.request, "urlopen",
			side_effect=urllib.error.URLError("network unreachable"),
		):
			addon_update._downloadAndInstall("3.2", "https://example.org/x.nvda-addon")
		conf = configuration.get_config()
		self.assertEqual(conf["addon_update"]["last_handled_version"], "3.2")
		self.assertTrue(conf["addon_update"]["last_handled_failed"])
		self.assertFalse(addon_update._checking)

	def test_install_failure_is_also_recorded_as_failed(self):
		fakeResponse = io.BytesIO(b"fake addon bytes")
		fakeResponse.__enter__ = lambda self=fakeResponse: fakeResponse
		fakeResponse.__exit__ = lambda self, *a: False
		with mock.patch.object(addon_update.urllib.request, "urlopen", return_value=fakeResponse), \
			mock.patch.object(addon_update.addonHandler, "installAddonBundle", side_effect=RuntimeError("bad bundle")):
			addon_update._downloadAndInstall("3.2", "https://example.org/x.nvda-addon")
		conf = configuration.get_config()
		self.assertEqual(conf["addon_update"]["last_handled_version"], "3.2")
		self.assertTrue(conf["addon_update"]["last_handled_failed"])


if __name__ == "__main__":
	unittest.main()
