"""Logic-level regression tests for menu.py / dialogs.py / connection_info.py.

client.py itself (and everything importing it, including __init__.py) cannot be
unit-tested outside a real NVDA process: it imports ctypes.wintypes, api,
braille, core, keyboardHandler, winUser, utils.security etc, none of which
exist as installable packages. That gap is why RemoteClient's own methods
(handle_error/handle_session_list/connectToTarget/...) were verified by
code-reading with the advisor tool instead - see the client CLAUDE.md and the
project's git history for that reasoning. This file covers what *can* run
without NVDA: menu.py's item-enablement state machine and dialogs.py's pure
selection/description logic, by installing minimal stand-ins for wx/gui/
addonHandler/logHandler/globalVars in sys.modules before importing the addon
package's submodules directly (bypassing remoteClient/__init__.py, which pulls
in client.py and would otherwise make this impossible too).

Run with the system python3 (needs configobj+validate, not NVDA-only libs):
    python3 -m unittest discover -s client/tests -v
"""
import builtins
import importlib.util
import os
import sys
import tempfile
import types
import unittest

ADDON_DIR = os.path.abspath(
	os.path.join(os.path.dirname(__file__), "..", "addon", "globalPlugins")
)
PKG_DIR = os.path.join(ADDON_DIR, "remoteClient")


def _install_stub_modules():
	if "_" not in builtins.__dict__:
		builtins._ = lambda s: s  # NVDA's translation function, passthrough here

	if "globalVars" not in sys.modules:
		globalVars = types.ModuleType("globalVars")

		class _AppArgs:
			configPath = tempfile.mkdtemp(prefix="remote_client_test_config_")
			secure = False

		globalVars.appArgs = _AppArgs()
		sys.modules["globalVars"] = globalVars

	if "addonHandler" not in sys.modules:
		addonHandler = types.ModuleType("addonHandler")

		class AddonError(Exception):
			pass

		addonHandler.AddonError = AddonError
		addonHandler.initTranslation = lambda: None
		sys.modules["addonHandler"] = addonHandler

	if "logHandler" not in sys.modules:
		logHandler = types.ModuleType("logHandler")

		class _Log:
			def warning(self, *a, **kw): pass
			def info(self, *a, **kw): pass
			def error(self, *a, **kw): pass
			def debug(self, *a, **kw): pass
			def exception(self, *a, **kw): pass

		logHandler.log = _Log()
		sys.modules["logHandler"] = logHandler

	if "wx" not in sys.modules:
		wx = types.ModuleType("wx")

		class _Stateful:
			"""Stands in for wx.MenuItem/wx.Window-ish objects: tracks the one
			thing the code under test actually reads back - Enable()/Check()
			state - instead of silently swallowing it like a bare Mock would."""
			def __init__(self, *a, **kw):
				self.enabled = True
				self.checked = False
				self.Id = id(self)
			def Enable(self, state=True):
				self.enabled = state
			def Check(self, state=True):
				self.checked = state
			def IsChecked(self):
				return self.checked
			def Bind(self, *a, **kw): pass
			def Destroy(self): pass

		class Menu:
			def __init__(self, *a, **kw):
				self._items = []
			def Append(self, id, label="", help="", kind=None):
				item = _Stateful()
				self._items.append(item)
				return item
			def Remove(self, id): pass
			def Destroy(self): pass
			def AppendSubMenu(self, submenu, label, help=""):
				return _Stateful()

		class ListBox:
			def __init__(self, parent, id, choices=None):
				self._choices = list(choices or [])
				self._selection = -1
				self.enabled = True
			def SetSelection(self, idx):
				self._selection = idx
			def GetSelection(self):
				return self._selection
			def Enable(self, state=True):
				self.enabled = state
			def SetFocus(self): pass
			def Append(self, s):
				self._choices.append(s)

		class ComboBox:
			def __init__(self, parent, id, value=""):
				self._value = value
				self._items = []
			def GetValue(self):
				return self._value
			def SetValue(self, v):
				self._value = v
			def Append(self, s):
				self._items.append(s)
			def SetFocus(self): pass

		class Dialog:
			def __init__(self, parent=None, id=None, title=""):
				self._title = title
			def CreateButtonSizer(self, flags):
				return _Stateful()
			def SetSizer(self, sizer): pass
			def SetSizerAndFit(self, sizer): pass
			def Center(self, *a, **kw): pass
			def Fit(self): pass

		class BoxSizer:
			def __init__(self, *a, **kw): pass
			def Add(self, *a, **kw): pass
			def Fit(self, *a, **kw): pass

		class StaticText:
			def __init__(self, *a, **kw): pass

		class MessageDialog:
			def __init__(self, *a, **kw): pass
			def SetYesNoLabels(self, *a, **kw): pass

		def FindWindowById(id, parent=None):
			return _Stateful()

		class CommandEvent:
			def __init__(self, *a, **kw): pass
			def Skip(self): pass

		class Window:
			pass

		wx.Menu = Menu
		wx.MenuItem = _Stateful
		wx.CommandEvent = CommandEvent
		wx.Window = Window
		wx.ListBox = ListBox
		wx.ComboBox = ComboBox
		wx.Dialog = Dialog
		wx.BoxSizer = BoxSizer
		wx.StaticText = StaticText
		wx.MessageDialog = MessageDialog
		wx.FindWindowById = FindWindowById
		wx.NOT_FOUND = -1
		wx.ID_ANY = -1
		wx.ID_OK = 5100
		wx.ID_CANCEL = 5101
		# Real bit flags (these get OR'd together, e.g. wx.OK | wx.CANCEL) -
		# distinct powers of two so a wrong-flag bug would show up as an
		# unexpected combined value instead of silently colliding at 0/None.
		for i, name in enumerate((
			"VERTICAL", "HORIZONTAL", "ALL", "EXPAND", "BOTTOM", "ALIGN_RIGHT",
			"OK", "CANCEL", "YES", "NO", "YES_NO", "CANCEL_DEFAULT", "CENTRE",
			"ICON_ERROR", "ICON_WARNING", "ICON_EXCLAMATION", "ICON_INFORMATION", "ITEM_CHECK",
			"CENTER_ON_SCREEN", "NO_DEFAULT", "BOTH", "Center",
		)):
			setattr(wx, name, 1 << i)
		# Event-binding tokens - never OR'd, just passed through to Bind().
		wx.EVT_BUTTON = "EVT_BUTTON"
		wx.EVT_MENU = "EVT_MENU"
		wx.version = lambda: ("4", "0", "0")
		# Synchronous by default (real wx.CallAfter defers to the next event
		# loop iteration) - fine for tests driving handlers directly; tests
		# that care about the deferral itself patch this per-test.
		wx.CallAfter = lambda f, *a, **kw: f(*a, **kw)
		sys.modules["wx"] = wx

	if "gui" not in sys.modules:
		wx = sys.modules["wx"]
		gui = types.ModuleType("gui")

		class _ToolsMenu:
			def AppendSubMenu(self, menu, label, help=""):
				return wx.MenuItem()
			def Remove(self, id): pass

		class _SysTrayIcon:
			toolsMenu = _ToolsMenu()
			def Bind(self, *a, **kw): pass

		class _MainFrame:
			sysTrayIcon = _SysTrayIcon()

		gui.mainFrame = _MainFrame()
		gui.messageBox = lambda *a, **kw: None

		def runScriptModalDialog(dlg, callback=None):
			if callback:
				callback(getattr(dlg, "_test_result", wx.ID_CANCEL))
		gui.runScriptModalDialog = runScriptModalDialog

		class _SettingsDialogs:
			class NVDASettingsDialog:
				categoryClasses = []
		gui.settingsDialogs = _SettingsDialogs()

		class _GuiHelper:
			class BoxSizerHelper:
				def __init__(self, *a, **kw): pass
				def addItem(self, *a, **kw): pass
		gui.guiHelper = _GuiHelper()
		sys.modules["gui"] = gui


def _load_remoteclient_submodules():
	"""Import menu/dialogs/connection_info/configuration directly, bypassing
	remoteClient/__init__.py (which imports client.py, which imports
	ctypes.wintypes and other Windows-only NVDA modules and cannot be loaded
	outside NVDA on any platform)."""
	_install_stub_modules()
	if "remoteClient" not in sys.modules:
		pkg = types.ModuleType("remoteClient")
		pkg.__path__ = [PKG_DIR]
		pkg.__package__ = "remoteClient"
		sys.modules["remoteClient"] = pkg
	import remoteClient.connection_info as connection_info  # noqa: F401
	import remoteClient.configuration as configuration  # noqa: F401
	import remoteClient.menu as menu  # noqa: F401
	import remoteClient.dialogs as dialogs  # noqa: F401
	return connection_info, configuration, menu, dialogs


connection_info, configuration, menu, dialogs = _load_remoteclient_submodules()
ConnectionMode = connection_info.ConnectionMode
ConnectionInfo = connection_info.ConnectionInfo


class FakeClient:
	"""Stand-in for RemoteClient - RemoteMenu only calls bound methods on it,
	never inspects state, so a set of no-op callables is enough."""
	def doConnect(self, *a, **kw): pass
	def showControlAnotherComputer(self, *a, **kw): pass
	def onShowAdmin(self, *a, **kw): pass
	def onDisconnectFromTargetItem(self, *a, **kw): pass
	def toggleMute(self, *a, **kw): pass
	def pushClipboard(self, *a, **kw): pass
	def copyLink(self, *a, **kw): pass
	def sendSAS(self, *a, **kw): pass


class MenuStateMachineTests(unittest.TestCase):
	"""Regression coverage for the advisor-flagged double-connect race: without
	handleConnecting(MASTER) disabling controlAnotherComputerItem, a second
	'Control another computer' click during an in-flight master connect could
	overwrite masterTransport and orphan the first connection."""

	def setUp(self):
		self.remoteMenu = menu.RemoteMenu(FakeClient())

	def test_initial_state_only_connect_enabled(self):
		m = self.remoteMenu
		self.assertFalse(m.disconnectItem.enabled)
		self.assertFalse(m.controlAnotherComputerItem.enabled)
		self.assertFalse(m.disconnectFromTargetItem.enabled)
		self.assertFalse(m.sendCtrlAltDelItem.enabled)

	def test_connecting_slave_disables_connect_item(self):
		m = self.remoteMenu
		m.handleConnecting(ConnectionMode.SLAVE)
		self.assertFalse(m.connectItem.enabled)
		self.assertTrue(m.disconnectItem.enabled)

	def test_connected_slave_enables_control_another_computer(self):
		m = self.remoteMenu
		m.handleConnecting(ConnectionMode.SLAVE)
		m.handleConnected(ConnectionMode.SLAVE, True)
		self.assertFalse(m.connectItem.enabled)  # already connected as slave
		self.assertTrue(m.controlAnotherComputerItem.enabled)
		self.assertFalse(m.disconnectFromTargetItem.enabled)

	def test_master_connect_in_flight_disables_control_another_computer(self):
		"""The exact race the advisor caught: without this, the item stayed
		clickable for the whole in-flight connect window."""
		m = self.remoteMenu
		m.handleConnecting(ConnectionMode.SLAVE)
		m.handleConnected(ConnectionMode.SLAVE, True)
		self.assertTrue(m.controlAnotherComputerItem.enabled)
		m.handleConnecting(ConnectionMode.MASTER)
		self.assertFalse(
			m.controlAnotherComputerItem.enabled,
			"controlAnotherComputerItem must be disabled while a master "
			"connect is in flight, or a second click can orphan the first "
			"transport (advisor finding, round 6).",
		)

	def test_master_connect_success_updates_items_and_stays_disabled(self):
		m = self.remoteMenu
		m.handleConnecting(ConnectionMode.SLAVE)
		m.handleConnected(ConnectionMode.SLAVE, True)
		m.handleConnecting(ConnectionMode.MASTER)
		m.handleConnected(ConnectionMode.MASTER, True)
		self.assertFalse(m.controlAnotherComputerItem.enabled)  # already controlling
		self.assertTrue(m.disconnectFromTargetItem.enabled)
		self.assertTrue(m.sendCtrlAltDelItem.enabled)

	def test_master_connect_failure_recovers_the_menu_item(self):
		"""Covers the advisor's second concern about my own fix: *given*
		handleConnected(MASTER, False) is called, the item must re-enable, not
		stay stuck disabled with no way to retry. This only tests the menu's
		reaction, not the wiring that guarantees the call happens on every
		terminal failure path (client.py's close()->transportClosing->
		onDisconnectingAsMaster chain) - that part is code-read only, see the
		client CLAUDE.md, not something this file can reach."""
		m = self.remoteMenu
		m.handleConnecting(ConnectionMode.SLAVE)
		m.handleConnected(ConnectionMode.SLAVE, True)
		m.handleConnecting(ConnectionMode.MASTER)
		self.assertFalse(m.controlAnotherComputerItem.enabled)
		m.handleConnected(ConnectionMode.MASTER, False)  # failure/close path
		self.assertTrue(m.controlAnotherComputerItem.enabled)
		self.assertFalse(m.disconnectFromTargetItem.enabled)

	def test_disconnect_from_target_reverts_to_slave_only(self):
		m = self.remoteMenu
		m.handleConnecting(ConnectionMode.SLAVE)
		m.handleConnected(ConnectionMode.SLAVE, True)
		m.handleConnecting(ConnectionMode.MASTER)
		m.handleConnected(ConnectionMode.MASTER, True)
		m.handleConnected(ConnectionMode.MASTER, False)
		self.assertTrue(m.controlAnotherComputerItem.enabled)
		self.assertFalse(m.disconnectFromTargetItem.enabled)
		self.assertFalse(m.sendCtrlAltDelItem.enabled)
		self.assertTrue(m.disconnectItem.enabled)  # still connected as slave

	def test_full_disconnect_restores_connect_item(self):
		m = self.remoteMenu
		m.handleConnecting(ConnectionMode.SLAVE)
		m.handleConnected(ConnectionMode.SLAVE, True)
		m.handleConnected(ConnectionMode.SLAVE, False)
		self.assertTrue(m.connectItem.enabled)
		self.assertFalse(m.controlAnotherComputerItem.enabled)
		self.assertFalse(m.disconnectItem.enabled)

	def test_terminate_does_not_raise(self):
		"""terminate() touches eleven item references (including the two new
		ones and the pre-existing adminItem/cleanupItem cleanup) and runs on
		every NVDA shutdown and addon reload - the one method here otherwise
		with no coverage at all."""
		m = self.remoteMenu
		m.terminate()


class ControlAnotherComputerDialogTests(unittest.TestCase):
	def test_describe_marks_controlled_sessions(self):
		dlg = dialogs.ControlAnotherComputerDialog(
			None,
			[
				{"key": "PC-FREE", "client_count": 1, "has_controller": False},
				{"key": "PC-BUSY", "client_count": 2, "has_controller": True},
			],
		)
		self.assertIn("free", dlg._describe(dlg.sessions[0]))
		self.assertIn("already being controlled", dlg._describe(dlg.sessions[1]))

	def test_get_selected_key_returns_chosen_session(self):
		dlg = dialogs.ControlAnotherComputerDialog(
			None,
			[
				{"key": "PC-A", "client_count": 1, "has_controller": False},
				{"key": "PC-B", "client_count": 1, "has_controller": False},
			],
		)
		dlg.list.SetSelection(1)
		self.assertEqual(dlg.getSelectedKey(), "PC-B")

	def test_get_selected_key_none_when_list_empty(self):
		dlg = dialogs.ControlAnotherComputerDialog(None, [])
		self.assertIsNone(dlg.getSelectedKey())

	def test_get_selected_key_none_when_nothing_selected(self):
		dlg = dialogs.ControlAnotherComputerDialog(
			None, [{"key": "PC-A", "client_count": 1, "has_controller": False}]
		)
		dlg.list.SetSelection(-1)
		self.assertIsNone(dlg.getSelectedKey())


class DirectConnectDialogTests(unittest.TestCase):
	def setUp(self):
		configuration.get_config()["controlserver"]["key"] = "my-hostname"

	def test_connection_info_is_always_slave_with_configured_key(self):
		dlg = dialogs.DirectConnectDialog(None, title="Connect")
		dlg.host.SetValue("example.org:6837")
		info = dlg.getConnectionInfo()
		self.assertEqual(info.mode, ConnectionMode.SLAVE)
		self.assertEqual(info.key, "my-hostname")
		self.assertEqual(info.hostname, "example.org")
		self.assertEqual(info.port, 6837)


class ConnectionInfoAddressTests(unittest.TestCase):
	"""Guards the specific claim the advisor checked by code-reading: that
	write_connection_to_config's address key is host:port, never the session
	key - i.e. controlling another machine can never pollute the connect
	dialog's server dropdown with that machine's hostname-as-key."""

	def test_get_address_is_host_port_not_key(self):
		info = ConnectionInfo(
			hostname="relay.example.org", port=6837, key="SOME-TARGET-HOSTNAME",
			mode=ConnectionMode.MASTER,
		)
		self.assertEqual(info.getAddress(), "relay.example.org:6837")
		self.assertNotIn("SOME-TARGET-HOSTNAME", info.getAddress())


if __name__ == "__main__":
	unittest.main()
