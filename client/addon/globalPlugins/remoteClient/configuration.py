import os
import socket
from io import StringIO

import configobj
import globalVars
from configobj import validate

from .connection_info import ConnectionInfo

CONFIG_FILE_NAME = 'remote.ini'

_config = None
configspec = StringIO("""
[connections]
	last_connected = list(default=list())
[controlserver]
	version = float(default=2.0)
	autoconnect = boolean(default=True)
	self_hosted = boolean(default=False)
	connection_type = integer(default=0)
	host = string(default="danijel0.danijels-computer.de")
	port = integer(default=6837)
	key = string(default="")
	admin_token = string(default="")

[admin_tokens]
	__many__ = string(default="")

[seen_motds]
	__many__ = string(default="")

[trusted_certs]
	__many__ = string(default="")

[ui]
	play_sounds = boolean(default=True)
""")
def get_config():
	global _config
	if not _config:
		path = os.path.abspath(os.path.join(globalVars.appArgs.configPath, CONFIG_FILE_NAME))
		_config = configobj.ConfigObj(infile=path, configspec=configspec, create_empty=True)
		val = validate.Validator()
		_config.validate(val, copy=True)
		# Always enforce hostname as the session name (key)
		_config['controlserver']['key'] = socket.gethostname()
	return _config

def migrate_config(parent_window):
	"""Checks if the configuration needs migration and asks the user."""
	import wx
	import gui
	conf = get_config()
	if conf['controlserver']['version'] >= 3.1:
		return

	def do_migration():
		# Check if current config is already "standard" or if it's empty
		is_empty = not conf['controlserver']['host'] or conf['controlserver']['host'] == ""
		is_non_standard = conf['controlserver']['host'] != "danijel0.danijels-computer.de" or not conf['controlserver']['autoconnect']
		
		if is_empty:
			# Just apply defaults silently for fresh installs
			conf['controlserver']['host'] = "danijel0.danijels-computer.de"
			conf['controlserver']['autoconnect'] = True
			conf['controlserver']['version'] = 3.1
			conf.write()
			return

		if is_non_standard:
			msg = _("A new standard configuration for NVDA Remote is available. \n\n"
					"Should the connection to {new_host} be activated and autoconnect enabled? \n"
					"Your current host is: {current_host}").format(
						new_host="danijel0.danijels-computer.de",
						current_host=conf['controlserver']['host']
					)
			if gui.messageBox(msg, _("NVDA Remote Configuration Update"), wx.YES_NO | wx.ICON_QUESTION, parent=parent_window) == wx.YES:
				conf['controlserver']['host'] = "danijel0.danijels-computer.de"
				conf['controlserver']['autoconnect'] = True
				# If we update the host, we also set the key to hostname to be sure
				conf['controlserver']['key'] = socket.gethostname()
				gui.messageBox(_("Configuration successfully updated to new standards."), _("Success"), wx.OK | wx.ICON_INFORMATION)
		
		# Set version to 3.1 anyway to stop asking
		conf['controlserver']['version'] = 3.1
		conf.write()

	wx.CallAfter(do_migration)

def migrate_legacy_token(parent_window, client):
	"""Checks for a legacy admin token and asks the user to migrate it."""
	import wx
	import gui
	conf = get_config()
	legacy_token = conf['controlserver'].get('admin_token', '')
	
	if not legacy_token:
		return

	# If it's already in the new structure, we don't need to ask (or we could, but let's be smart)
	# Check if this token is already assigned to any server
	if any(t == legacy_token for t in conf.get('admin_tokens', {}).values()):
		# Already migrated or manually added
		return

	def do_migration():
		msg = _("New configuration structure\nLegacy admin tokens found. Should they be transferred? In the following screen, you must name the token (e.g., the server address).")
		if gui.messageBox(msg, _("Configuration Migration"), wx.YES_NO | wx.ICON_QUESTION, parent=parent_window) == wx.YES:
			dlg = wx.TextEntryDialog(parent_window, _("Enter a name or server address for this token:"), _("Token Migration"), value=conf['controlserver'].get('host', ''))
			if dlg.ShowModal() == wx.ID_OK:
				name = dlg.GetValue().strip()
				if name:
					if 'admin_tokens' not in conf:
						conf['admin_tokens'] = {}
					conf['admin_tokens'][name] = legacy_token
					# We keep the legacy one for now to avoid losing data if migration fails, 
					# but the UI will now prefer the new structure.
					conf.write()
					gui.messageBox(_("Token successfully transferred."), _("Success"), wx.OK | wx.ICON_INFORMATION)
			dlg.Destroy()
		else:
			# User denied migration, clear legacy token to stop asking
			conf['controlserver']['admin_token'] = ""
			conf.write()
			gui.messageBox(_("Legacy token deleted."), _("Configuration Updated"), wx.OK | wx.ICON_INFORMATION)

	wx.CallAfter(do_migration)

def write_connection_to_config(connection_info: ConnectionInfo):
	"""Writes a connection to the last connected section of the config.
	If the connection is already in the config, move it to the end.
	
	Args:
		connection_info: The ConnectionInfo object containing connection details
	"""
	conf = get_config()
	last_cons = conf['connections']['last_connected']
	address = connection_info.getAddress()
	if address in last_cons:
		conf['connections']['last_connected'].remove(address)
	conf['connections']['last_connected'].append(address)
	conf.write()
