import wx
import gui
from gui.settingsDialogs import SettingsPanel
from . import configuration

class RemoteSettingsPanel(SettingsPanel):
	# Translators: This is the label for the remote settings category in NVDA Settings screen.
	title = _("Remote")
	autoconnect: wx.CheckBox
	host: wx.TextCtrl
	key: wx.TextCtrl
	play_sounds: wx.CheckBox
	allow_beta_updates: wx.CheckBox
	delete_fingerprints: wx.Button

	def makeSettings(self, settingsSizer):
		self.config = configuration.get_config()
		sHelper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.autoconnect = wx.CheckBox(self, wx.ID_ANY, label=_("Auto-connect to control server on startup"))
		self.autoconnect.Bind(wx.EVT_CHECKBOX, self.on_autoconnect)
		sHelper.addItem(self.autoconnect)

		# No connection-type choice here anymore: auto-connect is always as a
		# controllable machine (slave). Controlling another machine is a
		# separate, later action (Remote menu -> Control another computer).
		sHelper.addItem(wx.StaticText(self, wx.ID_ANY, label=_("&Host:")))
		self.host = wx.TextCtrl(self, wx.ID_ANY)
		self.host.Enable(False)
		sHelper.addItem(self.host)
		
		sHelper.addItem(wx.StaticText(self, wx.ID_ANY, label=_("&Session name:")))
		self.key = wx.TextCtrl(self, wx.ID_ANY)
		self.key.Enable(False)
		sHelper.addItem(self.key)
		
		# Translators: A checkbox in add-on options dialog to set whether sounds play instead of beeps.
		self.play_sounds = wx.CheckBox(self, wx.ID_ANY, label=_("Play sounds instead of beeps"))
		sHelper.addItem(self.play_sounds)

		# Translators: A checkbox in add-on options dialog to opt into unstable nightly builds pushed by the server.
		self.allow_beta_updates = wx.CheckBox(self, wx.ID_ANY, label=_("Allow beta (nightly, untested) updates"))
		sHelper.addItem(self.allow_beta_updates)

		# Translators: A button in add-on options dialog to delete all fingerprints of unauthorized certificates.
		self.delete_fingerprints = wx.Button(self, wx.ID_ANY, label=_("Delete all trusted fingerprints"))
		self.delete_fingerprints.Bind(wx.EVT_BUTTON, self.on_delete_fingerprints)
		sHelper.addItem(self.delete_fingerprints)
		
		self.set_from_config()

	def on_autoconnect(self, evt: wx.CommandEvent) -> None:
		self.set_controls()

	def set_controls(self) -> None:
		state = bool(self.autoconnect.GetValue())
		# The session name (key) is now always the hostname and should not be editable
		self.key.Enable(False)
		self.host.Enable(state)

	def set_from_config(self) -> None:
		cs = self.config['controlserver']
		self.autoconnect.SetValue(cs['autoconnect'])
		self.host.SetValue(cs['host'])
		self.key.SetValue(cs['key'])
		self.set_controls()
		self.play_sounds.SetValue(self.config['ui']['play_sounds'])
		self.allow_beta_updates.SetValue(self.config['addon_update']['allow_beta_updates'])

	def on_delete_fingerprints(self, evt: wx.CommandEvent) -> None:
		if gui.messageBox(_("When connecting to an unauthorized server, you will again be prompted to accepts its certificate."), _("Are you sure you want to delete all stored trusted fingerprints?"), wx.YES|wx.NO|wx.NO_DEFAULT|wx.ICON_WARNING) == wx.YES:
			self.config['trusted_certs'].clear()
			self.config.write()
		evt.Skip()

	def isValid(self) -> bool:
		if self.autoconnect.GetValue():
			if not self.host.GetValue() or not self.key.GetValue():
				gui.messageBox(_("Both host and session name must be set in the Remote section."), _("Remote Error"), wx.OK | wx.ICON_ERROR)
				return False
		return True

	def write_to_config(self) -> None:
		cs = self.config['controlserver']
		cs['autoconnect'] = self.autoconnect.GetValue()
		cs['self_hosted'] = False
		# connection_type is deliberately left alone: it's no longer read by
		# performAutoconnect (always slave now) or set by this panel.
		cs['host'] = self.host.GetValue()
		cs['key'] = self.key.GetValue()
		self.config['ui']['play_sounds'] = self.play_sounds.GetValue()
		self.config['addon_update']['allow_beta_updates'] = self.allow_beta_updates.GetValue()
		self.config.write()

	def onSave(self):
		self.write_to_config()
