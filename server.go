package main

import (
	"crypto/tls"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io/ioutil"
	"log"
	"net/http"
	"net"
	"os"
	"sync"
	"time"
)

const (
	pingInterval               = 300 * time.Second
	initialTimeout             = 30 * time.Second
	generatedKeyExpirationTime = 24 * time.Hour
)

var (
	certificatePath string
	privKeyPath     string
	chainPath       string
	motdPath        string
	networkInterface string
	port            int
)

func init() {
	flag.StringVar(&certificatePath, "certificate", "cert", "SSL certificate")
	flag.StringVar(&privKeyPath, "privkey", "privkey", "SSL private key")
	flag.StringVar(&chainPath, "chain", "chain", "SSL chain")
	flag.StringVar(&motdPath, "motd", "motd", "MOTD")
	flag.StringVar(&networkInterface, "network-interface", "::", "Interface to listen on")
	flag.IntVar(&port, "port", 6837, "Server port")
}

// Define other types and functions to replicate the functionality of the Python implementation...

func main() {
	flag.Parse()

	// Load the certificate, private key, and chain
	certificate, err := tls.LoadX509KeyPair(certificatePath, privKeyPath)
	if err != nil {
		log.Fatalf("Failed to load key pair: %v", err)
	}

	chainData, err := ioutil.ReadFile(chainPath)
	if err != nil {
		log.Fatalf("Failed to read chain file: %v", err)
	}

	chainCert, err := tls.X509KeyPair(chainData, chainData)
	if err != nil {
		log.Fatalf("Failed to parse chain certificate: %v", err)
	}

	// Create the TLS configuration
	config := &tls.Config{
		Certificates: []tls.Certificate{certificate, chainCert},
	}

	// Read the MOTD if it exists
	var motd string
	if _, err := os.Stat(motdPath); err == nil {
		data, err := ioutil.ReadFile(motdPath)
		if err != nil {
			log.Fatalf("Failed to read MOTD file: %v", err)
		}
		motd = string(data)
	}

	// Initialize server state...
	globalServerState = ServerState{
		channels: make(map[string]*Channel),
		motd:     motd,
	}

	// Start the server...
	addr := fmt.Sprintf("%s:%d", networkInterface, port)
	listener, err := tls.Listen("tcp", addr, config)
	if err != nil {
		log.Fatalf("Failed to listen: %v", err)
	}
	defer listener.Close()

	log.Printf("Server listening on %s", addr)

	// Accept connections...
	for {
		conn, err := listener.Accept()
		if err != nil {
			log.Printf("Failed to accept connection: %v", err)
			continue
		}

		go handleConnection(conn)
	}
}
import (
	"bufio"
	"crypto/rand"
	"crypto/tls"
	"encoding/hex"
	"errors"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync/atomic"
)

// Define types and functions to replicate the functionality of the Python implementation...

type Channel struct {
	clients map[int]*Client
	key     string
	mu      sync.RWMutex
}

func (ch *Channel) Broadcast(msg map[string]interface{}, exclude *Client) {
	ch.mu.RLock()
	defer ch.mu.RUnlock()
	for _, client := range ch.clients {
		if client != exclude {
			client.SendMessage(msg)
		}
	}
}

	mu       sync.Mutex
}

// NewChannel creates a new Channel with the given key.
func NewChannel(key string) *Channel {
	return &Channel{
		clients: make(map[int]*Client),
		key:     key,
	}
}

// Client represents a client connected to the server.
type Client struct {
	id      int
	channel *Channel
	conn    net.Conn
	reader  *bufio.Reader
	writer  *bufio.Writer
	mu      sync.Mutex
}

type ServerState struct {
	channels map[string]*Channel
	motd     string
	mu       sync.Mutex
}

var (
	globalServerState ServerState
	clientIDCounter   int32
)

func NewChannel(key string) *Channel {
	return &Channel{
		clients: make(map[int]*Client),
		key:     key,
	}
}

func (c *Client) ReadLoop() {
	for {
		line, _, err := c.reader.ReadLine()
		if err != nil {
			if err != io.EOF {
				log.Printf("Error reading from client %d: %v", c.id, err)
			}
			c.Disconnect()
			return
		}
		c.HandleMessage(line)
	}
}

func (c *Client) Disconnect() {
	if c.channel != nil {
		c.channel.RemoveClient(c)
	}
	c.conn.Close()
}

func (c *Client) HandleMessage(line []byte) {
	c.lastActivity = time.Now()
	var msg map[string]interface{}
	if err := json.Unmarshal(line, &msg); err != nil {
		log.Printf("Error unmarshalling message from client %d: %v", c.id, err)
		c.Disconnect()
		return
	}
	if handler, ok := messageHandlers[msg["type"].(string)]; ok {
		handler(c, msg)
	} else {
		log.Printf("Unhandled message type from client %d: %v", c.id, msg)
		c.SendMessage(map[string]interface{}{
			"type":    "error",
			"message": "unknown message type",
		})
	}
}

var messageHandlers = map[string]func(*Client, map[string]interface{}){
	"join":              (*Client).handleJoin,
	"protocol_version":  (*Client).handleProtocolVersion,
	"generate_key":      (*Client).handleGenerateKey,
	// Add additional message handlers here
}

func (c *Client) handleJoin(msg map[string]interface{}) {
	// Implement join logic
	// Implement join logic with enhanced functionality
	// ...
}

func (c *Client) handleProtocolVersion(msg map[string]interface{}) {
	// Implement protocol version handling logic with enhanced functionality
	// ...
	// Implement protocol version handling logic
}

func (c *Client) handleGenerateKey(msg map[string]interface{}) {
	// Implement generate key logic with enhanced functionality
	// ...
	key := generateKey()
	c.SendMessage(map[string]interface{}{
		"type": "generate_key",
		"key":  key,
	})
}

func generateKey() string {
	b := make([]byte, 3) // Adjust size as needed.
	_, err := rand.Read(b)
	if err != nil {
		log.Fatal(err)
	}
	return hex.EncodeToString(b)
}
}

func (c *Client) HandleMessage(line []byte) {
	// Handle incoming messages from clients
}

func (c *Client) SendMessage(msg map[string]interface{}) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if err := json.NewEncoder(c.writer).Encode(msg); err != nil {
		log.Printf("Error sending message to client %d: %v", c.id, err)
	}
	c.writer.Flush()
}

func (ch *Channel) AddClient(client *Client) {
	ch.mu.Lock()
	defer ch.mu.Unlock()
	ch.clients[client.id] = client
	// Notify other clients that a new client has joined
	ch.Broadcast(map[string]interface{}{
		"type":    "client_joined",
		"user_id": client.id,
	}, client)
}

func (ch *Channel) RemoveClient(client *Client) {
	ch.mu.Lock()
	defer ch.mu.Unlock()
	delete(ch.clients, client.id)
	// Notify other clients that a client has left
	ch.Broadcast(map[string]interface{}{
		"type":    "client_left",
		"user_id": client.id,
	}, nil)
	if len(ch.clients) == 0 {
		ch.serverState.RemoveChannel(ch.key)
	}
}

func (s *ServerState) FindOrCreateChannel(key string) *Channel {
	defer s.mu.Unlock()
	s.mu.Lock()
	defer s.mu.Unlock()
	channel, exists := s.channels[key]
	if !exists {
		channel = NewChannel(key)
		s.channels[key] = channel
	}
	return channel
}

func (s *ServerState) RemoveChannel(key string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.channels, key)
}

func (s *ServerState) BroadcastToAll(msg map[string]interface{}) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, channel := range s.channels {
		channel.Broadcast(msg, nil)
	}
}

func (s *ServerState) PingClients() {
	s.BroadcastToAll(map[string]interface{}{
		"type": "ping",
	})
	for _, channel := range s.channels {
		for _, client := range channel.clients {
			if time.Since(client.lastActivity) > clientTimeout {
				client.Disconnect()
			}
		}
	}
}

func handleConnection(conn net.Conn) {
	defer conn.Close()
	client := &Client{
		id:     int(atomic.AddInt32(&clientIDCounter, 1)),
		conn:   conn,
		reader: bufio.NewReader(conn),
		writer: bufio.NewWriter(conn),
		lastActivity: time.Now(),
		lastActivity: time.Now(),
	}
	// Perform initial setup for the client
	// ...
	client.SendMOTD(globalServerState.motd)
	client.ReadLoop()
}

func (c *Client) SendMOTD(motd string) {
	if motd != "" {
		c.SendMessage(map[string]interface{}{
			"type": "motd",
			"motd": motd,
		})
	}
}

func main() {
	// Existing main function code...

	// Initialize global server state
	globalServerState = ServerState{
		channels: make(map[string]*Channel),
		motd:     motd,
	}

	// Start pinging clients periodically
	go func() {
		pingTicker := time.NewTicker(pingInterval)
		defer pingTicker.Stop()
		for {
			select {
			case <-pingTicker.C:
				globalServerState.PingClients()
			}
		}
	}()

	// Accept connections...
	for {
		conn, err := listener.Accept()
		if err != nil {
			log.Printf("Failed to accept connection: %v", err)
			continue
		}
		go handleConnection(conn)
	}
}
	reader  *bufio.Reader
	writer  *bufio.Writer
	mu      sync.Mutex
}

// Channel represents a communication channel on the server.
type Channel struct {
	clients map[int]*Client
	key     string
	mu      sync.RWMutex
}

// Broadcast sends a message to all clients in the channel, excluding the sender if provided.
func (ch *Channel) Broadcast(msg map[string]interface{}, exclude *Client) {
	ch.mu.RLock()
	defer ch.mu.RUnlock()
	for _, client := range ch.clients {
		if client != exclude {
			client.SendMessage(msg)
		}
	}
}

// AddClient adds a client to the channel and notifies other clients.
func (ch *Channel) AddClient(client *Client) {
	ch.mu.Lock()
	defer ch.mu.Unlock()
	ch.clients[client.id] = client
	// Notify other clients that a new client has joined
	ch.Broadcast(map[string]interface{}{
		"type":    "client_joined",
		"user_id": client.id,
	}, client)
}

// RemoveClient removes a client from the channel and notifies other clients.
func (ch *Channel) RemoveClient(client *Client) {
	ch.mu.Lock()
	defer ch.mu.Unlock()
	delete(ch.clients, client.id)
	// Notify other clients that a client has left
	ch.Broadcast(map[string]interface{}{
		"type":    "client_left",
		"user_id": client.id,
	}, nil)
	if len(ch.clients) == 0 {
		ch.serverState.RemoveChannel(ch.key)
	}
}

// FindOrCreateChannel finds an existing channel with the given key or creates a new one.
func (s *ServerState) FindOrCreateChannel(key string) *Channel {
	s.mu.Lock()
	defer s.mu.Unlock()
	channel, exists := s.channels[key]
	if !exists {
		channel = NewChannel(key)
		s.channels[key] = channel
	}
	return channel
}

// RemoveChannel removes a channel with the given key from the server state.
func (s *ServerState) RemoveChannel(key string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.channels, key)
}

// BroadcastToAll sends a message to all clients in all channels.
func (s *ServerState) BroadcastToAll(msg map[string]interface{}) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, channel := range s.channels {
		channel.Broadcast(msg, nil)
	}
}

// PingClients sends a ping message to all clients and disconnects inactive ones.
func (s *ServerState) PingClients() {
	s.BroadcastToAll(map[string]interface{}{
		"type": "ping",
	})
	for _, channel := range s.channels {
		for _, client := range channel.clients {
			if time.Since(client.lastActivity) > clientTimeout {
				client.Disconnect()
			}
		}
	}
}

// handleConnection handles a new client connection.
func handleConnection(conn net.Conn) {
	defer conn.Close()
	client := &Client{
		id:     int(atomic.AddInt32(&clientIDCounter, 1)),
		conn:   conn,
		reader: bufio.NewReader(conn),
		writer: bufio.NewWriter(conn),
		lastActivity: time.Now(),
		lastActivity: time.Now(),
	}
	// Perform initial setup for the client
	// ...
	client.SendMOTD(globalServerState.motd)
	client.ReadLoop()
}

func (c *Client) SendMOTD(motd string) {
	if motd != "" {
		c.SendMessage(map[string]interface{}{
			"type": "motd",
			"motd": motd,
		})
	}
}

func main() {
	// Existing main function code...

	// Initialize global server state
	globalServerState = ServerState{
		channels: make(map[string]*Channel),
		motd:     motd,
	}

	// Start pinging clients periodically
	go func() {
		pingTicker := time.NewTicker(pingInterval)
		defer pingTicker.Stop()
		for {
			select {
			case <-pingTicker.C:
				globalServerState.PingClients()
			}
		}
	}()

	// Accept connections...
	for {
		conn, err := listener.Accept()
		if err != nil {
			log.Printf("Failed to accept connection: %v", err)
			continue
		}
		go handleConnection(conn)
	}
}
