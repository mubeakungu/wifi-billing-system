"""
Built-in RADIUS server (RFC 2865 / 2866) for the WiFi billing system.

Any router, access point controller or firewall with a RADIUS-capable hotspot
or captive portal (MikroTik, UniFi, TP-Link Omada, pfSense, OPNsense, OpenWrt
with CoovaChilli, FortiGate, Cisco, Ruckus, ...) can send customer logins here.

  * Access-Request: the username (and password) is the voucher code. Unused
    codes start their time on first login. The reply carries Session-Timeout
    (time left) and the plan's speed in the attributes each vendor understands.
  * Accounting: tracks who is online, data used, and keeps a code on one
    device at a time.

Routers are added in the dashboard (Routers tab) with their IP and a shared
secret; requests from anywhere else are ignored.

Run it on its own:      python radius_server.py
or with the web server: RADIUS_ENABLED=1 python billing_system_backend.py
"""

import hashlib
import hmac
import ipaddress
import logging
import os
import selectors
import socket
import struct
import time
from datetime import datetime, timedelta

log = logging.getLogger('radius')

# Packet codes
ACCESS_REQUEST, ACCESS_ACCEPT, ACCESS_REJECT = 1, 2, 3
ACCOUNTING_REQUEST, ACCOUNTING_RESPONSE = 4, 5

# Attribute types
USER_NAME, USER_PASSWORD, CHAP_PASSWORD, NAS_IP_ADDRESS = 1, 2, 3, 4
FRAMED_IP_ADDRESS, REPLY_MESSAGE, VENDOR_SPECIFIC = 8, 18, 26
SESSION_TIMEOUT, CALLING_STATION_ID, NAS_IDENTIFIER = 27, 31, 32
ACCT_STATUS_TYPE, ACCT_INPUT_OCTETS, ACCT_OUTPUT_OCTETS = 40, 42, 43
ACCT_SESSION_ID, ACCT_TERMINATE_CAUSE = 44, 49
ACCT_INPUT_GIGAWORDS, ACCT_OUTPUT_GIGAWORDS = 52, 53
CHAP_CHALLENGE, MESSAGE_AUTHENTICATOR, ACCT_INTERIM_INTERVAL = 60, 80, 85

ACCT_START, ACCT_STOP, ACCT_INTERIM, ACCT_ON, ACCT_OFF = 1, 2, 3, 7, 8
TERMINATE_CAUSES = {1: 'User-Request', 2: 'Lost-Carrier', 3: 'Lost-Service', 4: 'Idle-Timeout',
                    5: 'Session-Timeout', 6: 'Admin-Reset', 7: 'Admin-Reboot', 9: 'NAS-Error',
                    10: 'NAS-Request', 11: 'NAS-Reboot'}

# Vendors for speed limits
VENDOR_MIKROTIK, MIKROTIK_RATE_LIMIT = 14988, 8
VENDOR_WISPR, WISPR_BW_MAX_UP, WISPR_BW_MAX_DOWN = 14122, 7, 8
VENDOR_CHILLISPOT, CHILLI_BW_MAX_UP, CHILLI_BW_MAX_DOWN = 14559, 4, 5

INTERIM_INTERVAL = int(os.getenv('RADIUS_INTERIM_INTERVAL', '300'))
# A session with no accounting update for this long is treated as gone
STALE_AFTER = timedelta(seconds=INTERIM_INTERVAL * 2 + 60)


# ---------------------------------------------------------------------------
# Packet encoding
# ---------------------------------------------------------------------------

class Packet:
    def __init__(self, code, ident, authenticator, attrs):
        self.code, self.id, self.authenticator, self.attrs = code, ident, authenticator, attrs

    @classmethod
    def parse(cls, data):
        if len(data) < 20:
            raise ValueError('packet too short')
        code, ident, length = struct.unpack('!BBH', data[:4])
        if length < 20 or length > len(data):
            raise ValueError('bad length')
        attrs, pos = [], 20
        while pos < length:
            if pos + 2 > length:
                raise ValueError('truncated attribute')
            atype, alen = data[pos], data[pos + 1]
            if alen < 2 or pos + alen > length:
                raise ValueError('bad attribute length')
            attrs.append((atype, data[pos + 2:pos + alen]))
            pos += alen
        return cls(code, ident, data[4:20], attrs), data[:length]

    def get(self, atype):
        for t, v in self.attrs:
            if t == atype:
                return v
        return None

    def get_str(self, atype):
        v = self.get(atype)
        return v.decode('utf-8', 'replace') if v is not None else None

    def get_int(self, atype):
        v = self.get(atype)
        return struct.unpack('!I', v)[0] if v is not None and len(v) == 4 else None


def encode_attrs(attrs):
    out = b''
    for atype, value in attrs:
        if len(value) > 253:
            value = value[:253]
        out += struct.pack('!BB', atype, len(value) + 2) + value
    return out


def attr_int(atype, n):
    return (atype, struct.pack('!I', max(0, min(int(n), 0xFFFFFFFF))))


def attr_str(atype, s):
    return (atype, s.encode('utf-8'))


def vsa(vendor, vtype, value):
    if isinstance(value, int):
        value = struct.pack('!I', max(0, min(value, 0xFFFFFFFF)))
    elif isinstance(value, str):
        value = value.encode('utf-8')
    return (VENDOR_SPECIFIC, struct.pack('!IBB', vendor, vtype, len(value) + 2) + value)


def build_reply(code, request, secret, attrs):
    """Reply with a Message-Authenticator first (RFC 3579 / BlastRADIUS hardening)."""
    secret = secret.encode()
    with_ma = code in (ACCESS_ACCEPT, ACCESS_REJECT)
    body_attrs = ([(MESSAGE_AUTHENTICATOR, b'\x00' * 16)] if with_ma else []) + list(attrs)
    body = encode_attrs(body_attrs)
    length = 20 + len(body)
    header = struct.pack('!BBH', code, request.id, length)
    if with_ma:
        mac = hmac.new(secret, header + request.authenticator + body, hashlib.md5).digest()
        body = struct.pack('!BB', MESSAGE_AUTHENTICATOR, 18) + mac + body[18:]
    resp_auth = hashlib.md5(header + request.authenticator + body + secret).digest()
    return header + resp_auth + body


def decrypt_pap(encrypted, secret, authenticator):
    secret = secret.encode()
    if not encrypted or len(encrypted) % 16:
        return None
    out, last = b'', authenticator
    for i in range(0, len(encrypted), 16):
        block = encrypted[i:i + 16]
        key = hashlib.md5(secret + last).digest()
        out += bytes(a ^ b for a, b in zip(block, key))
        last = block
    return out.rstrip(b'\x00').decode('utf-8', 'replace')


def check_chap(packet, candidates):
    chap = packet.get(CHAP_PASSWORD)
    if not chap or len(chap) != 17:
        return False
    challenge = packet.get(CHAP_CHALLENGE) or packet.authenticator
    for pw in candidates:
        if hashlib.md5(chap[:1] + pw.encode() + challenge).digest() == chap[1:]:
            return True
    return False


def valid_message_authenticator(raw, packet, secret):
    """True if absent or correct (Access-Request)."""
    ma = packet.get(MESSAGE_AUTHENTICATOR)
    if ma is None:
        return True
    pos = 20
    while pos < len(raw):
        if raw[pos] == MESSAGE_AUTHENTICATOR:
            zeroed = raw[:pos + 2] + b'\x00' * 16 + raw[pos + 18:]
            expected = hmac.new(secret.encode(), zeroed, hashlib.md5).digest()
            return hmac.compare_digest(expected, ma)
        pos += raw[pos + 1]
    return False


def valid_accounting_authenticator(raw, secret):
    expected = hashlib.md5(raw[:4] + b'\x00' * 16 + raw[20:] + secret.encode()).digest()
    return hmac.compare_digest(expected, raw[4:20])


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

def speed_attributes(vendor, mbps):
    bps = int(mbps) * 1000 * 1000
    if vendor == 'mikrotik':
        # rx/tx from the router's side = upload/download for the customer
        return [vsa(VENDOR_MIKROTIK, MIKROTIK_RATE_LIMIT, f'{int(mbps)}M/{int(mbps)}M')]
    attrs = [vsa(VENDOR_WISPR, WISPR_BW_MAX_UP, bps), vsa(VENDOR_WISPR, WISPR_BW_MAX_DOWN, bps)]
    if vendor in ('openwrt', 'other'):
        attrs += [vsa(VENDOR_CHILLISPOT, CHILLI_BW_MAX_UP, bps // 1000),
                  vsa(VENDOR_CHILLISPOT, CHILLI_BW_MAX_DOWN, bps // 1000)]
    return attrs


class RadiusServer:
    def __init__(self, app, host='0.0.0.0', auth_port=1812, acct_port=1813):
        self.app = app
        self.host, self.auth_port, self.acct_port = host, auth_port, acct_port
        self._clients, self._clients_loaded = [], 0.0
        self._last_heartbeat = 0.0
        self._last_seen_write = {}

    # -- clients ------------------------------------------------------------

    def _load_clients(self):
        from billing_system_backend import Router
        clients = []
        # The built-in gateway logs customers in over HTTP, not RADIUS
        for r in Router.query.filter(Router.is_active.is_(True), Router.vendor != 'gateway').all():
            try:
                net = ipaddress.ip_network(r.address.strip(), strict=False)
            except ValueError:
                log.warning('Router %s has an invalid address %r', r.name, r.address)
                continue
            clients.append((net, r.id, r.secret, r.vendor, r.name))
        # Most specific range wins
        clients.sort(key=lambda c: c[0].prefixlen, reverse=True)
        self._clients, self._clients_loaded = clients, time.time()

    def find_client(self, ip):
        if time.time() - self._clients_loaded > 15:
            self._load_clients()
        addr = ipaddress.ip_address(ip)
        for client in self._clients:
            if addr in client[0]:
                return client
        return None

    def _touch_router(self, router_id, ip):
        now = time.time()
        if now - self._last_seen_write.get(router_id, 0) < 30:
            return
        self._last_seen_write[router_id] = now
        from billing_system_backend import Router, db
        r = db.session.get(Router, router_id)
        if r:
            r.last_seen_at = datetime.utcnow()
            r.last_seen_ip = ip
            db.session.commit()

    # -- handlers -----------------------------------------------------------

    def handle_auth(self, packet, raw, client, ip):
        from billing_system_backend import check_voucher_login
        _, router_id, secret, vendor, router_name = client

        if not valid_message_authenticator(raw, packet, secret):
            log.warning('Bad Message-Authenticator from %s (%s): check the shared secret', ip, router_name)
            return None
        username = (packet.get_str(USER_NAME) or '').strip()
        code = username.upper()
        mac = packet.get_str(CALLING_STATION_ID)

        def reject(message):
            log.info('Reject %s from %s (%s): %s', username, router_name, mac, message)
            return build_reply(ACCESS_REJECT, packet, secret, [attr_str(REPLY_MESSAGE, message)])

        candidates = [code, code.lower(), username]
        if packet.get(USER_PASSWORD) is not None:
            password = decrypt_pap(packet.get(USER_PASSWORD), secret, packet.authenticator)
            if password is None:
                return reject('Invalid request')
            # Accept the code as password; an empty password is allowed for
            # portals that only have a single "voucher code" field
            if password.strip().upper() not in (code, ''):
                return reject('Wrong voucher code or password')
        elif packet.get(CHAP_PASSWORD) is not None:
            if not check_chap(packet, candidates):
                return reject('Wrong voucher code or password')
        else:
            return reject('No password supplied')

        voucher, remaining, reason = check_voucher_login(code, mac, STALE_AFTER)
        if not voucher:
            return reject(reason)
        attrs = [attr_int(SESSION_TIMEOUT, remaining),
                 attr_int(ACCT_INTERIM_INTERVAL, INTERIM_INTERVAL),
                 attr_str(REPLY_MESSAGE, f'Welcome! {voucher.voucher_type.name} plan')]
        attrs += speed_attributes(vendor, voucher.voucher_type.speed_mbps or 0) \
            if voucher.voucher_type.speed_mbps else []
        log.info('Accept %s from %s (%s), %ss left', code, router_name, mac, remaining)
        return build_reply(ACCESS_ACCEPT, packet, secret, attrs)

    def handle_acct(self, packet, raw, client, ip):
        from billing_system_backend import Voucher, RadiusSession, db, utcnow
        _, router_id, secret, vendor, router_name = client
        if not valid_accounting_authenticator(raw, secret):
            log.warning('Bad accounting authenticator from %s (%s): check the shared secret', ip, router_name)
            return None

        status = packet.get_int(ACCT_STATUS_TYPE)
        now = utcnow()
        if status in (ACCT_ON, ACCT_OFF):
            # Router restarted: everything it had is gone
            (RadiusSession.query.filter_by(router_id=router_id, stopped_at=None)
             .update({'stopped_at': now, 'terminate_cause': 'NAS-Reboot'}, synchronize_session=False))
            db.session.commit()
        elif status in (ACCT_START, ACCT_STOP, ACCT_INTERIM):
            username = (packet.get_str(USER_NAME) or '').strip()
            session_id = packet.get_str(ACCT_SESSION_ID) or ''
            sess = (RadiusSession.query
                    .filter_by(router_id=router_id, acct_session_id=session_id, username=username.upper())
                    .order_by(RadiusSession.id.desc()).first())
            if sess is None:
                voucher = Voucher.query.filter_by(code=username.upper()).first()
                if voucher and voucher.status == 'unused':
                    voucher.activate()
                sess = RadiusSession(router_id=router_id, acct_session_id=session_id,
                                     username=username.upper(), voucher_id=voucher.id if voucher else None,
                                     started_at=now)
                db.session.add(sess)
            sess.mac = packet.get_str(CALLING_STATION_ID) or sess.mac
            framed = packet.get(FRAMED_IP_ADDRESS)
            if framed and len(framed) == 4:
                sess.ip = socket.inet_ntoa(framed)
            gi = packet.get_int(ACCT_INPUT_GIGAWORDS) or 0
            go = packet.get_int(ACCT_OUTPUT_GIGAWORDS) or 0
            if packet.get_int(ACCT_INPUT_OCTETS) is not None:
                sess.input_octets = (gi << 32) + packet.get_int(ACCT_INPUT_OCTETS)
            if packet.get_int(ACCT_OUTPUT_OCTETS) is not None:
                sess.output_octets = (go << 32) + packet.get_int(ACCT_OUTPUT_OCTETS)
            sess.updated_at = now
            if status == ACCT_STOP:
                sess.stopped_at = now
                cause = packet.get_int(ACCT_TERMINATE_CAUSE)
                sess.terminate_cause = TERMINATE_CAUSES.get(cause, str(cause) if cause else None)
            db.session.commit()
        return build_reply(ACCOUNTING_RESPONSE, packet, secret, [])

    # -- loop ---------------------------------------------------------------

    def handle(self, data, addr, kind):
        ip = addr[0]
        with self.app.app_context():
            from billing_system_backend import db, set_setting
            try:
                client = self.find_client(ip)
                if client is None:
                    log.warning('Ignored RADIUS request from unknown router %s', ip)
                    set_setting('radius_unknown_nas', ip)
                    return None
                packet, raw = Packet.parse(data)
                self._touch_router(client[1], ip)
                if kind == 'auth' and packet.code == ACCESS_REQUEST:
                    return self.handle_auth(packet, raw, client, ip)
                if kind == 'acct' and packet.code == ACCOUNTING_REQUEST:
                    return self.handle_acct(packet, raw, client, ip)
                return None
            except Exception:
                log.exception('Error handling RADIUS packet from %s', ip)
                db.session.rollback()
                return None
            finally:
                db.session.remove()

    def heartbeat(self):
        if time.time() - self._last_heartbeat < 30:
            return
        self._last_heartbeat = time.time()
        with self.app.app_context():
            from billing_system_backend import RadiusSession, db, set_setting, utcnow
            try:
                (RadiusSession.query
                 .filter(RadiusSession.stopped_at.is_(None),
                         RadiusSession.updated_at < utcnow() - STALE_AFTER * 3)
                 .update({'stopped_at': utcnow(), 'terminate_cause': 'Stale'},
                         synchronize_session=False))
                db.session.commit()
                set_setting('radius_heartbeat', f'{self.auth_port}/{self.acct_port}')
            except Exception:
                log.exception('RADIUS heartbeat failed')
                db.session.rollback()
            finally:
                db.session.remove()

    def serve_forever(self):
        sel = selectors.DefaultSelector()
        socks = []
        for port, kind in ((self.auth_port, 'auth'), (self.acct_port, 'acct')):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, port))
            sel.register(sock, selectors.EVENT_READ, kind)
            socks.append(sock)
        log.info('RADIUS listening on %s udp/%s (auth) and udp/%s (accounting)',
                 self.host, self.auth_port, self.acct_port)
        self.heartbeat()
        while True:
            for key, _ in sel.select(timeout=5):
                try:
                    data, addr = key.fileobj.recvfrom(4096)
                except OSError:
                    continue
                reply = self.handle(data, addr, key.data)
                if reply:
                    key.fileobj.sendto(reply, addr)
            self.heartbeat()


def run(app):
    server = RadiusServer(app, host=os.getenv('RADIUS_HOST', '0.0.0.0'),
                          auth_port=int(os.getenv('RADIUS_AUTH_PORT', '1812')),
                          acct_port=int(os.getenv('RADIUS_ACCT_PORT', '1813')))
    server.serve_forever()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    from billing_system_backend import app
    run(app)
