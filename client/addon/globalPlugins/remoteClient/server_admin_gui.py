import wx
import gui
import addonHandler
from .protocol import RemoteMessageType
from . import configuration

addonHandler.initTranslation()

class ServerAdminDialog(wx.Dialog):
	def __init__(self, client):
		super().__init__(gui.mainFrame, title=_("Server Administration"), size=(800, 600))
		self.client = client
		self.config = configuration.get_config()
		self.client.set_admin_ui(self)
		
		# Get current server address for token mapping
		transport = self.client._get_active_transport()
		self.current_server = f"{transport.address[0]}:{transport.address[1]}" if transport else ""
		
		self._init_ui()
		self._refresh_token_list()
		self.Center()
		
	def _init_ui(self):
		main_panel = wx.Panel(self)
		main_vbox = wx.BoxSizer(wx.VERTICAL)
		
		self.notebook = wx.Notebook(main_panel)
		
		# Tab 1: Token Management
		self.tokens_tab = wx.Panel(self.notebook)
		self._setup_tokens_tab()
		self.notebook.AddPage(self.tokens_tab, _("Token Management"))
		
		# Tab 2: Session Management (existing logic)
		self.sessions_tab = wx.Panel(self.notebook)
		self._setup_sessions_tab()
		self.notebook.AddPage(self.sessions_tab, _("Session Management"))
		
		main_vbox.Add(self.notebook, proportion=1, flag=wx.EXPAND | wx.ALL, border=5)
		
		# Bottom Buttons
		hbox = wx.BoxSizer(wx.HORIZONTAL)
		self.close_btn = wx.Button(main_panel, label=_("Close"))
		self.close_btn.Bind(wx.EVT_BUTTON, self.on_close)
		hbox.Add(self.close_btn, flag=wx.ALL, border=5)
		
		main_vbox.Add(hbox, flag=wx.ALIGN_RIGHT | wx.ALL, border=5)
		
		main_panel.SetSizer(main_vbox)

	def _setup_tokens_tab(self):
		vbox = wx.BoxSizer(wx.VERTICAL)
		
		# Current Server Info
		curr_box = wx.StaticBox(self.tokens_tab, label=_("Current Connection"))
		curr_sizer = wx.StaticBoxSizer(curr_box, wx.VERTICAL)
		
		hbox_curr = wx.BoxSizer(wx.HORIZONTAL)
		hbox_curr.Add(wx.StaticText(self.tokens_tab, label=_("Server: %s") % self.current_server), flag=wx.CENTER|wx.RIGHT, border=10)
		
		self.login_btn = wx.Button(self.tokens_tab, label=_("Login to Current Server"))
		self.login_btn.Bind(wx.EVT_BUTTON, self.on_login)
		hbox_curr.Add(self.login_btn)
		
		curr_sizer.Add(hbox_curr, flag=wx.ALL|wx.EXPAND, border=10)
		vbox.Add(curr_sizer, flag=wx.EXPAND|wx.ALL, border=10)
		
		# Token List Management
		vbox.Add(wx.StaticText(self.tokens_tab, label=_("Stored Admin Tokens:")), flag=wx.LEFT|wx.TOP, border=10)
		
		self.token_list = wx.ListCtrl(self.tokens_tab, style=wx.LC_REPORT|wx.LC_SINGLE_SEL)
		self.token_list.InsertColumn(0, _("Server Address"), width=250)
		self.token_list.InsertColumn(1, _("Token"), width=350)
		vbox.Add(self.token_list, proportion=1, flag=wx.EXPAND|wx.ALL, border=10)
		
		hbox_btns = wx.BoxSizer(wx.HORIZONTAL)
		
		self.add_token_btn = wx.Button(self.tokens_tab, label=_("Add/Edit Token"))
		self.add_token_btn.Bind(wx.EVT_BUTTON, self.on_add_token)
		hbox_btns.Add(self.add_token_btn, flag=wx.RIGHT, border=5)
		
		self.delete_token_btn = wx.Button(self.tokens_tab, label=_("Delete Selected"))
		self.delete_token_btn.Bind(wx.EVT_BUTTON, self.on_delete_token)
		hbox_btns.Add(self.delete_token_btn, flag=wx.RIGHT, border=5)
		
		vbox.Add(hbox_btns, flag=wx.LEFT|wx.BOTTOM, border=10)
		
		self.tokens_tab.SetSizer(vbox)

	def _setup_sessions_tab(self):
		vbox = wx.BoxSizer(wx.VERTICAL)
		
		# List (Existing)
		self.list_ctrl = wx.ListCtrl(self.sessions_tab, style=wx.LC_REPORT|wx.LC_SINGLE_SEL)
		self.list_ctrl.InsertColumn(0, _("Session name"), width=200)
		self.list_ctrl.InsertColumn(1, _("Status"), width=100)
		self.list_ctrl.InsertColumn(2, _("Authorization"), width=150)
		self.list_ctrl.InsertColumn(3, _("Clients"), width=80)
		
		vbox.Add(self.list_ctrl, proportion=1, flag=wx.EXPAND|wx.ALL, border=10)

		# Buttons Area (Existing)
		hbox2 = wx.BoxSizer(wx.HORIZONTAL)
		self.refresh_btn = wx.Button(self.sessions_tab, label=_("Refresh List"))
		self.refresh_btn.Bind(wx.EVT_BUTTON, self.on_refresh)
		hbox2.Add(self.refresh_btn)
		
		self.approve_btn = wx.Button(self.sessions_tab, label=_("Approve/Whitelist"))
		self.approve_btn.Bind(wx.EVT_BUTTON, self.on_approve)
		hbox2.Add(self.approve_btn, flag=wx.LEFT, border=10)
		
		self.remove_btn = wx.Button(self.sessions_tab, label=_("Block/Remove"))
		self.remove_btn.Bind(wx.EVT_BUTTON, self.on_remove)
		hbox2.Add(self.remove_btn, flag=wx.LEFT, border=10)
		
		vbox.Add(hbox2, flag=wx.ALIGN_CENTER|wx.ALL, border=10)
		self.sessions_tab.SetSizer(vbox)

	def _refresh_token_list(self):
		self.token_list.DeleteAllItems()
		tokens = self.config.get('admin_tokens', {})
		for addr, token in tokens.items():
			idx = self.token_list.InsertItem(self.token_list.GetItemCount(), addr)
			self.token_list.SetItem(idx, 1, "*" * len(token)) # Mask tokens
			if addr == self.current_server:
				self.token_list.SetItemTextColour(idx, wx.Colour(0, 0, 200))

	def on_add_token(self, event):
		# Simple dialog to add/edit token
		dlg = wx.TextEntryDialog(self, _("Enter Server Address (host:port):"), _("Add/Edit Token"), value=self.current_server)
		if dlg.ShowModal() == wx.ID_OK:
			addr = dlg.GetValue().strip()
			if not addr: return
			
			token_dlg = wx.TextEntryDialog(self, _("Enter Admin Token for %s:") % addr, _("Token Input"))
			if token_dlg.ShowModal() == wx.ID_OK:
				token = token_dlg.GetValue().strip()
				if token:
					if 'admin_tokens' not in self.config:
						self.config['admin_tokens'] = {}
					self.config['admin_tokens'][addr] = token
					self.config.write()
					self._refresh_token_list()
			token_dlg.Destroy()
		dlg.Destroy()

	def on_delete_token(self, event):
		idx = self.token_list.GetFirstSelected()
		if idx == -1: return
		addr = self.token_list.GetItemText(idx, 0)
		
		if gui.messageBox(_("Delete token for %s?") % addr, _("Confirm Delete"), wx.YES|wx.NO) == wx.YES:
			del self.config['admin_tokens'][addr]
			self.config.write()
			self._refresh_token_list()

	def on_login(self, event):
		# Try to get token for current server
		token = self.config.get('admin_tokens', {}).get(self.current_server)
		if not token:
			# Fallback to legacy single token if available and it matches the connection? 
			# Or just ask for it.
			token = self.config['controlserver'].get('admin_token', '')
		
		if not token:
			dlg = wx.TextEntryDialog(self, _("No token saved for %s. Enter token:") % self.current_server, _("Login"))
			if dlg.ShowModal() == wx.ID_OK:
				token = dlg.GetValue().strip()
			dlg.Destroy()
			
		if token:
			# Always update the last used token for convenience
			self.config['controlserver']['admin_token'] = token
			if self.current_server:
				self.config['admin_tokens'][self.current_server] = token
			self.config.write()
			self.client.send_admin_auth(token)
			self.notebook.SetSelection(1) # Switch to sessions tab on login attempt

	def on_close(self, event):
		self.client.set_admin_ui(None)
		self.Destroy()

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
