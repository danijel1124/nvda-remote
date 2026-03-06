import json
import random
import threading
from typing import Any, Dict, List, Optional, Union
from urllib import request

import addonHandler
import gui
import wx
from logHandler import log

from . import configuration, serializer, socket_utils, transport
from .alwaysCallAfter import alwaysCallAfter
from .connection_info import ConnectionInfo, ConnectionMode
from .protocol import SERVER_PORT, RemoteMessageType

try:
	addonHandler.initTranslation()
except addonHandler.AddonError:
	log.warning(
		"Unable to initialise translations. This may be because the addon is running from NVDA scratchpad."
	)

WX_VERSION = int(wx.version()[0])
WX_CENTER = wx.Center if WX_VERSION>=4 else wx.CENTER_ON_SCREEN

DEFAULT_HOST = "danijel0.danijels-computer.de"

class ClientPanel(wx.Panel):
	host: wx.ComboBox
	key: wx.TextCtrl
	generate_key: wx.Button
	keyConnector: Optional['transport.RelayTransport']

	def __init__(self, parent: Optional[wx.Window] = None, id: int = wx.ID_ANY):
		super().__init__(parent, id)
		sizer = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: The label of an edit field in connect dialog to enter name or address of the remote computer.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Server:")))
		self.host = wx.ComboBox(self, wx.ID_ANY, value=DEFAULT_HOST)
		sizer.Add(self.host)
		# Translators: Label of the edit field to enter session name (password) to secure the remote connection.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Session name:")))
		self.key = wx.TextCtrl(self, wx.ID_ANY, value=configuration.get_config()['controlserver']['key'])
		self.key.Enable(False)
		sizer.Add(self.key)
		self.SetSizerAndFit(sizer)

	def on_generate_key(self, evt: wx.CommandEvent) -> None:
		if not self.host.GetValue():
			gui.messageBox(_("Server must be set."), _("Error"), wx.OK | wx.ICON_ERROR)
			self.host.SetFocus()
		else:
			evt.Skip()
			self.generate_key_command()

	def generate_key_command(self, insecure: bool = False) -> None:
			address = socket_utils.addressToHostPort(self.host.GetValue())
			self.keyConnector = transport.RelayTransport(address=address, serializer=serializer.JSONSerializer(), insecure=insecure)
			self.keyConnector.registerInbound(RemoteMessageType.generate_key, self.handle_key_generated)
			self.keyConnector.transportCertificateAuthenticationFailed.register(self.handle_certificate_failed)
			t = threading.Thread(target=self.keyConnector.run)
			t.start()

	@alwaysCallAfter
	def handle_key_generated(self, key: Optional[str] = None) -> None:
		self.key.SetValue(key)
		self.key.SetFocus()
		self.keyConnector.close()
		self.keyConnector = None

	@alwaysCallAfter
	def handle_certificate_failed(self) -> None:
		try:
			cert_hash = self.keyConnector.lastFailFingerprint
				
			wnd = CertificateUnauthorizedDialog(None, fingerprint=cert_hash)
			a = wnd.ShowModal()
			if a == wx.ID_YES:
				config = configuration.get_config()
				config['trusted_certs'][self.host.GetValue()]=cert_hash
				config.write()
			if a != wx.ID_YES and a != wx.ID_NO: return
		except Exception as ex:
			log.error(ex)
			return
		self.keyConnector.close()
		self.keyConnector = None
		self.generate_key_command(True)

class DirectConnectDialog(wx.Dialog):
	connection_type: wx.RadioBox
	container: wx.Panel
	panel: ClientPanel
	main_sizer: wx.BoxSizer

	def __init__(self, parent: wx.Window, id: int, title: str, hostnames: Optional[List[str]] = None):
		super().__init__(parent, id, title=title)
		main_sizer = self.main_sizer = wx.BoxSizer(wx.VERTICAL)
		
		choices = [_("Control another machine"), _("Allow this machine to be controlled")]
		self.connection_type = wx.RadioBox(self, wx.ID_ANY, choices=choices, style=wx.RA_VERTICAL)
		self.connection_type.SetSelection(0)
		main_sizer.Add(self.connection_type)
		self.container = wx.Panel(parent=self)
		self.panel = ClientPanel(parent=self.container)
		main_sizer.Add(self.container)
		buttons = self.CreateButtonSizer(wx.OK | wx.CANCEL)
		main_sizer.Add(buttons, flag=wx.BOTTOM)
		main_sizer.Fit(self)
		self.SetSizer(main_sizer)
		self.Center(wx.BOTH | WX_CENTER)
		ok = wx.FindWindowById(wx.ID_OK, self)
		ok.Bind(wx.EVT_BUTTON, self.onOk)
		
		self.connection_type.SetFocus()

		if hostnames:
			existing = self.panel.host.GetValue()
			for h in hostnames:
				if h != existing:
					self.panel.host.Append(h)

	def onOk(self, evt: wx.CommandEvent) -> None:
		if (not self.panel.host.GetValue() or not self.panel.key.GetValue()):
			gui.messageBox(_("Both server and session name must be set."), _("Error"), wx.OK | wx.ICON_ERROR)
			self.panel.host.SetFocus()
		else:
			evt.Skip()

	def getKey(self) -> str:
		return self.panel.key.GetValue()
	
	def getConnectionInfo(self) -> ConnectionInfo:
		host = self.panel.host.GetValue()
		serverAddr, port = socket_utils.addressToHostPort(host)
		mode = ConnectionMode.MASTER if self.connection_type.GetSelection() == 0 else ConnectionMode.SLAVE
		return ConnectionInfo(
			hostname=serverAddr, 
			mode=mode, 
			key=self.getKey(), 
			port=port,
			insecure=False
		)

class CertificateUnauthorizedDialog(wx.MessageDialog):
	def __init__(self, parent: Optional[wx.Window], fingerprint: Optional[str] = None):
		title=_("NVDA Remote Connection Security Warning")
		message = _("Warning! The certificate of this server could not be verified.\nThis connection may not be secure. It is possible that someone is trying to overhear your communication.\nBefore continuing please make sure that the following server certificate fingerprint is a proper one.\nIf you have any questions, please contact the server administrator.\n\nServer SHA256 fingerprint: {fingerprint}\n\nDo you want to continue connecting?").format(fingerprint=fingerprint)
		super().__init__(parent, caption=title, message=message, style=wx.YES_NO|wx.CANCEL|wx.CANCEL_DEFAULT|wx.CENTRE)
		self.SetYesNoLabels(_("Connect and do not ask again for this server"), _("Connect"))
