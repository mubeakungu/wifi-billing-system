#!/usr/bin/env python3
"""
Built-in hotspot gateway for routers that have no hotspot of their own (e.g. Tenda).

This Linux machine becomes the customers' gateway:

  * DHCP (dnsmasq) hands out addresses with this machine as the router,
    so all customer traffic passes through it. Turn DHCP OFF on your router.
  * nftables lets only logged-in customers through. Anyone else who opens a
    web page is sent to the login page (port 8080), the same page MikroTik
    routers use (backend/hotspot/login.html).
  * A voucher code is checked with the billing system (/api/gateway/login),
    which applies the time limit and keeps a code on one device. When the time
    is up the customer's address is removed and they see the login page again.

Runs in the "gateway" service of docker-compose.yml (host network, privileged).
Needs only the standard library plus the nft, dnsmasq, iptables, conntrack and ip tools.

Settings (backend/.env), all optional:
  GATEWAY_INTERFACE   network card customers come in on (default: the one with the default route)
  GATEWAY_DHCP_RANGE  e.g. 192.168.0.120,192.168.0.250 (default: .50 to .250 of the network)
  GATEWAY_DHCP        1 (default) to run DHCP here, 0 if something else hands out this machine as gateway
  GATEWAY_DNS         DNS servers given to customers (default 8.8.8.8,1.1.1.1)
  GATEWAY_FREE_MACS   devices that never need a code, e.g. your own PC and phone (comma separated)
  GATEWAY_PORT        login page port (default 8080)
  GATEWAY_API_URL     billing system API as seen from this machine (default http://127.0.0.1:5000/api)
"""

import hashlib
import hmac
import html
import ipaddress
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('gateway')

TABLE = 'wifibilling'
HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.getenv('GATEWAY_TEMPLATE', os.path.join(HERE, 'hotspot', 'login.html'))
LEASE_FILE = os.getenv('GATEWAY_LEASE_FILE', '/data/dnsmasq.leases')
HEARTBEAT_EVERY = 30
# Phones open these to detect a login page; after login there is no point going back to them
CHECK_HOSTS = {'connectivitycheck.gstatic.com', 'connectivitycheck.android.com', 'clients3.google.com',
               'clients1.google.com', 'captive.apple.com', 'www.apple.com', 'www.msftconnecttest.com',
               'www.msftncsi.com', 'detectportal.firefox.com', 'nmcheck.gnome.org', 'neverssl.com'}


def run(cmd, check=True, stdin=None):
    result = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f'{" ".join(cmd)}: {result.stderr.strip() or result.stdout.strip()}')
    return result


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def default_route():
    out = run(['ip', '-4', 'route', 'show', 'default']).stdout.split()
    via = out[out.index('via') + 1] if 'via' in out else None
    dev = out[out.index('dev') + 1] if 'dev' in out else None
    return via, dev


def interface_address(dev):
    out = run(['ip', '-4', '-o', 'addr', 'show', 'dev', dev]).stdout
    m = re.search(r'inet (\d+\.\d+\.\d+\.\d+/\d+)', out)
    if not m:
        raise RuntimeError(f'{dev} has no IPv4 address')
    return ipaddress.ip_interface(m.group(1))


class Config:
    def __init__(self):
        upstream, dev = default_route()
        self.iface = os.getenv('GATEWAY_INTERFACE', '').strip() or dev
        if not self.iface:
            raise RuntimeError('No default route: set GATEWAY_INTERFACE')
        addr = interface_address(self.iface)
        self.ip = str(addr.ip)
        self.net = addr.network
        self.upstream = upstream
        self.port = int(os.getenv('GATEWAY_PORT', '8080'))
        self.dhcp = os.getenv('GATEWAY_DHCP', '1').strip().lower() not in ('0', 'false', 'no', 'off')
        self.dns = [d.strip() for d in os.getenv('GATEWAY_DNS', '8.8.8.8,1.1.1.1').split(',') if d.strip()]
        self.free_macs = [m.strip().lower().replace('-', ':')
                          for m in os.getenv('GATEWAY_FREE_MACS', '').split(',') if m.strip()]
        self.api = (os.getenv('GATEWAY_API_URL', '').strip() or 'http://127.0.0.1:5000/api').rstrip('/')
        secret = os.getenv('SECRET_KEY', '')
        if not secret:
            raise RuntimeError('SECRET_KEY must be set (same backend/.env as the billing system)')
        self.key = hmac.new(secret.encode(), b'gateway', hashlib.sha256).hexdigest()[:32]
        self.login_url = f'http://{self.ip}:{self.port}/login'

    def dhcp_ranges(self):
        custom = os.getenv('GATEWAY_DHCP_RANGE', '').strip()
        if custom:
            start, end = [ipaddress.ip_address(x.strip()) for x in custom.split(',')[:2]]
        else:
            hosts = self.net.num_addresses
            start = self.net.network_address + min(50, max(2, hosts // 5))
            end = self.net.network_address + min(250, hosts - 2)
        # Never hand out this machine's own address
        me = ipaddress.ip_address(self.ip)
        if start <= me <= end:
            parts = [(start, me - 1), (me + 1, end)]
        else:
            parts = [(start, end)]
        return [(str(a), str(b)) for a, b in parts if a <= b]


# ---------------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------------

def setup_firewall(cfg):
    for path, value in (('net/ipv4/ip_forward', '1'),
                        ('net/ipv4/conf/all/send_redirects', '0'),
                        (f'net/ipv4/conf/{cfg.iface}/send_redirects', '0')):
        # Without this the kernel tells customers to skip us and use the router directly
        with open(f'/proc/sys/{path}', 'w') as f:
            f.write(value)

    run(['nft', 'delete', 'table', 'inet', TABLE], check=False)
    free = f'elements = {{ {", ".join(cfg.free_macs)} }}' if cfg.free_macs else ''
    rules = f'''
table inet {TABLE} {{
  set allowed {{ type ipv4_addr; flags timeout; }}
  set free_macs {{ type ether_addr; {free} }}

  chain prerouting {{
    type nat hook prerouting priority dstnat; policy accept;
    iifname "{cfg.iface}" ip saddr {cfg.net} ip daddr != {cfg.ip} ip saddr != @allowed ether saddr != @free_macs tcp dport 80 dnat ip to {cfg.ip}:{cfg.port}
  }}

  chain forward {{
    type filter hook forward priority filter; policy accept;
    iifname "{cfg.iface}" ip saddr {cfg.net} jump customers
  }}

  chain customers {{
    ct state established,related accept
    ct status dnat accept
    ip saddr @allowed accept
    ether saddr @free_macs accept
    ip daddr {cfg.net} accept
    udp dport 53 accept
    tcp dport 53 accept
    meta l4proto tcp reject with tcp reset
    reject
  }}

  chain postrouting {{
    type nat hook postrouting priority srcnat; policy accept;
    oifname "{cfg.iface}" ip saddr {cfg.net} ip daddr != {cfg.net} masquerade
  }}
}}
'''
    run(['nft', '-f', '-'], stdin=rules)
    allow_through_docker(cfg)
    log.info('Firewall ready on %s (%s), login page %s', cfg.iface, cfg.net, cfg.login_url)


def allow_through_docker(cfg):
    """Docker sets the FORWARD policy to DROP; let customer traffic reach our own rules."""
    rule = ['DOCKER-USER', '-i', cfg.iface, '-o', cfg.iface, '-j', 'ACCEPT']
    if run(['iptables', '-L', 'DOCKER-USER', '-n'], check=False).returncode != 0:
        return
    if run(['iptables', '-C'] + rule, check=False).returncode != 0:
        run(['iptables', '-I'] + rule, check=False)


def allow(ip, seconds):
    run(['nft', 'add', 'element', 'inet', TABLE, 'allowed', f'{{ {ip} timeout {max(1, int(seconds))}s }}'])


def disallow(ip):
    run(['nft', 'delete', 'element', 'inet', TABLE, 'allowed', f'{{ {ip} }}'], check=False)
    # Close connections that are already open
    run(['conntrack', '-D', '-s', ip], check=False)


# ---------------------------------------------------------------------------
# DHCP
# ---------------------------------------------------------------------------

def start_dhcp(cfg):
    os.makedirs(os.path.dirname(LEASE_FILE), exist_ok=True)
    cmd = ['dnsmasq', '--keep-in-foreground', '--conf-file=/dev/null', '--port=0',
           f'--interface={cfg.iface}', '--bind-dynamic', '--dhcp-authoritative',
           f'--dhcp-leasefile={LEASE_FILE}',
           f'--dhcp-option=option:router,{cfg.ip}',
           f'--dhcp-option=option:dns-server,{",".join(cfg.dns)}',
           # RFC 8910: tells phones where the login page is
           f'--dhcp-option=114,http://{cfg.ip}:{cfg.port}/login']
    for start, end in cfg.dhcp_ranges():
        cmd.append(f'--dhcp-range={start},{end},{cfg.net.netmask},12h')
    log.info('DHCP on %s: %s', cfg.iface, ', '.join(f'{a}-{b}' for a, b in cfg.dhcp_ranges()))

    def keep_running():
        while True:
            proc = subprocess.Popen(cmd)
            proc.wait()
            log.error('dnsmasq stopped (exit %s), restarting in 5s', proc.returncode)
            time.sleep(5)

    threading.Thread(target=keep_running, daemon=True).start()


def mac_of(ip):
    try:
        with open('/proc/net/arp') as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if parts[0] == ip and parts[3] != '00:00:00:00:00:00':
                    return parts[3].lower()
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# Billing system API
# ---------------------------------------------------------------------------

def api(cfg, path, payload):
    req = urllib.request.Request(f'{cfg.api}{path}', data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json', 'X-Gateway-Key': cfg.key})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b'{}')
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError) as e:
        log.warning('Billing system unreachable: %s', e)
        return 0, {'message': 'The billing system is not reachable. Try again in a minute.'}


class Sessions:
    """Who is logged in, by IP address."""

    def __init__(self):
        self.lock = threading.Lock()
        self.by_ip = {}

    def add(self, ip, info):
        with self.lock:
            self.by_ip[ip] = info

    def get(self, ip):
        with self.lock:
            info = self.by_ip.get(ip)
            if info and info['expires'] <= time.time():
                return None
            return info

    def remove_where(self, predicate):
        with self.lock:
            gone = [ip for ip, info in self.by_ip.items() if predicate(ip, info)]
            for ip in gone:
                del self.by_ip[ip]
        return gone

    def snapshot(self):
        with self.lock:
            return dict(self.by_ip)


def heartbeat_loop(cfg, sessions):
    while True:
        time.sleep(HEARTBEAT_EVERY)
        try:
            for ip in sessions.remove_where(lambda ip, s: s['expires'] <= time.time()):
                disallow(ip)
                log.info('Time up for %s', ip)
            current = sessions.snapshot()
            status, data = api(cfg, '/gateway/heartbeat', {
                'sessions': [{'session_id': s['session_id'], 'ip': ip, 'mac': s['mac']}
                             for ip, s in current.items()]})
            if status == 200:
                cut = set(data.get('cut') or [])
                for ip in sessions.remove_where(lambda ip, s: s['session_id'] in cut):
                    disallow(ip)
                    log.info('Disconnected %s (voucher no longer valid)', ip)
            allow_through_docker(cfg)
        except Exception:
            log.exception('Heartbeat failed')


# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------

def render_login(cfg, error='', dst=''):
    with open(TEMPLATE, encoding='utf-8') as f:
        page = f.read()
    # The template is written for MikroTik; fill in its variables the same way
    page = re.sub(r'\$\(if chap-id\)(.*?)(?:\$\(else\)(.*?))?\$\(endif\)',
                  lambda m: m.group(2) or '', page, flags=re.S)
    page = re.sub(r'\$\(if error\)(.*?)\$\(endif\)', lambda m: m.group(1) if error else '', page, flags=re.S)
    support = os.getenv('SUPPORT_CONTACT', '').strip()
    portal = os.getenv('PORTAL_URL', '').strip().rstrip('/') or f'http://{cfg.ip}:5000'
    public_api = os.getenv('PUBLIC_API_URL', '').strip().rstrip('/') or f'{portal}/api'
    for key, value in {
        '$(error)': html.escape(error),
        '$(link-login-only)': '/login',
        '$(link-orig)': html.escape(dst, quote=True),
        '{{BUSINESS_NAME}}': html.escape(os.getenv('BUSINESS_NAME', '').strip() or 'WiFi Hotspot'),
        '{{PORTAL_URL}}': html.escape(portal, quote=True),
        '{{API_URL}}': public_api,
        '{{SUPPORT_CONTACT}}': f'Help: {html.escape(support)}' if support else '',
    }.items():
        page = page.replace(key, value)
    return page


STATUS_PAGE = '''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Connected</title>
<style>body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0F172A;color:#E2E8F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:16px}}
.card{{max-width:380px;width:100%;background:#1E293B;border:1px solid #334155;border-radius:16px;padding:28px 24px;text-align:center}}
h1{{font-size:22px;margin:0 0 8px;color:#F8FAFC}}p{{color:#94A3B8;margin:6px 0}}b{{color:#F8FAFC}}
a{{display:block;margin-top:16px;padding:13px;border-radius:10px;background:#10B981;color:#fff;text-decoration:none;font-weight:600}}</style>
</head><body><div class="card"><h1>You're connected</h1><p>Plan: <b>{plan}</b></p>
<p>Time left: <b>{left}</b></p><a href="{dst}">Continue</a></div></body></html>'''


def format_left(seconds):
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f'{days} d {hours} h'
    if hours:
        return f'{hours} h {minutes} min'
    return f'{minutes} min'


def make_handler(cfg, sessions):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'WiFiBilling'

        def log_message(self, fmt, *args):
            pass

        def send_page(self, body, status=200):
            data = body.encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def redirect(self, url):
            self.send_response(302)
            self.send_header('Location', url)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def show_status(self, info, dst):
            self.send_page(STATUS_PAGE.format(plan=html.escape(info['plan']),
                                              left=format_left(info['expires'] - time.time()),
                                              dst=html.escape(dst or 'http://www.google.com', quote=True)))

        def do_GET(self):
            host = (self.headers.get('Host') or '').split(':')[0]
            client = self.client_address[0]
            url = urllib.parse.urlparse(self.path)
            if host not in (cfg.ip, '') or url.path not in ('/login', '/status'):
                # A page the customer tried to open: send them to the login page.
                # Keep where they wanted to go, unless it was a phone's connectivity check.
                orig = f'http://{host}{self.path}' if host and host != cfg.ip and host not in CHECK_HOSTS else ''
                self.redirect(cfg.login_url + ('?dst=' + urllib.parse.quote(orig, safe='') if orig else ''))
                return
            dst = urllib.parse.parse_qs(url.query).get('dst', [''])[0]
            info = sessions.get(client)
            if info:
                self.show_status(info, dst)
            else:
                self.send_page(render_login(cfg, dst=dst))

        def do_POST(self):
            client = self.client_address[0]
            length = min(int(self.headers.get('Content-Length') or 0), 10000)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode('utf-8', 'replace'))
            code = re.sub(r'\s+', '', (form.get('username') or [''])[0]).upper()
            dst = (form.get('dst') or [''])[0]
            if not dst.startswith('http'):
                dst = ''
            if not code:
                self.send_page(render_login(cfg, 'Enter your voucher code', dst))
                return
            if ipaddress.ip_address(client) not in cfg.net:
                self.send_page(render_login(cfg, 'Connect to the WiFi first', dst))
                return
            mac = mac_of(client)
            status, data = api(cfg, '/gateway/login', {'code': code, 'mac': mac, 'ip': client})
            if status != 200 or not data.get('ok'):
                self.send_page(render_login(cfg, data.get('message') or 'That code did not work', dst))
                return
            info = {'session_id': data['session_id'], 'code': code, 'mac': mac, 'plan': data.get('plan', ''),
                    'expires': time.time() + data['seconds']}
            allow(client, data['seconds'])
            sessions.add(client, info)
            log.info('Login %s from %s (%s), %ss', code, client, mac, data['seconds'])
            if dst:
                self.redirect(dst)
            else:
                self.show_status(info, dst)

    return Handler


def main():
    cfg = Config()
    setup_firewall(cfg)
    if cfg.dhcp:
        start_dhcp(cfg)
    sessions = Sessions()
    threading.Thread(target=heartbeat_loop, args=(cfg, sessions), daemon=True).start()
    server = ThreadingHTTPServer(('0.0.0.0', cfg.port), make_handler(cfg, sessions))
    log.info('Gateway running. Customers get addresses from this machine (%s); router upstream %s',
             cfg.ip, cfg.upstream)
    server.serve_forever()


if __name__ == '__main__':
    main()
