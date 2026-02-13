from twisted.internet.protocol import Protocol, Factory, defer
from twisted.python.modules import getModule
from twisted.python import log
from twisted.internet import ssl, reactor
from twisted.protocols.basic import LineReceiver
from twisted.internet.task import LoopingCall
from twisted.python import usage
from collections import OrderedDict
import json
import os
import sys
from OpenSSL import crypto
import io
import time
import random
import string
import secrets

log.startLogging(sys.stdout)

PING_INTERVAL = 300
INITIAL_TIMEOUT = 30
GENERATED_KEY_EXPIRATION_TIME = 60*60*24 
QUARANTINE_MSG_INTERVAL = 5.0

DATA_DIR = "data"
AUTH_KEYS_FILE = os.path.join(DATA_DIR, "authorized_keys.json")
SEEN_KEYS_FILE = os.path.join(DATA_DIR, "seen_keys.json")
ADMIN_TOKEN_FILE = os.path.join(DATA_DIR, "admin.token")

class Channel(object):
	def __init__(self, key, server_state=None):
		self.clients = OrderedDict()
		self.key = key
		self.server_state = server_state
		self.quarantine_loop = None
		log.msg(f"Channel created for key: {self.key}")
		self.check_authorization()

	def check_authorization(self):
		self.is_authorized = self.server_state.is_key_authorized(self.key)
		log.msg(f"Checking auth for {self.key}: {self.is_authorized}")
		if self.is_authorized:
			self.stop_quarantine()
		else:
			self.start_quarantine()

	def start_quarantine(self):
		if self.quarantine_loop is None or not self.quarantine_loop.running:
			self.quarantine_loop = LoopingCall(self.send_quarantine_msg)
			self.quarantine_loop.start(QUARANTINE_MSG_INTERVAL)

	def stop_quarantine(self):
		if self.quarantine_loop and self.quarantine_loop.running:
			self.quarantine_loop.stop()
		self.quarantine_loop = None

	def send_quarantine_msg(self):
		if self.clients:
			log.msg(f"Sending quarantine message to channel {self.key}")
		for client in self.clients.values():
			client.send(
				type='speak',
				sequence=['Nicht autorisiert. Warten auf Freigabe.']
			)

	def add_client(self, client):
		if client.protocol.protocol_version == 1:
			ids = [c.user_id for c in self.clients.values()]
			msg = dict(type='channel_joined', channel=self.key, user_ids=ids, origin=client.user_id)
		else:
			clients = [i.as_dict() for i in self.clients.values()]
			msg = dict(type='channel_joined', channel=self.key, origin=client.user_id, clients=clients)
		
		client.send(**msg)
		for existing_client in self.clients.values():
			if existing_client.protocol.protocol_version == 1:
				existing_client.send(type='client_joined', user_id=client.user_id)
			else:
				existing_client.send(type='client_joined', client=client.as_dict())
		self.clients[client.user_id] = client

	def remove_connection(self, con):
		if con.user_id in self.clients:
			del self.clients[con.user_id]
		for client in self.clients.values():
			if client.protocol.protocol_version == 1:
				client.send(type='client_left', user_id=con.user_id)
			else:
				client.send(type='client_left', client=con.as_dict())
		if not self.clients:
			self.stop_quarantine()
			self.server_state.remove_channel(self.key)

	def ping_clients(self):
		self.send_to_clients({'type': 'ping'})

	def send_to_clients(self, obj, exclude=None, origin=None):
		if not self.is_authorized:
			return
		for client in self.clients.values():
			if client is exclude:
				continue
			client.send(origin=origin, **obj)

class Handler(LineReceiver):
	delimiter = b'\n'
	connection_id = 0
	MAX_LENGTH = 20*1048576

	def __init__(self):
		self.connection_id = Handler.connection_id + 1
		Handler.connection_id += 1
		self.protocol_version = 1

	def connectionMade(self):
		log.msg("Connection %d from %s" % (self.connection_id, self.transport.getPeer()))
		self.transport.setTcpNoDelay(True)
		self.bytes_sent = 0
		self.bytes_received = 0
		self.user = User(protocol=self)
		self.cleanup_timer = reactor.callLater(INITIAL_TIMEOUT, self.cleanup)
		self.user.send_motd()

	def connectionLost(self, reason):
		log.msg("Connection %d lost, bytes sent: %d received: %d" % (self.connection_id, self.bytes_sent, self.bytes_received))
		self.user.connection_lost()
		if self.cleanup_timer is not None and not self.cleanup_timer.cancelled:
			self.cleanup_timer.cancel()

	def lineReceived(self, line):
		self.bytes_received += len(line)
		try:
			parsed = json.loads(line)
			if not isinstance(parsed, dict):
				raise ValueError
		except ValueError:
			log.msg("Unable to parse %r" % line)
			self.transport.loseConnection()
			return
		if 'type' not in parsed:
			log.msg("Invalid object received: %r" % parsed)
			return
		parsed.pop('origin', None)
		
		if parsed['type'].startswith('admin_'):
			self.handle_admin_command(parsed)
			return
		if parsed['type'] == 'auth_admin':
			self.do_auth_admin(parsed)
			return

		if parsed['type'] in ('ping', 'pong'):
			method_name = "do_" + parsed['type']
			if hasattr(self, method_name):
				getattr(self, method_name)(parsed)
			return

		if self.user.channel is not None:
			self.user.channel.send_to_clients(parsed, exclude=self.user, origin=self.user.user_id)
			return
		elif not hasattr(self, "do_"+parsed['type']):
			log.msg("No function for type %s" % parsed['type'])
			return
		getattr(self, "do_"+parsed['type'])(parsed)

	def do_ping(self, obj):
		self.send(type='pong')

	def do_pong(self, obj):
		pass

	def handle_admin_command(self, parsed):
		if not self.user.is_admin:
			self.send(type='error', error='access_denied')
			return
		method_name = "do_" + parsed['type']
		if hasattr(self, method_name):
			getattr(self, method_name)(parsed)
		else:
			self.send(type='error', error='unknown_admin_command')

	def do_auth_admin(self, obj):
		token = obj.get('token')
		if self.user.server_state.check_admin_token(token):
			self.user.is_admin = True
			self.send(type='auth_admin_response', success=True)
			log.msg(f"Admin authenticated: Connection {self.connection_id}")
			self.do_admin_list_channels({})
		else:
			self.send(type='auth_admin_response', success=False)
			log.msg(f"Failed admin auth attempt: Connection {self.connection_id}")

	def do_admin_list_channels(self, obj):
		channels_info = []
		state = self.user.server_state
		all_keys = set(state.authorized_keys) | set(state.channels.keys()) | set(state.seen_keys)
		for key in all_keys:
			is_online = key in state.channels
			is_authorized = state.is_key_authorized(key)
			client_count = len(state.channels[key].clients) if is_online else 0
			channels_info.append({
				'key': key,
				'client_count': client_count,
				'authorized': is_authorized,
				'online': bool(is_online)
			})
		log.msg(f"Sending list: {channels_info}")
		self.send(type='admin_channel_list', channels=channels_info)

	def do_admin_approve_channel(self, obj):
		key = obj.get('key')
		if key:
			self.user.server_state.authorize_key(key)
			self.send(type='admin_response', command='approve', success=True, key=key)
			if key in self.user.server_state.channels:
				self.user.server_state.channels[key].check_authorization()

	def do_admin_remove_channel(self, obj):
		key = obj.get('key')
		if key:
			self.user.server_state.deauthorize_key(key)
			self.send(type='admin_response', command='remove', success=True, key=key)
			if key in self.user.server_state.channels:
				self.user.server_state.channels[key].check_authorization()

	def do_join(self, obj):
		log.msg(f"Connection {self.connection_id} requested join to channel: {obj.get('channel')}")
		if 'channel' not in obj or not obj['channel']:
			self.send(type='error', error='invalid_parameters')
			return
		self.user.join(obj['channel'], connection_type=obj.get('connection_type'))
		self.cleanup_timer.cancel()

	def do_protocol_version(self, obj):
		if 'version' not in obj:
			return
		self.protocol_version = obj['version']

	def do_generate_key(self, obj):
		self.user.generate_key()

	def send(self, **msg):
		origin = msg.pop('origin', None)
		if self.protocol_version > 1 and origin:
			msg['origin'] = origin
		obj = json.dumps(msg).encode('ascii')
		self.bytes_sent += len(obj)
		self.sendLine(obj)

	def cleanup(self):
		log.msg("Connection %d timed out" % self.connection_id)
		self.transport.abortConnection()
		self.cleanup_timer = None

class User(object):
	user_id = 0
	def __init__(self, protocol):
		self.protocol = protocol
		self.channel = None
		self.server_state = self.protocol.factory.server_state
		self.connection_type = None
		self.user_id = User.user_id + 1
		User.user_id += 1
		self.is_admin = False

	def as_dict(self):
		return dict(id=self.user_id, connection_type=self.connection_type)

	def generate_key(self):
		ip = self.protocol.transport.getPeer().host
		if ip in self.server_state.generated_ips and time.time()-self.server_state.generated_ips[ip] < 1:
			self.send(type="error", message="too many keys")
			self.protocol.transport.loseConnection()
			return
		key = "".join([random.choice(string.digits) for i in range(7)])
		while key in self.server_state.generated_keys or key in self.server_state.channels.keys():
			key = "".join([random.choice(string.digits) for i in range(7)])
		self.server_state.generated_keys.add(key)
		self.server_state.generated_ips[ip] = time.time()
		reactor.callLater(GENERATED_KEY_EXPIRATION_TIME, lambda: self.server_state.generated_keys.remove(key))
		if key:
			self.send(type="generate_key", key=key)
		return key

	def connection_lost(self):
		if self.channel is not None:
			self.channel.remove_connection(self)

	def join(self, channel, connection_type):
		if self.channel:
			self.send(type="error", error="already_joined")
			return
		self.connection_type = connection_type
		self.channel = self.server_state.find_or_create_channel(channel)
		self.channel.add_client(self)

	def send(self, **obj):
		self.protocol.send(**obj)

	def send_motd(self):
		if self.server_state.motd is not None:
			self.send(type='motd', motd=self.server_state.motd)

class RemoteServerFactory(Factory):
	def __init__(self, server_state):
		self.server_state = server_state
	def ping_connected_clients(self):
		for channel in self.server_state.channels.values():
			channel.ping_clients()

class ServerState(object):
	def __init__(self):
		self.channels = {}
		self.generated_keys = set()
		self.generated_ips = {}
		self.motd = None
		self.authorized_keys = set()
		self.seen_keys = set()
		self.admin_token = ""
		self.init_data_dir()
		self.load_keys()
		self.load_seen_keys()
		self.load_or_generate_admin_token()

	def init_data_dir(self):
		if not os.path.exists(DATA_DIR):
			os.makedirs(DATA_DIR)

	def load_keys(self):
		if os.path.exists(AUTH_KEYS_FILE):
			try:
				with open(AUTH_KEYS_FILE, 'r') as f:
					self.authorized_keys = set(json.load(f))
					log.msg(f"Loaded {len(self.authorized_keys)} authorized keys.")
			except Exception as e:
				log.err(f"Failed to load keys: {e}")

	def save_keys(self):
		try:
			with open(AUTH_KEYS_FILE, 'w') as f:
				json.dump(list(self.authorized_keys), f)
		except Exception as e:
			log.err(f"Failed to save keys: {e}")

	def load_seen_keys(self):
		if os.path.exists(SEEN_KEYS_FILE):
			try:
				with open(SEEN_KEYS_FILE, 'r') as f:
					self.seen_keys = set(json.load(f))
			except:
				pass
		# Always include authorized keys in seen keys
		self.seen_keys.update(self.authorized_keys)

	def save_seen_keys(self):
		try:
			with open(SEEN_KEYS_FILE, 'w') as f:
				json.dump(list(self.seen_keys), f)
			log.msg(f"Saved {len(self.seen_keys)} seen keys to {SEEN_KEYS_FILE}")
		except Exception as e:
			log.err(f"Failed to save seen keys: {e}")

	def is_key_authorized(self, key):
		authorized = key in self.authorized_keys
		log.msg(f"Is key '{key}' authorized? {authorized}")
		return authorized

	def authorize_key(self, key):
		self.authorized_keys.add(key)
		self.seen_keys.add(key)
		self.save_keys()
		self.save_seen_keys()
		log.msg(f"Key authorized: {key}")

	def deauthorize_key(self, key):
		if key in self.authorized_keys:
			self.authorized_keys.remove(key)
			self.save_keys()
			log.msg(f"Key deauthorized: {key}")
		if key in self.seen_keys:
			self.seen_keys.remove(key)
			self.save_seen_keys()
			log.msg(f"Key removed from seen list: {key}")

	def load_or_generate_admin_token(self):
		if os.path.exists(ADMIN_TOKEN_FILE):
			with open(ADMIN_TOKEN_FILE, 'r') as f:
				self.admin_token = f.read().strip()
		if not self.admin_token:
			self.admin_token = secrets.token_urlsafe(32)
			with open(ADMIN_TOKEN_FILE, 'w') as f:
				f.write(self.admin_token)
			log.msg(f"Generated new admin token in {ADMIN_TOKEN_FILE}")

	def check_admin_token(self, token):
		if not token or not self.admin_token: return False
		return secrets.compare_digest(token, self.admin_token)

	def remove_channel(self, channel):
		del self.channels[channel]

	def find_or_create_channel(self, name):
		log.msg(f"find_or_create_channel called for: {name}")
		if name not in self.seen_keys:
			log.msg(f"New key seen: {name}")
			self.seen_keys.add(name)
			self.save_seen_keys()
		
		if name in self.channels:
			channel = self.channels[name]
		else:
			channel = Channel(name, self)
			self.channels[name] = channel
		return channel

class Options(usage.Options):
	optParameters = [
		["certificate", "c", "cert", "SSL certificate"],
		["privkey", "k", "privkey", "SSL private key"],
		["chain", "C", "chain", "SSL chain"],
		["motd", "m", "motd", "MOTD"],
		["network-interface", "i", "::", "Interface to listen on"],
		["port", "p", "6837", "Server port"],
	]

def main():
	config = Options()
	config.parseOptions()
	privkey = open(config['privkey']).read()
	certData = open(config['certificate']).read()
	chain = open(config['chain']).read()
	privkey = crypto.load_privatekey(crypto.FILETYPE_PEM, privkey)
	certificate = crypto.load_certificate(crypto.FILETYPE_PEM, certData)
	chain = crypto.load_certificate(crypto.FILETYPE_PEM, chain)
	context_factory = ssl.CertificateOptions(privateKey=privkey, certificate=certificate, extraCertChain=[chain])
	state = ServerState()
	if config['motd'] and os.path.exists(config['motd']):
		with io.open(config['motd'], encoding='utf-8') as fp:
			state.motd = fp.read().strip()
	else:
		state.motd = None
	f = RemoteServerFactory(state)
	l = LoopingCall(f.ping_connected_clients)
	l.start(PING_INTERVAL)
	f.protocol = Handler
	reactor.listenSSL(int(config['port']), f, context_factory, interface=config['network-interface'])
	reactor.run()
	return defer.Deferred()

if __name__ == '__main__':
	res = main()
