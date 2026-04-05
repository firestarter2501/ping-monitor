#!/usr/bin/env python3
"""
Ping Monitor - Monitor hosts and send Discord webhook notifications

This script monitors network hosts by sending periodic ping requests and
sends Discord webhook notifications when hosts become unreachable.

Asyncio-based implementation for concurrent ping monitoring.
"""

import argparse
import asyncio
import gc
import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List, Any

# Constants
DEFAULT_CONFIG_PATH = 'config.json'
DEFAULT_PORT = 8080
PING_TIMEOUT = 2  # seconds
PING_PACKET_COUNT = 1
PONG_TIMEOUT = 5  # seconds for subprocess timeout
DISCORD_TIMEOUT = 10  # seconds for webhook timeout
USER_AGENT = 'PingMonitor/1.0'

# Global state management
global_status: Dict[str, Any] = {}
global_lock = asyncio.Lock()


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
        self._http_server: HTTPServer | None = None

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

    async def ping_host(self, host):
        """Send a ping to the specified host asynchronously.

        Args:
            host: Hostname or IP address to ping

        Returns:
            tuple: (success: bool, response_ms: float or None)
        """
        try:
            # Create subprocess asynchronously
            process = await asyncio.create_subprocess_exec(
                'ping', '-c', str(PING_PACKET_COUNT), '-W', str(PING_TIMEOUT), host,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # Wait for completion with timeout
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=PONG_TIMEOUT
                )

                if process.returncode == 0:
                    output = stdout.decode('utf-8')
                    return self._parse_ping_response(output)

                return False, None

            except asyncio.TimeoutError:
                # Kill the process on timeout
                try:
                    process.kill()
                    await process.wait()
                except ProcessLookupError:
                    pass
                return False, None

        except Exception:
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

    async def send_discord_notification(self, message):
        """Send a notification to Discord webhook asynchronously.

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

    async def monitor_loop(self):
        """Main monitoring loop - runs concurrently using asyncio."""
        self.running = True
        print(f"Starting ping monitor (interval: {self.monitor_interval}s)")
        print(f"Monitoring {len(self.targets)} target(s)")

        iteration_count = 0

        while self.running:
            async with self.lock:
                targets_snapshot = list(self.targets)

            # Create tasks for all targets to ping concurrently
            tasks = []
            for target in targets_snapshot:
                host = target['host']
                name = target.get('name', host)
                tasks.append(self._ping_and_update(host, name))

            # Execute all pings concurrently
            await asyncio.gather(*tasks, return_exceptions=True)

            await asyncio.sleep(self.monitor_interval)
            iteration_count += 1

            # Periodic garbage collection every 100 iterations
            if iteration_count % 100 == 0:
                gc.collect()

    async def _ping_and_update(self, host, name):
        """Ping a host and update its status concurrently.

        Args:
            host: Hostname or IP address to ping
            name: Display name for the host
        """
        # Perform ping
        success, response_ms = await self.ping_host(host)

        # Update status
        async with self.lock:
            if host not in self.status:
                return

            if success:
                await self._handle_ping_success(host, name, response_ms)
            else:
                await self._handle_ping_failure(host, name)

            self.status[host]['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    async def _handle_ping_success(self, host, name, response_ms):
        """Handle successful ping response."""
        if self.status[host]['status'] == 'alert':
            # Recovery from alert
            if self.status[host]['alert_sent']:
                await self.send_discord_notification(f"[Recovery] {name}: Connection restored")
                self.status[host]['alert_sent'] = False

        self.status[host]['status'] = 'normal'
        self.status[host]['lost_count'] = 0
        self.status[host]['last_response_ms'] = f"{response_ms:.1f}"

    async def _handle_ping_failure(self, host, name):
        """Handle failed ping response."""
        self.status[host]['lost_count'] += 1
        self.status[host]['last_response_ms'] = 'N/A'

        if self.status[host]['status'] == 'normal':
            if self.status[host]['lost_count'] >= self.alert_threshold:
                self.status[host]['status'] = 'alert'
                if not self.status[host]['alert_sent']:
                    await self.send_discord_notification(
                        f"[Alert] {name}: {self.status[host]['lost_count']} consecutive ping losses"
                    )
                    self.status[host]['alert_sent'] = True

    def start(self):
        """Start the monitor and HTTP server."""
        # Create event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # Create tasks for monitor and HTTP server
            monitor_task = loop.create_task(self.monitor_loop())
            http_task = loop.create_task(self._run_http_server())

            # Run both tasks concurrently
            loop.run_until_complete(asyncio.gather(monitor_task, http_task, return_exceptions=True))
        except KeyboardInterrupt:
            print("\nShutting down...")
            self.running = False
            # Cancel all tasks
            for task in asyncio.all_tasks(loop):
                task.cancel()
            # Force garbage collection on shutdown
            gc.collect()
        finally:
            loop.close()

    async def _run_http_server(self):
        """Run HTTP server asynchronously."""
        server = HTTPServer(('0.0.0.0', self.port), MonitorHandler)
        self._http_server = server
        print(f"Web server started at http://0.0.0.0:{self.port}")
        print("Press Ctrl+C to stop")

        # Run serve_forever in executor to avoid blocking event loop
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, server.serve_forever)
        except Exception:
            pass
        finally:
            server.server_close()


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
        # HTTP handler runs in a separate thread, so we need to use run_coroutine_threadsafe
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
            # Schedule the async task in the event loop
            future = asyncio.run_coroutine_threadsafe(_get_status_json(), loop)
            # Wait for the result with a timeout
            status_copy = future.result(timeout=5)
        except Exception as e:
            # Fallback: direct access (not thread-safe but works for read-only)
            print(f"Warning: Using fallback status access: {e}")
            status_copy = json.dumps(dict(global_status))

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(status_copy.encode('utf-8'))


async def _get_status_json():
    """Get status as JSON string with async lock."""
    async with global_lock:
        return json.dumps(dict(global_status))


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