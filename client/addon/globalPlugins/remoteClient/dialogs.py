from typing import List, Optional

import addonHandler
import gui
import wx
from logHandler import log

from . import configuration, socket_utils
from .connection_info import ConnectionInfo, ConnectionMode

try:
	addonHandler.initTranslation()
except addonHandler.AddonError:
	log.warning(
		"Unable to initialise translations. This may be because the addon is running from NVDA scratchpad."
	)

WX_VERSION = int(wx.version()[0])
WX_CENTER = wx.Center if WX_VERSION>=4 else wx.CENTER_ON_SCREEN

DEFAULT_HOST = "danijel0.danijels-computer.de"

class DirectConnectDialog(wx.Dialog):
	"""Connects to a control server as this machine's session (always as a
	controllable "slave" - the session name/key is fixed to this machine's
	hostname, enforced in configuration.py, so it's not shown here either).
	Controlling *another* machine is a separate, later step (Remote menu ->
	Control another computer), not something chosen at connect time anymore.
	"""

	host: wx.ComboBox

	def __init__(self, parent: Optional[wx.Window] = None, id: int = wx.ID_ANY, title: str = "", hostnames: Optional[List[str]] = None):
		super().__init__(parent, id, title=title)
		main_sizer = wx.BoxSizer(wx.VERTICAL)

		row = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: The label of an edit field in the connect dialog to enter the control server's name or address.
		row.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Server:")))
		self.host = wx.ComboBox(self, wx.ID_ANY, value=DEFAULT_HOST)
		row.Add(self.host)
		main_sizer.Add(row, flag=wx.ALL, border=10)

		buttons = self.CreateButtonSizer(wx.OK | wx.CANCEL)
		main_sizer.Add(buttons, flag=wx.BOTTOM | wx.ALIGN_RIGHT, border=10)
		main_sizer.Fit(self)
		self.SetSizer(main_sizer)
		self.Center(wx.BOTH | WX_CENTER)
		ok = wx.FindWindowById(wx.ID_OK, self)
		ok.Bind(wx.EVT_BUTTON, self.onOk)

		self.host.SetFocus()

		if hostnames:
			existing = self.host.GetValue()
			for h in hostnames:
				if h != existing:
					self.host.Append(h)

	def onOk(self, evt: wx.CommandEvent) -> None:
		if not self.host.GetValue():
			gui.messageBox(_("Server must be set."), _("Error"), wx.OK | wx.ICON_ERROR)
			self.host.SetFocus()
		else:
			evt.Skip()

	def getConnectionInfo(self) -> ConnectionInfo:
		host = self.host.GetValue()
		serverAddr, port = socket_utils.addressToHostPort(host)
		return ConnectionInfo(
			hostname=serverAddr,
			mode=ConnectionMode.SLAVE,
			key=configuration.get_config()['controlserver']['key'],
			port=port,
			insecure=False,
		)

class ControlAnotherComputerDialog(wx.Dialog):
	"""Lets the user pick which other online, controllable session to connect
	to as master. A plain list (not a fancy picker) so Enter on the selected
	entry - the dialog's default OK button - connects to it, and Escape
	cancels: standard, predictable dialog behavior rather than a custom
	widget, since not every custom control reads well with a screen reader."""

	list: wx.ListBox

	def __init__(self, parent: wx.Window, sessions: List[dict]):
		# Translators: Title of the dialog listing other computers available to control.
		super().__init__(parent, title=_("Control another computer"))
		self.sessions = sessions
		main_sizer = wx.BoxSizer(wx.VERTICAL)

		if sessions:
			choices = [self._describe(s) for s in sessions]
		else:
			# Translators: Shown in the "control another computer" list when nobody else is online.
			choices = [_("No other computers are currently online.")]
		self.list = wx.ListBox(self, wx.ID_ANY, choices=choices)
		self.list.SetSelection(0)
		self.list.Enable(bool(sessions))
		main_sizer.Add(self.list, proportion=1, flag=wx.EXPAND | wx.ALL, border=10)

		buttons = self.CreateButtonSizer(wx.OK | wx.CANCEL)
		main_sizer.Add(buttons, flag=wx.BOTTOM | wx.ALIGN_RIGHT, border=10)
		self.SetSizerAndFit(main_sizer)
		self.Center(wx.BOTH | WX_CENTER)

		ok = wx.FindWindowById(wx.ID_OK, self)
		ok.Enable(bool(sessions))
		ok.Bind(wx.EVT_BUTTON, self.onOk)

		self.list.SetFocus()

	def _describe(self, session: dict) -> str:
		status = (
			# Translators: Status shown for a session that's already being controlled by someone else.
			_("already being controlled") if session.get('has_controller')
			# Translators: Status shown for a session nobody is currently controlling.
			else _("free")
		)
		return "{key} ({status})".format(key=session['key'], status=status)

	def onOk(self, evt: wx.CommandEvent) -> None:
		if not self.sessions:
			return
		evt.Skip()

	def getSelectedKey(self) -> Optional[str]:
		idx = self.list.GetSelection()
		if not self.sessions or idx == wx.NOT_FOUND:
			return None
		return self.sessions[idx]['key']

class CertificateUnauthorizedDialog(wx.MessageDialog):
	def __init__(self, parent: Optional[wx.Window], fingerprint: Optional[str] = None):
		title=_("NVDA Remote Connection Security Warning")
		message = _("Warning! The certificate of this server could not be verified.\nThis connection may not be secure. It is possible that someone is trying to overhear your communication.\nBefore continuing please make sure that the following server certificate fingerprint is a proper one.\nIf you have any questions, please contact the server administrator.\n\nServer SHA256 fingerprint: {fingerprint}\n\nDo you want to continue connecting?").format(fingerprint=fingerprint)
		super().__init__(parent, caption=title, message=message, style=wx.YES_NO|wx.CANCEL|wx.CANCEL_DEFAULT|wx.CENTRE)
		self.SetYesNoLabels(_("Connect and do not ask again for this server"), _("Connect"))
