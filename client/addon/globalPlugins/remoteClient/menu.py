from typing import TYPE_CHECKING

import wx

if TYPE_CHECKING:
	from .client import RemoteClient


import gui
import ui

from .connection_info import ConnectionMode


class RemoteMenu(wx.Menu):
	"""Menu for the NVDA Remote addon that appears in the NVDA Tools menu"""

	connectItem: wx.MenuItem
	disconnectItem: wx.MenuItem
	muteItem: wx.MenuItem
	pushClipboardItem: wx.MenuItem
	copyLinkItem: wx.MenuItem
	sendCtrlAltDelItem: wx.MenuItem
	controlAnotherComputerItem: wx.MenuItem
	disconnectFromTargetItem: wx.MenuItem
	serverInfoItem: wx.MenuItem
	remoteItem: wx.MenuItem

	def __init__(self, client: "RemoteClient") -> None:
		super().__init__()
		self.client = client
		# Master and slave are independent connections that can coexist (control-
		# server baseline + optionally controlling another machine) - tracked
		# separately so item enablement can reflect the right one instead of a
		# single "connected" flag that conflates the two roles.
		self._masterConnected = False
		self._slaveConnected = False
		toolsMenu = gui.mainFrame.sysTrayIcon.toolsMenu
		# Translators: Item in NVDA Remote submenu to connect to a remote computer.
		self.connectItem: wx.MenuItem = self.Append(
			wx.ID_ANY,
			_("Connect..."),
			_("Remotely connect to another computer running NVDA Remote Access"),
		)
		gui.mainFrame.sysTrayIcon.Bind(
			wx.EVT_MENU, self.client.doConnect, self.connectItem
		)
		# Translators: Item in NVDA Remote submenu to disconnect from a remote computer.
		self.disconnectItem: wx.MenuItem = self.Append(
			wx.ID_ANY,
			_("Disconnect"),
			_("Disconnect from the control server (and from any computer being controlled)"),
		)
		self.disconnectItem.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(
			wx.EVT_MENU, self.onDisconnectItem, self.disconnectItem
		)
		# Translators: Menu item in NVDA Remote submenu to choose another online computer to control.
		self.controlAnotherComputerItem: wx.MenuItem = self.Append(
			wx.ID_ANY,
			_("Control another computer..."),
			_("Choose another online computer to control"),
		)
		self.controlAnotherComputerItem.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(
			wx.EVT_MENU, self.client.showControlAnotherComputer, self.controlAnotherComputerItem
		)
		# Translators: Menu item in NVDA Remote submenu to stop controlling another computer.
		self.disconnectFromTargetItem: wx.MenuItem = self.Append(
			wx.ID_ANY,
			_("Disconnect from controlled computer"),
			_("Stop controlling the other computer, keeping the control-server connection"),
		)
		self.disconnectFromTargetItem.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(
			wx.EVT_MENU, self.onDisconnectFromTargetItem, self.disconnectFromTargetItem
		)
		# Translators: Menu item in NvDA Remote submenu to mute speech and sounds from the remote computer.
		self.muteItem: wx.MenuItem = self.Append(
			wx.ID_ANY,
			_("Mute remote"),
			_("Mute speech and sounds from the remote computer"),
			kind=wx.ITEM_CHECK,
		)
		self.muteItem.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.onMuteItem, self.muteItem)
		# Translators: Menu item in NVDA Remote submenu to push clipboard content to the remote computer.
		self.pushClipboardItem: wx.MenuItem = self.Append(
			wx.ID_ANY,
			_("&Push clipboard"),
			_("Push the clipboard to the other machine"),
		)
		self.pushClipboardItem.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(
			wx.EVT_MENU, self.onPushClipboardItem, self.pushClipboardItem
		)
		# Translators: Menu item in NVDA Remote submenu to copy a link to the current session.
		self.copyLinkItem: wx.MenuItem = self.Append(
			wx.ID_ANY, _("Copy &link"), _("Copy a link to the remote session")
		)
		self.copyLinkItem.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(
			wx.EVT_MENU, self.onCopyLinkItem, self.copyLinkItem
		)
		# Translators: Menu item in NVDA Remote submenu to send Control+Alt+Delete to the remote computer.
		self.sendCtrlAltDelItem: wx.MenuItem = self.Append(
			wx.ID_ANY, _("Send Ctrl+Alt+Del"), _("Send Ctrl+Alt+Del")
		)
		gui.mainFrame.sysTrayIcon.Bind(
			wx.EVT_MENU, self.onSendCtrlAltDel, self.sendCtrlAltDelItem
		)
		self.sendCtrlAltDelItem.Enable(False)

		# Translators: Menu item in NVDA Remote submenu to administer the server.
		self.adminItem: wx.MenuItem = self.Append(
			wx.ID_ANY, _("Server Administration..."), _("Administer the Remote Server")
		)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.client.onShowAdmin, self.adminItem)
		self.adminItem.Enable(False)

		# Translators: Menu item in NVDA Remote submenu to clean up unused configuration.
		self.cleanupItem: wx.MenuItem = self.Append(
			wx.ID_ANY, _("Clean up configuration..."), _("Remove unused or old configuration entries")
		)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.onCleanupItem, self.cleanupItem)

		# Translators: Menu item in NVDA Remote submenu to announce the connected relay server's version.
		self.serverInfoItem: wx.MenuItem = self.Append(
			wx.ID_ANY, _("Relay server information"), _("Announce the version of the connected relay server")
		)
		self.serverInfoItem.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.onServerInfoItem, self.serverInfoItem)

		# Translators: Label of menu in NVDA tools menu.
		self.remoteItem = toolsMenu.AppendSubMenu(
			self, _("R&emote"), _("NVDA Remote Access")
		)

	def terminate(self) -> None:
		self.Remove(self.connectItem.Id)
		self.connectItem.Destroy()
		self.connectItem = None
		self.Remove(self.disconnectItem.Id)
		self.disconnectItem.Destroy()
		self.disconnectItem = None
		self.Remove(self.controlAnotherComputerItem.Id)
		self.controlAnotherComputerItem.Destroy()
		self.controlAnotherComputerItem = None
		self.Remove(self.disconnectFromTargetItem.Id)
		self.disconnectFromTargetItem.Destroy()
		self.disconnectFromTargetItem = None
		self.Remove(self.muteItem.Id)
		self.muteItem.Destroy()
		self.muteItem = None
		self.Remove(self.pushClipboardItem.Id)
		self.pushClipboardItem.Destroy()
		self.pushClipboardItem = None
		self.Remove(self.copyLinkItem.Id)
		self.copyLinkItem.Destroy()
		self.copyLinkItem = None
		self.Remove(self.sendCtrlAltDelItem.Id)
		self.sendCtrlAltDelItem.Destroy()
		self.sendCtrlAltDelItem = None
		self.Remove(self.adminItem.Id)
		self.adminItem.Destroy()
		self.adminItem = None
		self.Remove(self.cleanupItem.Id)
		self.cleanupItem.Destroy()
		self.cleanupItem = None
		self.Remove(self.serverInfoItem.Id)
		self.serverInfoItem.Destroy()
		self.serverInfoItem = None
		tools_menu = gui.mainFrame.sysTrayIcon.toolsMenu
		tools_menu.Remove(self.remoteItem.Id)
		self.remoteItem.Destroy()
		self.remoteItem = None
		try:
			self.Destroy()
		except (RuntimeError, AttributeError):
			pass

	def onDisconnectItem(self, evt: wx.CommandEvent) -> None:
		evt.Skip()
		self.client.disconnect()

	def onDisconnectFromTargetItem(self, evt: wx.CommandEvent) -> None:
		evt.Skip()
		self.client.disconnectAsMaster()

	def onMuteItem(self, evt: wx.CommandEvent) -> None:
		evt.Skip()
		self.client.toggleMute()

	def onPushClipboardItem(self, evt: wx.CommandEvent) -> None:
		evt.Skip()
		self.client.pushClipboard()

	def onCopyLinkItem(self, evt: wx.CommandEvent) -> None:
		evt.Skip()
		self.client.copyLink()

	def onSendCtrlAltDel(self, evt: wx.CommandEvent) -> None:
		evt.Skip()
		self.client.sendSAS()

	def onCleanupItem(self, evt: wx.CommandEvent) -> None:
		from . import configuration
		configuration.minify_config(gui.mainFrame)

	def onServerInfoItem(self, evt: wx.CommandEvent) -> None:
		evt.Skip()
		# Prefer the slave (control-server) session - it's the always-on
		# baseline connection; a master session (controlling another
		# machine) talks to the same server in practice (session discovery
		# only lists other sessions on the same server), but slave is the
		# more likely one to actually be present.
		session = self.client.slaveSession or self.client.masterSession
		if session is None or session.serverVersion is None:
			# Translators: Reported when the relay server's version isn't known yet (e.g. not connected).
			ui.message(_("Relay server version not available"))
			return
		# Translators: Reports the connected relay server's version, %s is replaced with the version number.
		message = _("Relay server version %s") % session.serverVersion
		updateCheck = session.serverUpdateCheck
		if updateCheck and updateCheck.get('update_available') and updateCheck.get('latest_version'):
			# Translators: Appended when a newer relay server version is known to be available, %s is the version number.
			message += " " + _("A newer server version %s is available") % updateCheck['latest_version']
		ui.message(message)

	def handleConnected(self, mode: ConnectionMode, connected: bool) -> None:
		if mode == ConnectionMode.MASTER:
			self._masterConnected = connected
		else:
			self._slaveConnected = connected
		anyConnected = self._masterConnected or self._slaveConnected
		# Connecting always means "to the control server" (slave) now - it's
		# not blocked by a master connection to some other machine.
		self.connectItem.Enable(not self._slaveConnected)
		self.disconnectItem.Enable(anyConnected)
		self.muteItem.Enable(anyConnected)
		if not anyConnected:
			self.muteItem.Check(False)
		self.pushClipboardItem.Enable(anyConnected)
		self.copyLinkItem.Enable(anyConnected)
		# Send Ctrl+Alt+Del and "disconnect from controlled computer" only make
		# sense while controlling another machine (master); being slave-only
		# is the normal state, and sendSAS requires masterTransport to exist.
		self.sendCtrlAltDelItem.Enable(self._masterConnected)
		self.disconnectFromTargetItem.Enable(self._masterConnected)
		# Picking a new target requires a control-server connection to ask
		# through, and (for now - switching targets isn't supported yet)
		# requires not already controlling one.
		self.controlAnotherComputerItem.Enable(self._slaveConnected and not self._masterConnected)
		self.adminItem.Enable(anyConnected)
		self.serverInfoItem.Enable(anyConnected)

	def handleConnecting(self, mode: ConnectionMode) -> None:
		self.disconnectItem.Enable(True)
		if mode == ConnectionMode.SLAVE:
			self.connectItem.Enable(False)
		else:
			# A master connect is in flight: isConnectedAsMaster() still reads
			# False (masterTransport.connected isn't true yet), so without this
			# the item would stay clickable and a second pick during the same
			# in-flight connect would overwrite self.masterTransport, orphaning
			# the first transport (and its still-running reconnectorThread).
			# handleConnected() re-enables/disables this correctly on both the
			# success and failure paths that follow.
			self.controlAnotherComputerItem.Enable(False)
