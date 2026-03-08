#!/usr/bin/env python3
"""
Ping Monitor - Monitor hosts and send Discord webhook notifications

This script monitors network hosts by sending periodic ping requests and
sends Discord webhook notifications when hosts become unreachable.
"""

import argparse
import gc
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import weakref
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# Constants
DEFAULT_CONFIG_PATH = 'config.json'
DEFAULT_PORT = 8080
PING_TIMEOUT = 2  # seconds
PING_PACKET_COUNT = 1
PONG_TIMEOUT = 5  # seconds for subprocess timeout
DISCORD_TIMEOUT = 10  # seconds for webhook timeout
USER_AGENT = 'PingMonitor/1.0'

# Global state management
global_status = {}
global_lock = threading.Lock()


class PingMonitor:
    """Monitor network hosts and send alerts when they become unreachable."""

    def __init__(self, config_path, port):
        """Initialize the ping monitor with configuration.

        Args:
            config_path: Path to the JSON configuration file
            port: Port number for the web server
        """
        self.config_path = config_path
        self.port = port
        self.status = global_status
        self.lock = global_lock
        self.running = False

        self.load_config()

    def load_config(self):
        """Load and parse the configuration file."""
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)

            self.monitor_interval = config.get('monitor', {}).get('interval', 5)
            self.alert_threshold = config.get('monitor', {}).get('alert_threshold', 3)
            self.targets = config.get('targets', [])
            self.discord_webhook_url = config.get('discord', {}).get('webhook_url', '')

            self._initialize_target_status()

        except FileNotFoundError:
            print(f"Error: Configuration file '{self.config_path}' not found.")
            exit(1)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in configuration file: {e}")
            exit(1)

    def _initialize_target_status(self):
        """Initialize status tracking for each target host."""
        for target in self.targets:
            host = target['host']
            self.status[host] = {
                'name': target.get('name', host),
                'status': 'normal',
                'lost_count': 0,
                'last_response_ms': 'N/A',
                'last_check': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'alert_sent': False
            }

    def ping_host(self, host):
        """Send a ping to the specified host.

        Args:
            host: Hostname or IP address to ping

        Returns:
            tuple: (success: bool, response_ms: float or None)
        """
        try:
            # Use subprocess with explicit resource cleanup
            result = subprocess.run(
                ['ping', '-c', str(PING_PACKET_COUNT), '-W', str(PING_TIMEOUT), host],
                capture_output=True,
                text=True,
                timeout=PONG_TIMEOUT
            )

            if result.returncode == 0:
                # Extract output and let result be garbage collected
                output = result.stdout
                return self._parse_ping_response(output)

            return False, None

        except (subprocess.TimeoutExpired, Exception):
            return False, None

    def _parse_ping_response(self, output):
        """Parse ping command output to extract response time.

        Args:
            output: stdout from ping command

        Returns:
            tuple: (success: bool, response_ms: float)
        """
        # Try Linux format: time=25.123 ms
        for line in output.split('\n'):
            if 'time=' in line:
                try:
                    time_str = line.split('time=')[1].split(' ')[0]
                    return True, float(time_str)
                except (IndexError, ValueError):
                    pass

        # Try macOS format: round-trip min/avg/max/stddev = 43.520/43.520/43.520/0.000 ms
        for line in output.split('\n'):
            if 'round-trip' in line and 'min/avg/max' in line:
                try:
                    stats_part = line.split('=')[1].strip()
                    times = stats_part.split('ms')[0].split('/')
                    if len(times) >= 2:
                        return True, float(times[1])
                except (IndexError, ValueError):
                    pass

        # Ping succeeded but couldn't parse response time
        return True, 0.0

    def send_discord_notification(self, message):
        """Send a notification to Discord webhook.

        Args:
            message: Message content to send
        """
        if not self.discord_webhook_url:
            return

        try:
            data = json.dumps({'content': message}).encode('utf-8')
            req = urllib.request.Request(
                self.discord_webhook_url,
                data=data,
                headers={
                    'Content-Type': 'application/json',
                    'User-Agent': USER_AGENT
                }
            )
            # Use context manager to ensure response is properly closed
            with urllib.request.urlopen(req, timeout=DISCORD_TIMEOUT) as response:
                print(f"Discord notification sent: {message}")
                print(f"Response status: {response.status}")

        except urllib.error.HTTPError as e:
            print(f"Discord notification failed (HTTP {e.code}): {e.reason}")
            try:
                print(f"Response body: {e.read().decode()}")
            except Exception:
                pass
        except urllib.error.URLError as e:
            print(f"Discord notification failed (URL error): {e.reason}")
        except Exception as e:
            print(f"Discord notification failed: {e}")

    def monitor_loop(self):
        """Main monitoring loop - runs in background thread."""
        self.running = True
        print(f"Starting ping monitor (interval: {self.monitor_interval}s)")
        print(f"Monitoring {len(self.targets)} target(s)")

        iteration_count = 0

        while self.running:
            with self.lock:
                targets_snapshot = list(self.targets)

            for target in targets_snapshot:
                host = target['host']
                name = target.get('name', host)

                # Perform ping
                success, response_ms = self.ping_host(host)

                # Update status
                with self.lock:
                    if host not in self.status:
                        continue

                    if success:
                        self._handle_ping_success(host, name, response_ms)
                    else:
                        self._handle_ping_failure(host, name)

                    self.status[host]['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            time.sleep(self.monitor_interval)
            iteration_count += 1

            # Periodic garbage collection every 100 iterations
            if iteration_count % 100 == 0:
                gc.collect()

    def _handle_ping_success(self, host, name, response_ms):
        """Handle successful ping response."""
        if self.status[host]['status'] == 'alert':
            # Recovery from alert
            if self.status[host]['alert_sent']:
                self.send_discord_notification(f"[Recovery] {name}: Connection restored")
                self.status[host]['alert_sent'] = False

        self.status[host]['status'] = 'normal'
        self.status[host]['lost_count'] = 0
        self.status[host]['last_response_ms'] = f"{response_ms:.1f}"

    def _handle_ping_failure(self, host, name):
        """Handle failed ping response."""
        self.status[host]['lost_count'] += 1
        self.status[host]['last_response_ms'] = 'N/A'

        if self.status[host]['status'] == 'normal':
            if self.status[host]['lost_count'] >= self.alert_threshold:
                self.status[host]['status'] = 'alert'
                if not self.status[host]['alert_sent']:
                    self.send_discord_notification(
                        f"[Alert] {name}: {self.status[host]['lost_count']} consecutive ping losses"
                    )
                    self.status[host]['alert_sent'] = True

    def start(self):
        """Start the monitor and HTTP server."""
        # Start monitoring thread
        monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        monitor_thread.start()

        # Start HTTP server
        server = HTTPServer(('0.0.0.0', self.port), MonitorHandler)
        print(f"Web server started at http://0.0.0.0:{self.port}")
        print("Press Ctrl+C to stop")

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")
            self.running = False
            # Force garbage collection on shutdown
            gc.collect()


class MonitorHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the web interface."""

    # Class-level cache for template to avoid repeated file I/O
    _template_cache = None
    _template_cache_lock = threading.Lock()

    def log_message(self, format, *args):
        """Suppress default HTTP server logging."""
        pass

    def do_GET(self):
        """Handle GET requests."""
        try:
            if self.path == '/':
                self.serve_html()
            elif self.path == '/api/status':
                self.serve_status()
            else:
                self.send_error(404, 'Not Found')
        except Exception:
            # Handle any unexpected errors to prevent thread crashes
            self.send_error(500, 'Internal Server Error')

    def serve_html(self):
        """Serve the main HTML page using cached template."""
        html = self._get_cached_template()
        if html is None:
            self.send_error(404, 'Template not found')
            return

        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def _get_cached_template(self):
        """Get HTML template from cache or file.

        Returns:
            str: HTML content or None if not found
        """
        with self._template_cache_lock:
            if self._template_cache is not None:
                return self._template_cache

            try:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                template_path = os.path.join(script_dir, 'templates', 'index.html')

                with open(template_path, 'r') as f:
                    content = f.read()
                    self._template_cache = content
                    return content

            except FileNotFoundError:
                return None

    def serve_status(self):
        """Serve current status as JSON."""
        with global_lock:
            status_copy = json.dumps(dict(global_status))

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(status_copy.encode('utf-8'))


def main():
    """Main entry point."""
    # Get default values from environment variables
    config_path = os.environ.get('PING_MONITOR_CONFIG', DEFAULT_CONFIG_PATH)
    port = int(os.environ.get('PING_MONITOR_PORT', DEFAULT_PORT))

    parser = argparse.ArgumentParser(description='Ping Monitor')
    parser.add_argument('--config', '-c', default=config_path,
                        help=f'Path to configuration file (default: {config_path}, env: PING_MONITOR_CONFIG)')
    parser.add_argument('--port', '-p', type=int, default=port,
                        help=f'Port number for web server (default: {port}, env: PING_MONITOR_PORT)')

    args = parser.parse_args()

    monitor = PingMonitor(args.config, args.port)
    monitor.start()


if __name__ == '__main__':
    main()