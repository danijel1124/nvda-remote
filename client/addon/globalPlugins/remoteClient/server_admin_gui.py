import wx
import gui
import addonHandler
from .protocol import RemoteMessageType
from . import configuration

addonHandler.initTranslation()

class ServerAdminDialog(wx.Dialog):
	def __init__(self, client):
		super().__init__(gui.mainFrame, title=_("Server Administration"), size=(750, 550))
		self.client = client
		self.config = configuration.get_config()
		self.client.set_admin_ui(self)
		self._init_ui()
		
		saved_token = self.config['controlserver'].get('admin_token', '')
		if saved_token:
			self.token_input.SetValue(saved_token)
		
		self.Center()
		
	def _init_ui(self):
		panel = wx.Panel(self)
		vbox = wx.BoxSizer(wx.VERTICAL)
		
		# Token Input Area
		hbox1 = wx.BoxSizer(wx.HORIZONTAL)
		hbox1.Add(wx.StaticText(panel, label=_("Admin Token:")), flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=8)
		self.token_input = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
		hbox1.Add(self.token_input, proportion=1, flag=wx.EXPAND)
		
		self.login_btn = wx.Button(panel, label=_("Login"))
		self.login_btn.Bind(wx.EVT_BUTTON, self.on_login)
		hbox1.Add(self.login_btn, flag=wx.LEFT, border=8)
		
		self.reset_token_btn = wx.Button(panel, label=_("Reset Saved Token"))
		self.reset_token_btn.Bind(wx.EVT_BUTTON, self.on_reset_token)
		hbox1.Add(self.reset_token_btn, flag=wx.LEFT, border=8)
		
		vbox.Add(hbox1, flag=wx.EXPAND|wx.ALL, border=10)
		
		# List
		self.list_ctrl = wx.ListCtrl(panel, style=wx.LC_REPORT|wx.LC_SINGLE_SEL)
		self.list_ctrl.InsertColumn(0, _("Session name"), width=200)
		self.list_ctrl.InsertColumn(1, _("Status"), width=100)
		self.list_ctrl.InsertColumn(2, _("Authorization"), width=150)
		self.list_ctrl.InsertColumn(3, _("Clients"), width=80)
		
		vbox.Add(self.list_ctrl, proportion=1, flag=wx.EXPAND|wx.ALL, border=10)

		# Buttons Area
		hbox2 = wx.BoxSizer(wx.HORIZONTAL)
		self.refresh_btn = wx.Button(panel, label=_("Refresh List"))
		self.refresh_btn.Bind(wx.EVT_BUTTON, self.on_refresh)
		hbox2.Add(self.refresh_btn)
		
		self.approve_btn = wx.Button(panel, label=_("Approve/Whitelist"))
		self.approve_btn.Bind(wx.EVT_BUTTON, self.on_approve)
		hbox2.Add(self.approve_btn, flag=wx.LEFT, border=10)
		
		self.remove_btn = wx.Button(panel, label=_("Block/Remove"))
		self.remove_btn.Bind(wx.EVT_BUTTON, self.on_remove)
		hbox2.Add(self.remove_btn, flag=wx.LEFT, border=10)
		
		self.close_btn = wx.Button(panel, label=_("Close"))
		self.close_btn.Bind(wx.EVT_BUTTON, self.on_close)
		hbox2.Add(self.close_btn, flag=wx.LEFT, border=10)

		vbox.Add(hbox2, flag=wx.ALIGN_CENTER|wx.ALL, border=10)
		panel.SetSizer(vbox)

	def on_close(self, event):
		self.client.set_admin_ui(None)
		self.Destroy()

	def on_login(self, event):
		token = self.token_input.GetValue().strip()
		if token:
			# Save token to config
			self.config['controlserver']['admin_token'] = token
			self.config.write()
			self.client.send_admin_auth(token)

	def on_reset_token(self, event):
		if gui.messageBox(_("Do you really want to delete the saved admin token from the configuration?"), _("Reset Token"), wx.YES|wx.NO|wx.ICON_QUESTION) == wx.YES:
			self.config['controlserver']['admin_token'] = ""
			self.config.write()
			self.token_input.SetValue("")
			gui.messageBox(_("Saved token has been deleted."), _("Reset Token"), wx.OK|wx.ICON_INFORMATION)

	def on_refresh(self, event):
		self.client.send_admin_list_req()

	def on_approve(self, event):
		key = self._get_selected_key()
		if key: self.client.send_admin_approve(key)

	def on_remove(self, event):
		key = self._get_selected_key()
		if key: self.client.send_admin_remove(key)
			
	def _get_selected_key(self):
		idx = self.list_ctrl.GetFirstSelected()
		return self.list_ctrl.GetItemText(idx, 0) if idx != -1 else None

	def update_list(self, channels):
		self.list_ctrl.DeleteAllItems()
		# Sort: Online first
		sorted_channels = sorted(channels, key=lambda x: (not x.get('online'), x['key']))
		
		for ch in sorted_channels:
			idx = self.list_ctrl.InsertItem(self.list_ctrl.GetItemCount(), ch['key'])
			
			is_online = bool(ch.get('online'))
			client_count = int(ch.get('client_count', 0))
			if client_count > 0: is_online = True
			
			status_text = _("ONLINE") if is_online else _("OFFLINE")
			self.list_ctrl.SetItem(idx, 1, status_text)
			
			auth_text = _("Authorized") if ch['authorized'] else _("QUARANTINE")
			self.list_ctrl.SetItem(idx, 2, auth_text)
			self.list_ctrl.SetItem(idx, 3, str(client_count))
			
			if not ch['authorized']:
				self.list_ctrl.SetItemTextColour(idx, wx.Colour(200, 0, 0))
			elif not is_online:
				self.list_ctrl.SetItemTextColour(idx, wx.Colour(120, 120, 120))
			else:
				self.list_ctrl.SetItemTextColour(idx, wx.Colour(0, 150, 0))

	def show_status(self, msg):
		wx.CallAfter(lambda: wx.MessageBox(msg, _("Server Status"), wx.OK, self))
