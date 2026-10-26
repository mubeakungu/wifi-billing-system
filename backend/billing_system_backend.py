"""
WiFi Billing System - Flask Backend

Customers buy WiFi vouchers with M-Pesa (Daraja STK Push). Admin, finance and
viewer users sign in with JWT to see revenue analytics, customers,
transactions and vouchers.

Runs out of the box for local development:
  * SQLite is used when DATABASE_URL is not set.
  * M-Pesa runs in mock mode when the Daraja credentials are not set: an STK
    push is simulated and the payment completes after MPESA_MOCK_DELAY seconds.
"""

import base64
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import threading
import uuid
from urllib.parse import urlparse
from datetime import datetime, timedelta
from functools import wraps

import jwt
import requests
from dotenv import load_dotenv
from flask import Flask, Response, g, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

import mikrotik

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

IS_PRODUCTION = os.getenv('FLASK_ENV') == 'production'


def _database_url():
    url = os.getenv('DATABASE_URL', '').strip().strip('"\'').strip()
    if not url:
        return 'sqlite:///' + os.path.join(BASE_DIR, 'wifi_billing.db')
    if '://' not in url:
        raise RuntimeError('DATABASE_URL must be a URL like postgresql://user:password@host/dbname '
                           '(on Render: the database\'s Internal Database URL, not the PSQL command)')
    # Render and Heroku hand out postgres:// URLs, which SQLAlchemy rejects
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    # Pin the driver from requirements.txt (SQLAlchemy 2.1+ defaults to psycopg 3)
    if url.startswith('postgresql://'):
        url = 'postgresql+psycopg2://' + url[len('postgresql://'):]
    return url


SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise RuntimeError('SECRET_KEY must be set when FLASK_ENV=production')
    SECRET_KEY = 'dev-only-insecure-secret-key-change-me-0000'

JWT_EXPIRY_HOURS = int(os.getenv('JWT_EXPIRY_HOURS', '12'))
# Daily analytics are grouped in local time (Kenya is UTC+3)
TZ_OFFSET = timedelta(hours=float(os.getenv('TIMEZONE_OFFSET_HOURS', '3')))

MPESA_CONSUMER_KEY = os.getenv('MPESA_CONSUMER_KEY', '').strip()
MPESA_CONSUMER_SECRET = os.getenv('MPESA_CONSUMER_SECRET', '').strip()
MPESA_SHORTCODE = os.getenv('MPESA_SHORTCODE', '174379').strip()
MPESA_PASSKEY = os.getenv('MPESA_PASSKEY', '').strip()
MPESA_CALLBACK_URL = os.getenv('MPESA_CALLBACK_URL', '').strip()
MPESA_CALLBACK_TOKEN = os.getenv('MPESA_CALLBACK_TOKEN', '').strip()
MPESA_ENV = os.getenv('MPESA_ENV', 'sandbox').strip().lower()
MPESA_TRANSACTION_TYPE = os.getenv('MPESA_TRANSACTION_TYPE', 'CustomerPayBillOnline')
MPESA_MOCK_DELAY = int(os.getenv('MPESA_MOCK_DELAY', '8'))
# SMS (Africa's Talking). Without credentials messages are only logged.
AT_USERNAME = os.getenv('AT_USERNAME', '').strip()
AT_API_KEY = os.getenv('AT_API_KEY', '').strip()
AT_SENDER_ID = os.getenv('AT_SENDER_ID', '').strip()
SMS_MODE = 'africastalking' if AT_USERNAME and AT_API_KEY and not AT_API_KEY.startswith('your-') else 'log'
AT_SMS_URL = ('https://api.sandbox.africastalking.com/version1/messaging' if AT_USERNAME == 'sandbox'
              else 'https://api.africastalking.com/version1/messaging')
SUPPORT_CONTACT = os.getenv('SUPPORT_CONTACT', '').strip()

# Branding and public URLs
BUSINESS_NAME = os.getenv('BUSINESS_NAME', '').strip() or 'WiFi Hotspot'
PORTAL_URL = os.getenv('PORTAL_URL', '').strip().rstrip('/')          # where customers buy
PUBLIC_API_URL = os.getenv('PUBLIC_API_URL', '').strip().rstrip('/')  # e.g. https://api.example.com/api
HOTSPOT_LOGIN_URL = os.getenv('HOTSPOT_LOGIN_URL', '').strip()        # e.g. http://wifi.login

# Router (MikroTik hotspot). off | pull | api, see mikrotik.py
# Most routers connect through the built-in RADIUS server (Routers tab). These
# extra MikroTik-only modes sync hotspot users directly, for when RADIUS (UDP)
# can't reach the server, e.g. a cloud host like Render.
ROUTER_MODE = os.getenv('ROUTER_MODE', 'off').strip().lower()
if ROUTER_MODE not in ('off', 'pull', 'api'):
    ROUTER_MODE = 'off'
RADIUS_ENABLED = os.getenv('RADIUS_ENABLED', '1').strip().lower() not in ('0', 'false', 'no', 'off')
# Address routers use to reach the RADIUS server (shown in setup guides)
RADIUS_PUBLIC_HOST = os.getenv('RADIUS_PUBLIC_HOST', '').strip()
ROUTER_SYNC_INTERVAL = max(10, int(os.getenv('ROUTER_SYNC_INTERVAL', '20')))
# Key the router uses to download its sync script. Derived from SECRET_KEY if unset.
ROUTER_SYNC_KEY = os.getenv('ROUTER_SYNC_KEY', '').strip()
MIKROTIK_HOST = os.getenv('MIKROTIK_HOST', '').strip()
MIKROTIK_PORT = int(os.getenv('MIKROTIK_PORT', '8728'))
MIKROTIK_USER = os.getenv('MIKROTIK_USER', '').strip()
MIKROTIK_PASSWORD = os.getenv('MIKROTIK_PASSWORD', '')
MPESA_BASE_URL = ('https://api.safaricom.co.ke' if MPESA_ENV == 'production'
                  else 'https://sandbox.safaricom.co.ke')

_placeholder = lambda v: not v or v.startswith('your-')
_mode = os.getenv('MPESA_MODE', '').strip().lower()
if _mode not in ('mock', 'live'):
    _mode = 'mock' if any(_placeholder(v) for v in
                          (MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET, MPESA_PASSKEY)) else 'live'
# Never hand out free WiFi by accident: mock payments in production only if asked for
if IS_PRODUCTION and _mode == 'mock' and os.getenv('MPESA_MODE', '').strip().lower() != 'mock':
    _mode = 'disabled'
MPESA_MODE = _mode

app = Flask(__name__, static_folder=None)  # frontend files are served by frontend() below
app.config['SQLALCHEMY_DATABASE_URI'] = _database_url()
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}
app.config['SECRET_KEY'] = SECRET_KEY
if not ROUTER_SYNC_KEY:
    ROUTER_SYNC_KEY = hmac.new(SECRET_KEY.encode(), b'router-sync', hashlib.sha256).hexdigest()[:32]

CORS(app, resources={
    # Called from the router's hotspot login page, whose origin is the router itself
    r'/api/vouchers/validate': {'origins': '*'},
    r'/api/*': {'origins': os.getenv('CORS_ORIGINS', '*').split(',')},
})
db = SQLAlchemy(app)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
app.logger.setLevel(logging.INFO)


def utcnow():
    return datetime.utcnow()


def iso(dt):
    return dt.isoformat() + 'Z' if dt else None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Customer(db.Model):
    __tablename__ = 'customers'
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(15), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    account_balance = db.Column(db.Float, default=0.0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    transactions = db.relationship('Transaction', backref='customer', lazy='dynamic')
    vouchers = db.relationship('Voucher', backref='customer', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'phone': self.phone,
            'name': self.name,
            'email': self.email,
            'account_balance': self.account_balance,
            'is_active': self.is_active,
            'created_at': iso(self.created_at),
        }


class VoucherType(db.Model):
    __tablename__ = 'voucher_types'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    duration_hours = db.Column(db.Integer, nullable=False)
    price_ksh = db.Column(db.Float, nullable=False)
    speed_mbps = db.Column(db.Integer, default=10)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'duration_hours': self.duration_hours,
            'price_ksh': self.price_ksh,
            'speed_mbps': self.speed_mbps,
            'is_active': self.is_active,
        }


class Voucher(db.Model):
    __tablename__ = 'vouchers'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    voucher_type_id = db.Column(db.Integer, db.ForeignKey('voucher_types.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'))
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id'))
    # unused: printed/bulk code not yet used; active: running; expired
    status = db.Column(db.String(20), default='unused', nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    activated_at = db.Column(db.DateTime)
    expires_at = db.Column(db.DateTime, index=True)

    voucher_type = db.relationship('VoucherType')

    def activate(self):
        now = utcnow()
        self.status = 'active'
        self.activated_at = now
        self.expires_at = now + timedelta(hours=self.voucher_type.duration_hours)

    def refresh_status(self):
        if self.status == 'active' and self.expires_at and self.expires_at <= utcnow():
            self.status = 'expired'
        return self.status

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'voucher_type_id': self.voucher_type_id,
            'voucher_type': self.voucher_type.name if self.voucher_type else None,
            'duration_hours': self.voucher_type.duration_hours if self.voucher_type else None,
            'speed_mbps': self.voucher_type.speed_mbps if self.voucher_type else None,
            'customer_id': self.customer_id,
            'phone': self.customer.phone if self.customer else None,
            'transaction_id': self.transaction_id,
            'status': self.status,
            'created_at': iso(self.created_at),
            'activated_at': iso(self.activated_at),
            'expires_at': iso(self.expires_at),
        }


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    # Unguessable id handed to the public portal for status polling
    reference = db.Column(db.String(32), unique=True, nullable=False, index=True,
                          default=lambda: uuid.uuid4().hex)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    voucher_type_id = db.Column(db.Integer, db.ForeignKey('voucher_types.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    phone = db.Column(db.String(15), nullable=False)
    # pending, completed, failed
    status = db.Column(db.String(20), default='pending', nullable=False, index=True)
    payment_method = db.Column(db.String(20), default='mpesa', nullable=False)
    merchant_request_id = db.Column(db.String(100))
    checkout_request_id = db.Column(db.String(100), unique=True, index=True)
    mpesa_receipt = db.Column(db.String(50))
    result_code = db.Column(db.String(20))
    result_desc = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    completed_at = db.Column(db.DateTime, index=True)
    last_query_at = db.Column(db.DateTime)

    voucher_type = db.relationship('VoucherType')
    voucher = db.relationship('Voucher', uselist=False, backref='transaction')

    def to_dict(self):
        return {
            'id': self.id,
            'reference': self.reference,
            'customer_id': self.customer_id,
            'phone': self.phone,
            'voucher_type_id': self.voucher_type_id,
            'voucher_type': self.voucher_type.name if self.voucher_type else None,
            'amount': self.amount,
            'status': self.status,
            'payment_method': self.payment_method,
            'mpesa_receipt': self.mpesa_receipt,
            'result_desc': self.result_desc,
            'voucher_code': self.voucher.code if self.voucher else None,
            'created_at': iso(self.created_at),
            'completed_at': iso(self.completed_at),
        }


class Admin(db.Model):
    __tablename__ = 'admins'
    ROLES = ('admin', 'finance', 'viewer')

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='viewer', nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_login = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'role': self.role,
            'is_active': self.is_active,
            'last_login': iso(self.last_login),
        }


class Router(db.Model):
    """A router, access point controller or firewall that asks us (RADIUS) to log customers in."""
    __tablename__ = 'routers'
    VENDORS = ('mikrotik', 'unifi', 'omada', 'pfsense', 'opnsense', 'openwrt', 'fortigate',
               'cisco', 'ruckus', 'other')

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    vendor = db.Column(db.String(20), default='other', nullable=False)
    # IP address or CIDR range the RADIUS requests come from
    address = db.Column(db.String(64), nullable=False)
    secret = db.Column(db.String(128), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_seen_at = db.Column(db.DateTime)
    last_seen_ip = db.Column(db.String(64))

    def to_dict(self, include_secret=False):
        d = {
            'id': self.id,
            'name': self.name,
            'vendor': self.vendor,
            'address': self.address,
            'is_active': self.is_active,
            'created_at': iso(self.created_at),
            'last_seen_at': iso(self.last_seen_at),
            'last_seen_ip': self.last_seen_ip,
            'online_sessions': RadiusSession.query.filter_by(router_id=self.id, stopped_at=None).count(),
        }
        if include_secret:
            d['secret'] = self.secret
        return d


class RadiusSession(db.Model):
    """A customer's connection, from RADIUS accounting."""
    __tablename__ = 'radius_sessions'
    id = db.Column(db.Integer, primary_key=True)
    voucher_id = db.Column(db.Integer, db.ForeignKey('vouchers.id'), index=True)
    router_id = db.Column(db.Integer, db.ForeignKey('routers.id'), index=True)
    username = db.Column(db.String(64), nullable=False)
    acct_session_id = db.Column(db.String(128), nullable=False, index=True)
    mac = db.Column(db.String(64))
    ip = db.Column(db.String(64))
    started_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    stopped_at = db.Column(db.DateTime, index=True)
    input_octets = db.Column(db.BigInteger, default=0)
    output_octets = db.Column(db.BigInteger, default=0)
    terminate_cause = db.Column(db.String(64))

    voucher = db.relationship('Voucher')
    router = db.relationship('Router')

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.username,
            'voucher_type': self.voucher.voucher_type.name if self.voucher else None,
            'router': self.router.name if self.router else None,
            'mac': self.mac,
            'ip': self.ip,
            'started_at': iso(self.started_at),
            'updated_at': iso(self.updated_at),
            'stopped_at': iso(self.stopped_at),
            'download_mb': round((self.output_octets or 0) / 1048576, 1),
            'upload_mb': round((self.input_octets or 0) / 1048576, 1),
            'terminate_cause': self.terminate_cause,
        }


class Setting(db.Model):
    """Small key/value store, e.g. when the router last synced."""
    __tablename__ = 'settings'
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


def get_setting(key):
    row = db.session.get(Setting, key)
    return (row.value, row.updated_at) if row else (None, None)


def set_setting(key, value):
    row = db.session.get(Setting, key) or Setting(key=key)
    row.value = value
    row.updated_at = utcnow()
    db.session.add(row)
    db.session.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def error(message, status=400):
    return jsonify({'error': message}), status


def normalize_phone(raw):
    """Return a phone as 2547XXXXXXXX / 2541XXXXXXXX, or None if invalid."""
    digits = ''.join(ch for ch in str(raw or '') if ch.isdigit())
    if digits.startswith('0') and len(digits) == 10:
        digits = '254' + digits[1:]
    elif len(digits) == 9 and digits[0] in '17':
        digits = '254' + digits
    if len(digits) == 12 and digits.startswith('254') and digits[3] in '17':
        return digits
    return None


_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no 0/O/1/I


def generate_voucher_code(length=8):
    while True:
        code = ''.join(secrets.choice(_CODE_ALPHABET) for _ in range(length))
        if not Voucher.query.filter_by(code=code).first():
            return code


def complete_transaction(tx, receipt=None, desc='Payment completed'):
    """Mark a transaction paid and issue its voucher. Idempotent."""
    if tx.status == 'completed':
        return tx.voucher
    tx.status = 'completed'
    tx.result_code = '0'
    tx.result_desc = desc
    tx.mpesa_receipt = receipt or tx.mpesa_receipt
    tx.completed_at = utcnow()
    voucher = Voucher(code=generate_voucher_code(), voucher_type_id=tx.voucher_type_id,
                      customer_id=tx.customer_id, transaction_id=tx.id)
    voucher.voucher_type = tx.voucher_type
    voucher.activate()  # purchased vouchers start immediately
    db.session.add(voucher)
    db.session.commit()
    app.logger.info('Transaction %s completed, voucher %s issued', tx.id, voucher.code)
    send_sms_async(tx.phone, voucher_sms_text(voucher))
    router_sync_async()
    return voucher


# ---------------------------------------------------------------------------
# SMS
# ---------------------------------------------------------------------------

def voucher_sms_text(voucher):
    vtype = voucher.voucher_type
    expires = (voucher.expires_at + TZ_OFFSET).strftime('%d %b %H:%M') if voucher.expires_at else None
    text = f'Your WiFi voucher code is {voucher.code} ({vtype.name}, {vtype.speed_mbps} Mbps).'
    if expires:
        text += f' Valid until {expires}.'
    if SUPPORT_CONTACT:
        text += f' Help: {SUPPORT_CONTACT}'
    return text


def send_sms(phone, message):
    """Send one SMS. Returns True on success. Never raises."""
    if SMS_MODE == 'log':
        app.logger.info('SMS (not sent, no SMS provider configured) to %s: %s', phone, message)
        return True
    data = {'username': AT_USERNAME, 'to': '+' + phone.lstrip('+'), 'message': message}
    if AT_SENDER_ID:
        data['from'] = AT_SENDER_ID
    try:
        resp = requests.post(AT_SMS_URL, data=data, timeout=15,
                             headers={'apiKey': AT_API_KEY, 'Accept': 'application/json'})
        recipients = (resp.json().get('SMSMessageData') or {}).get('Recipients') or []
        ok = resp.status_code in (200, 201) and any(
            r.get('status') == 'Success' for r in recipients)
        if not ok:
            app.logger.warning('SMS to %s failed (%s): %s', phone, resp.status_code, resp.text[:300])
        return ok
    except Exception as exc:
        app.logger.warning('SMS to %s failed: %s', phone, exc)
        return False


def send_sms_async(phone, message):
    # Keep the M-Pesa callback fast: Safaricom retries callbacks that time out
    threading.Thread(target=send_sms, args=(phone, message), daemon=True).start()


def fail_transaction(tx, code, desc):
    if tx.status != 'pending':
        return
    tx.status = 'failed'
    tx.result_code = str(code)
    tx.result_desc = desc
    tx.completed_at = utcnow()
    db.session.commit()


def expire_vouchers():
    """Mark vouchers past their expiry as expired. Used by the cron job."""
    with app.app_context():
        count = (Voucher.query
                 .filter(Voucher.status == 'active', Voucher.expires_at <= utcnow())
                 .update({'status': 'expired'}, synchronize_session=False))
        stale = (Transaction.query
                 .filter(Transaction.status == 'pending',
                         Transaction.created_at <= utcnow() - timedelta(hours=1))
                 .update({'status': 'failed', 'result_desc': 'Timed out'},
                         synchronize_session=False))
        db.session.commit()
        print(f'Expired {count} vouchers, timed out {stale} pending transactions')
        if ROUTER_MODE == 'api':
            print('Router sync:', sync_router_api())
        return count


# ---------------------------------------------------------------------------
# Router (MikroTik hotspot)
# ---------------------------------------------------------------------------

def mark_expired_vouchers():
    (Voucher.query.filter(Voucher.status == 'active', Voucher.expires_at <= utcnow())
     .update({'status': 'expired'}, synchronize_session=False))
    db.session.commit()


def router_state():
    """Plans and the vouchers that should exist as hotspot users right now."""
    mark_expired_vouchers()
    plans = [{'profile': mikrotik.profile_name(t.id), 'rate_limit': mikrotik.rate_limit(t.speed_mbps)}
             for t in VoucherType.query.order_by(VoucherType.id).all()]
    now = utcnow()
    vouchers = []
    for v in Voucher.query.filter(Voucher.status.in_(('unused', 'active'))).order_by(Voucher.id).all():
        if v.status == 'active':
            seconds = (v.expires_at - now).total_seconds()
        else:
            seconds = v.voucher_type.duration_hours * 3600
        vouchers.append({'code': v.code, 'profile': mikrotik.profile_name(v.voucher_type_id),
                         'limit_uptime': seconds})
    return plans, vouchers


def sync_router_api():
    """Push vouchers to the router over the RouterOS API (ROUTER_MODE=api)."""
    if ROUTER_MODE != 'api':
        return {'skipped': 'ROUTER_MODE is not api'}
    if not (MIKROTIK_HOST and MIKROTIK_USER):
        set_setting('router_last_error', 'MIKROTIK_HOST and MIKROTIK_USER are not set')
        return {'error': 'MIKROTIK_HOST and MIKROTIK_USER are not set'}
    plans, vouchers = router_state()
    try:
        result = mikrotik.api_sync(MIKROTIK_HOST, MIKROTIK_USER, MIKROTIK_PASSWORD, MIKROTIK_PORT,
                                   plans, vouchers)
    except mikrotik.RouterApiError as exc:
        app.logger.warning('%s', exc)
        set_setting('router_last_error', str(exc))
        return {'error': str(exc)}
    # A code seen logged in on the router has been used: start its clock
    if result['active_codes']:
        for v in Voucher.query.filter(Voucher.code.in_(result['active_codes']),
                                      Voucher.status == 'unused').all():
            v.activate()
        db.session.commit()
    set_setting('router_last_sync', f"added {result['added']}, removed {result['removed']}")
    set_setting('router_last_error', '')
    return result


def router_sync_async():
    if ROUTER_MODE != 'api':
        return

    def run():
        with app.app_context():
            sync_router_api()
    threading.Thread(target=run, daemon=True).start()


def api_base_url():
    return PUBLIC_API_URL or request.host_url.rstrip('/') + '/api'


def walled_garden_hosts():
    hosts = []
    for url in (PORTAL_URL, api_base_url()):
        host = urlparse(url).hostname if url else None
        if host and host not in hosts and host not in ('localhost', '127.0.0.1'):
            hosts.append(host)
    return hosts


@app.cli.command('expire-vouchers')
def expire_vouchers_command():
    expire_vouchers()


# ---------------------------------------------------------------------------
# M-Pesa Daraja
# ---------------------------------------------------------------------------

class MpesaError(Exception):
    pass


_token_cache = {'token': None, 'expires': datetime.min}


def mpesa_access_token():
    if _token_cache['token'] and _token_cache['expires'] > utcnow():
        return _token_cache['token']
    resp = requests.get(f'{MPESA_BASE_URL}/oauth/v1/generate?grant_type=client_credentials',
                        auth=(MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET), timeout=30)
    if resp.status_code != 200:
        raise MpesaError(f'OAuth failed ({resp.status_code}): {resp.text[:200]}')
    data = resp.json()
    _token_cache['token'] = data['access_token']
    _token_cache['expires'] = utcnow() + timedelta(seconds=int(data.get('expires_in', 3599)) - 60)
    return _token_cache['token']


def _mpesa_password():
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    raw = f'{MPESA_SHORTCODE}{MPESA_PASSKEY}{timestamp}'
    return base64.b64encode(raw.encode()).decode(), timestamp


def callback_url():
    url = MPESA_CALLBACK_URL or request.host_url.rstrip('/') + '/api/mpesa/callback'
    if MPESA_CALLBACK_TOKEN:
        url += ('&' if '?' in url else '?') + f'token={MPESA_CALLBACK_TOKEN}'
    return url


def stk_push(tx):
    """Send the STK push. Returns (merchant_request_id, checkout_request_id)."""
    if MPESA_MODE == 'mock':
        return f'MOCK-{uuid.uuid4().hex[:12]}', f'ws_MOCK_{uuid.uuid4().hex}'

    password, timestamp = _mpesa_password()
    payload = {
        'BusinessShortCode': MPESA_SHORTCODE,
        'Password': password,
        'Timestamp': timestamp,
        'TransactionType': MPESA_TRANSACTION_TYPE,
        'Amount': int(round(tx.amount)),
        'PartyA': tx.phone,
        'PartyB': MPESA_SHORTCODE,
        'PhoneNumber': tx.phone,
        'CallBackURL': callback_url(),
        'AccountReference': f'WIFI{tx.id}',
        'TransactionDesc': f'WiFi {tx.voucher_type.name}'[:13],
    }
    resp = requests.post(f'{MPESA_BASE_URL}/mpesa/stkpush/v1/processrequest', json=payload,
                         headers={'Authorization': f'Bearer {mpesa_access_token()}'}, timeout=30)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200 or str(data.get('ResponseCode')) != '0':
        raise MpesaError(data.get('errorMessage') or data.get('ResponseDescription')
                         or f'STK push failed ({resp.status_code})')
    return data.get('MerchantRequestID'), data.get('CheckoutRequestID')


def stk_query(tx):
    """Ask Daraja for a pending STK push's result, for when no callback arrives
    (for example a local machine that Safaricom cannot reach)."""
    password, timestamp = _mpesa_password()
    resp = requests.post(f'{MPESA_BASE_URL}/mpesa/stkpushquery/v1/query', json={
        'BusinessShortCode': MPESA_SHORTCODE,
        'Password': password,
        'Timestamp': timestamp,
        'CheckoutRequestID': tx.checkout_request_id,
    }, headers={'Authorization': f'Bearer {mpesa_access_token()}'}, timeout=30)
    data = resp.json() if resp.content else {}
    if 'ResultCode' not in data:
        return  # still processing (Daraja answers with an errorCode)
    code = str(data['ResultCode'])
    if code == '0':
        complete_transaction(tx, desc=data.get('ResultDesc', 'Payment completed'))
    else:
        fail_transaction(tx, code, data.get('ResultDesc', 'Payment failed'))


def check_pending(tx):
    """Advance a pending transaction without waiting for the callback."""
    if tx.status != 'pending':
        return
    age = (utcnow() - tx.created_at).total_seconds()
    if MPESA_MODE == 'mock':
        if age >= MPESA_MOCK_DELAY:
            complete_transaction(tx, receipt='MOCK' + secrets.token_hex(3).upper(),
                                 desc='Mock payment completed')
        return
    if age > 180:
        fail_transaction(tx, 'timeout', 'No response from M-Pesa. Please try again.')
        return
    recently = tx.last_query_at and (utcnow() - tx.last_query_at).total_seconds() < 10
    if age >= 20 and not recently:
        tx.last_query_at = utcnow()
        db.session.commit()
        try:
            stk_query(tx)
        except Exception as exc:  # network trouble: just try again next poll
            app.logger.warning('STK query failed for tx %s: %s', tx.id, exc)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def make_token(admin):
    payload = {
        'sub': str(admin.id),
        'username': admin.username,
        'role': admin.role,
        'iat': utcnow(),
        'exp': utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm='HS256')


def require_auth(*roles):
    """Require a valid JWT; optionally restrict to the given roles."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            header = request.headers.get('Authorization', '')
            if not header.startswith('Bearer '):
                return error('Authentication required', 401)
            try:
                payload = jwt.decode(header[7:], SECRET_KEY, algorithms=['HS256'])
            except jwt.ExpiredSignatureError:
                return error('Session expired, please log in again', 401)
            except jwt.InvalidTokenError:
                return error('Invalid token', 401)
            admin = db.session.get(Admin, int(payload['sub']))
            if not admin or not admin.is_active:
                return error('Account disabled', 401)
            if roles and admin.role not in roles:
                return error('You do not have permission for this action', 403)
            g.admin = admin
            return fn(*args, **kwargs)
        return wrapper
    return decorator


@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    admin = Admin.query.filter_by(username=(data.get('username') or '').strip()).first()
    if not admin or not admin.check_password(data.get('password') or ''):
        return error('Invalid username or password', 401)
    if not admin.is_active:
        return error('Account disabled', 403)
    admin.last_login = utcnow()
    db.session.commit()
    return jsonify({'token': make_token(admin), 'admin': admin.to_dict()})


@app.route('/api/auth/me')
@require_auth()
def me():
    return jsonify(g.admin.to_dict())


@app.route('/api/auth/change-password', methods=['POST'])
@require_auth()
def change_password():
    data = request.get_json(silent=True) or {}
    if not g.admin.check_password(data.get('current_password') or ''):
        return error('Current password is incorrect', 400)
    new = data.get('new_password') or ''
    if len(new) < 8:
        return error('New password must be at least 8 characters', 400)
    g.admin.set_password(new)
    db.session.commit()
    return jsonify({'message': 'Password changed'})


# ---------------------------------------------------------------------------
# Public endpoints (customer portal)
# ---------------------------------------------------------------------------

@app.route('/api/health')
def health():
    try:
        db.session.execute(db.text('SELECT 1'))
        db_ok = True
    except Exception:
        db_ok = False
    return jsonify({
        'status': 'ok' if db_ok else 'degraded',
        'database': 'ok' if db_ok else 'error',
        'database_engine': db.engine.url.get_backend_name(),
        'mpesa_mode': MPESA_MODE,
        'mpesa_env': MPESA_ENV,
        'sms_mode': SMS_MODE,
        'router_mode': ROUTER_MODE,
        'radius_enabled': RADIUS_ENABLED,
        'time': iso(utcnow()),
    }), 200 if db_ok else 503


@app.route('/api/voucher-types')
def list_voucher_types():
    query = VoucherType.query
    if request.args.get('all') != '1':
        query = query.filter_by(is_active=True)
    return jsonify([v.to_dict() for v in query.order_by(VoucherType.duration_hours).all()])


@app.route('/api/customers', methods=['POST'])
def create_customer():
    """Create a customer, or return the existing one with that phone."""
    data = request.get_json(silent=True) or {}
    phone = normalize_phone(data.get('phone'))
    if not phone:
        return error('Enter a valid Safaricom number, e.g. 0712345678')
    customer = Customer.query.filter_by(phone=phone).first()
    if customer:
        if not customer.is_active:
            return error('This account is suspended. Contact support.', 403)
        return jsonify(customer.to_dict()), 200
    customer = Customer(phone=phone, name=(data.get('name') or None),
                        email=(data.get('email') or None))
    db.session.add(customer)
    db.session.commit()
    return jsonify(customer.to_dict()), 201


@app.route('/api/transactions/initiate', methods=['POST'])
def initiate_transaction():
    data = request.get_json(silent=True) or {}
    customer = None
    if data.get('customer_id'):
        customer = db.session.get(Customer, int(data['customer_id']))
    elif data.get('phone'):
        phone = normalize_phone(data['phone'])
        if not phone:
            return error('Invalid phone number')
        customer = Customer.query.filter_by(phone=phone).first()
        if not customer:
            customer = Customer(phone=phone)
            db.session.add(customer)
            db.session.flush()
    if not customer:
        return error('Customer not found', 404)
    if not customer.is_active:
        return error('This account is suspended. Contact support.', 403)

    if MPESA_MODE == 'disabled':
        return error('M-Pesa payments are not set up yet. Please try again later.', 503)
    vtype = db.session.get(VoucherType, int(data.get('voucher_type_id') or 0))
    if not vtype or not vtype.is_active:
        return error('Voucher type not available', 404)

    # Stop the same phone being spammed with prompts
    recent = (Transaction.query
              .filter(Transaction.customer_id == customer.id,
                      Transaction.status == 'pending',
                      Transaction.created_at >= utcnow() - timedelta(seconds=30))
              .first())
    if recent:
        return error('A payment request was just sent to this phone. '
                     'Please complete it or wait 30 seconds.', 429)

    tx = Transaction(customer_id=customer.id, voucher_type_id=vtype.id,
                     amount=vtype.price_ksh, phone=customer.phone, status='pending')
    tx.voucher_type = vtype
    db.session.add(tx)
    db.session.commit()

    try:
        tx.merchant_request_id, tx.checkout_request_id = stk_push(tx)
    except Exception as exc:
        app.logger.error('STK push failed for tx %s: %s', tx.id, exc)
        fail_transaction(tx, 'stk_error', str(exc)[:255])
        return error('Could not send the M-Pesa prompt. Please try again shortly.', 502)
    db.session.commit()

    return jsonify({
        'transaction_id': tx.reference,
        'status': tx.status,
        'amount': tx.amount,
        'voucher_type': vtype.name,
        'mode': MPESA_MODE,
        'message': 'Check your phone and enter your M-Pesa PIN',
    }), 201


@app.route('/api/transactions/<reference>/status')
def transaction_status(reference):
    tx = Transaction.query.filter_by(reference=reference).first()
    if not tx:
        return error('Transaction not found', 404)
    check_pending(tx)
    result = {
        'transaction_id': tx.reference,
        'status': tx.status,
        'amount': tx.amount,
        'voucher_type': tx.voucher_type.name,
        'result_desc': tx.result_desc,
        'mpesa_receipt': tx.mpesa_receipt,
        'sms_enabled': SMS_MODE != 'log',
        'voucher': None,
    }
    if tx.status == 'completed' and tx.voucher:
        tx.voucher.refresh_status()
        result['voucher'] = {
            'code': tx.voucher.code,
            'type': tx.voucher_type.name,
            'duration_hours': tx.voucher_type.duration_hours,
            'speed_mbps': tx.voucher_type.speed_mbps,
            'expires_at': iso(tx.voucher.expires_at),
            'status': tx.voucher.status,
        }
    return jsonify(result)


@app.route('/api/mpesa/callback', methods=['POST'])
def mpesa_callback():
    accepted = jsonify({'ResultCode': 0, 'ResultDesc': 'Accepted'})
    if MPESA_CALLBACK_TOKEN and request.args.get('token') != MPESA_CALLBACK_TOKEN:
        app.logger.warning('Rejected M-Pesa callback with bad token from %s', request.remote_addr)
        return error('Forbidden', 403)

    data = request.get_json(silent=True) or {}
    cb = (data.get('Body') or {}).get('stkCallback') or {}
    checkout_id = cb.get('CheckoutRequestID')
    tx = Transaction.query.filter_by(checkout_request_id=checkout_id).first() if checkout_id else None
    if not tx:
        app.logger.warning('Callback for unknown CheckoutRequestID %s', checkout_id)
        return accepted
    if tx.status != 'pending':
        return accepted  # duplicate delivery

    code = str(cb.get('ResultCode'))
    desc = cb.get('ResultDesc', '')
    if code != '0':
        fail_transaction(tx, code, desc or 'Payment cancelled')
        return accepted

    items = {i.get('Name'): i.get('Value')
             for i in (cb.get('CallbackMetadata') or {}).get('Item', [])}
    amount = float(items.get('Amount') or 0)
    if amount + 0.001 < tx.amount:
        fail_transaction(tx, 'amount_mismatch', f'Paid {amount}, expected {tx.amount}')
        return accepted
    complete_transaction(tx, receipt=items.get('MpesaReceiptNumber'), desc=desc)
    return accepted


@app.route('/api/vouchers/validate', methods=['POST'])
def validate_voucher():
    """For the hotspot/router login page: checks a code and starts unused ones."""
    data = request.get_json(silent=True) or {}
    code = (data.get('code') or '').strip().upper()
    voucher = Voucher.query.filter_by(code=code).first()
    if not voucher:
        return jsonify({'valid': False, 'reason': 'Voucher not found'}), 404
    if voucher.status == 'unused':
        voucher.activate()
    voucher.refresh_status()
    db.session.commit()
    valid = voucher.status == 'active'
    remaining = int((voucher.expires_at - utcnow()).total_seconds()) if valid else 0
    return jsonify({
        'valid': valid,
        'reason': None if valid else 'Voucher has expired',
        'code': voucher.code,
        'voucher_type': voucher.voucher_type.name,
        'speed_mbps': voucher.voucher_type.speed_mbps,
        'expires_at': iso(voucher.expires_at),
        'remaining_seconds': max(remaining, 0),
    }), 200 if valid else 410


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

def _limit(default=100, maximum=1000):
    try:
        return max(1, min(int(request.args.get('limit', default)), maximum))
    except ValueError:
        return default


@app.route('/api/customers')
@require_auth()
def list_customers():
    query = Customer.query
    search = (request.args.get('search') or '').strip()
    if search:
        like = f'%{search}%'
        query = query.filter(db.or_(Customer.phone.ilike(like), Customer.name.ilike(like)))
    customers = query.order_by(Customer.created_at.desc()).limit(_limit(500, 5000)).all()

    stats = dict(db.session.query(Transaction.customer_id, func.sum(Transaction.amount))
                 .filter(Transaction.status == 'completed')
                 .group_by(Transaction.customer_id).all())
    result = []
    for c in customers:
        d = c.to_dict()
        d['total_spent'] = float(stats.get(c.id) or 0)
        result.append(d)
    return jsonify(result)


@app.route('/api/customers/<int:customer_id>', methods=['PATCH'])
@require_auth('admin')
def update_customer(customer_id):
    customer = db.session.get(Customer, customer_id)
    if not customer:
        return error('Customer not found', 404)
    data = request.get_json(silent=True) or {}
    for field in ('name', 'email'):
        if field in data:
            setattr(customer, field, data[field] or None)
    if 'is_active' in data:
        customer.is_active = bool(data['is_active'])
    db.session.commit()
    return jsonify(customer.to_dict())


@app.route('/api/transactions')
@require_auth()
def list_transactions():
    query = Transaction.query
    if request.args.get('status'):
        query = query.filter_by(status=request.args['status'])
    if request.args.get('phone'):
        query = query.filter(Transaction.phone.contains(request.args['phone']))
    txs = query.order_by(Transaction.created_at.desc()).limit(_limit(200)).all()
    return jsonify([t.to_dict() for t in txs])


@app.route('/api/transactions/<int:tx_id>/resend-sms', methods=['POST'])
@require_auth('admin', 'finance')
def resend_voucher_sms(tx_id):
    tx = db.session.get(Transaction, tx_id)
    if not tx or not tx.voucher:
        return error('No voucher for this transaction', 404)
    if not send_sms(tx.phone, voucher_sms_text(tx.voucher)):
        return error('SMS could not be sent. Check the SMS settings and logs.', 502)
    return jsonify({'message': f'Voucher code sent to {tx.phone}', 'sms_mode': SMS_MODE})


@app.route('/api/vouchers')
@require_auth()
def list_vouchers():
    # Bring statuses up to date before listing
    (Voucher.query.filter(Voucher.status == 'active', Voucher.expires_at <= utcnow())
     .update({'status': 'expired'}, synchronize_session=False))
    db.session.commit()
    query = Voucher.query
    if request.args.get('status'):
        query = query.filter_by(status=request.args['status'])
    vouchers = query.order_by(Voucher.created_at.desc()).limit(_limit(200)).all()
    return jsonify([v.to_dict() for v in vouchers])


@app.route('/api/vouchers/generate', methods=['POST'])
@require_auth('admin')
def generate_vouchers():
    """Bulk-create unused codes, e.g. for printed cards sold for cash."""
    data = request.get_json(silent=True) or {}
    vtype = db.session.get(VoucherType, int(data.get('voucher_type_id') or 0))
    if not vtype:
        return error('Voucher type not found', 404)
    try:
        count = int(data.get('count', 1))
    except (TypeError, ValueError):
        return error('count must be a number')
    if not 1 <= count <= 500:
        return error('count must be between 1 and 500')
    vouchers = []
    for _ in range(count):
        v = Voucher(code=generate_voucher_code(), voucher_type_id=vtype.id, status='unused')
        db.session.add(v)
        db.session.flush()  # so the next code's uniqueness check sees this one
        vouchers.append(v)
    db.session.commit()
    router_sync_async()
    return jsonify([v.to_dict() for v in vouchers]), 201


@app.route('/api/voucher-types', methods=['POST'])
@require_auth('admin')
def create_voucher_type():
    data = request.get_json(silent=True) or {}
    try:
        vtype = VoucherType(name=data['name'].strip(), duration_hours=int(data['duration_hours']),
                            price_ksh=float(data['price_ksh']),
                            speed_mbps=int(data.get('speed_mbps') or 10))
    except (KeyError, ValueError, AttributeError):
        return error('name, duration_hours and price_ksh are required')
    if VoucherType.query.filter_by(name=vtype.name).first():
        return error('A voucher type with that name already exists', 409)
    db.session.add(vtype)
    db.session.commit()
    return jsonify(vtype.to_dict()), 201


@app.route('/api/voucher-types/<int:type_id>', methods=['PUT', 'PATCH'])
@require_auth('admin')
def update_voucher_type(type_id):
    vtype = db.session.get(VoucherType, type_id)
    if not vtype:
        return error('Voucher type not found', 404)
    data = request.get_json(silent=True) or {}
    try:
        if 'name' in data:
            vtype.name = data['name'].strip()
        if 'duration_hours' in data:
            vtype.duration_hours = int(data['duration_hours'])
        if 'price_ksh' in data:
            vtype.price_ksh = float(data['price_ksh'])
        if 'speed_mbps' in data:
            vtype.speed_mbps = int(data['speed_mbps'])
        if 'is_active' in data:
            vtype.is_active = bool(data['is_active'])
    except (ValueError, AttributeError):
        db.session.rollback()
        return error('Invalid values')
    db.session.commit()
    return jsonify(vtype.to_dict())


@app.route('/api/config')
def public_config():
    """Branding for the customer portal."""
    return jsonify({
        'business_name': BUSINESS_NAME,
        'support_contact': SUPPORT_CONTACT,
        'hotspot_login_url': HOTSPOT_LOGIN_URL,
        'sms_enabled': SMS_MODE != 'log',
    })


@app.route('/api/router/sync.rsc')
def router_sync_script():
    """Downloaded by the router every few seconds in pull mode."""
    if ROUTER_MODE != 'pull':
        return Response('# router sync disabled\n', mimetype='text/plain')
    if not hmac.compare_digest(request.args.get('key', ''), ROUTER_SYNC_KEY):
        return Response('# forbidden\n', status=403, mimetype='text/plain')
    plans, vouchers = router_state()
    set_setting('router_last_sync', f'{len(vouchers)} vouchers from {request.remote_addr}')
    return Response(mikrotik.render_sync_script(plans, vouchers), mimetype='text/plain')


def _router_setup_script():
    sync_url = f'{api_base_url()}/router/sync.rsc?key={ROUTER_SYNC_KEY}'
    return mikrotik.render_setup_script(sync_url, ROUTER_SYNC_INTERVAL, walled_garden_hosts())


@app.route('/api/router/status')
@require_auth('admin')
def router_status():
    last_sync, last_sync_at = get_setting('router_last_sync')
    last_error, last_error_at = get_setting('router_last_error')
    plans, vouchers = router_state()
    return jsonify({
        'mode': ROUTER_MODE,
        'sync_interval': ROUTER_SYNC_INTERVAL,
        'last_sync': last_sync,
        'last_sync_at': iso(last_sync_at),
        'last_error': last_error or None,
        'last_error_at': iso(last_error_at) if last_error else None,
        'voucher_count': len(vouchers),
        'mikrotik_host': MIKROTIK_HOST or None,
        'api_url': api_base_url(),
        'portal_url': PORTAL_URL or None,
        'walled_garden_hosts': walled_garden_hosts(),
        'api_url_is_local': (urlparse(api_base_url()).hostname or '') in ('localhost', '127.0.0.1'),
        'setup_script': _router_setup_script() if ROUTER_MODE == 'pull' else None,
    })


@app.route('/api/router/sync', methods=['POST'])
@require_auth('admin')
def router_sync_now():
    if ROUTER_MODE != 'api':
        return error('Sync now is only for ROUTER_MODE=api. In pull mode the router syncs itself.')
    result = sync_router_api()
    if 'error' in result:
        return error(result['error'], 502)
    return jsonify(result)


@app.route('/api/router/login.html')
@require_auth('admin')
def router_login_page():
    """The hotspot login page, filled in with this system's name and URLs."""
    with open(os.path.join(BASE_DIR, 'hotspot', 'login.html'), encoding='utf-8') as f:
        html = f.read()
    from html import escape
    portal = PORTAL_URL or request.host_url.rstrip('/')
    for key, value in {
        '{{BUSINESS_NAME}}': escape(BUSINESS_NAME),
        '{{PORTAL_URL}}': escape(portal),
        '{{API_URL}}': api_base_url(),
        '{{SUPPORT_CONTACT}}': f'Help: {escape(SUPPORT_CONTACT)}' if SUPPORT_CONTACT else '',
    }.items():
        html = html.replace(key, value)
    return Response(html, mimetype='text/html',
                    headers={'Content-Disposition': 'attachment; filename=login.html'})


# ---------------------------------------------------------------------------
# Routers (RADIUS clients) and online sessions
# ---------------------------------------------------------------------------

def _validate_router(data, router=None):
    name = (data.get('name') if 'name' in data else router.name if router else '') or ''
    address = (data.get('address') if 'address' in data else router.address if router else '') or ''
    vendor = data.get('vendor', router.vendor if router else 'other')
    if not name.strip():
        return 'Give the router a name'
    try:
        ipaddress.ip_network(address.strip(), strict=False)
    except ValueError:
        return 'Address must be an IP like 192.168.88.1 or a range like 41.90.0.0/16'
    if vendor not in Router.VENDORS:
        return 'Unknown router type'
    secret = (data.get('secret') or '').strip()
    if secret and len(secret) < 8:
        return 'The shared secret must be at least 8 characters'
    return None


@app.route('/api/routers')
@require_auth('admin')
def list_routers():
    return jsonify([r.to_dict(include_secret=True) for r in Router.query.order_by(Router.id).all()])


@app.route('/api/routers', methods=['POST'])
@require_auth('admin')
def create_router():
    data = request.get_json(silent=True) or {}
    problem = _validate_router(data)
    if problem:
        return error(problem)
    router = Router(name=data['name'].strip(), vendor=data.get('vendor', 'other'),
                    address=data['address'].strip(),
                    secret=(data.get('secret') or '').strip() or secrets.token_urlsafe(12))
    db.session.add(router)
    db.session.commit()
    return jsonify(router.to_dict(include_secret=True)), 201


@app.route('/api/routers/<int:router_id>', methods=['PATCH'])
@require_auth('admin')
def update_router(router_id):
    router = db.session.get(Router, router_id)
    if not router:
        return error('Router not found', 404)
    data = request.get_json(silent=True) or {}
    problem = _validate_router(data, router)
    if problem:
        return error(problem)
    for field in ('name', 'vendor', 'address', 'secret'):
        if data.get(field):
            setattr(router, field, data[field].strip())
    if 'is_active' in data:
        router.is_active = bool(data['is_active'])
    db.session.commit()
    return jsonify(router.to_dict(include_secret=True))


@app.route('/api/routers/<int:router_id>', methods=['DELETE'])
@require_auth('admin')
def delete_router(router_id):
    router = db.session.get(Router, router_id)
    if not router:
        return error('Router not found', 404)
    RadiusSession.query.filter_by(router_id=router.id).update({'router_id': None})
    db.session.delete(router)
    db.session.commit()
    return jsonify({'message': 'Router removed'})


@app.route('/api/radius/status')
@require_auth('admin')
def radius_status():
    heartbeat, heartbeat_at = get_setting('radius_heartbeat')
    unknown, unknown_at = get_setting('radius_unknown_nas')
    running = bool(heartbeat_at and utcnow() - heartbeat_at < timedelta(seconds=90))
    if unknown and unknown_at and utcnow() - unknown_at < timedelta(hours=1):
        for r in Router.query.all():  # hide it once it has been added
            try:
                if ipaddress.ip_address(unknown) in ipaddress.ip_network(r.address, strict=False):
                    unknown = None
                    break
            except ValueError:
                continue
    else:
        unknown = None
    host = RADIUS_PUBLIC_HOST or urlparse(api_base_url()).hostname or ''
    return jsonify({
        'enabled': RADIUS_ENABLED,
        'running': running,
        'heartbeat_at': iso(heartbeat_at),
        'server_host': host,
        'server_host_is_local': host in ('localhost', '127.0.0.1', ''),
        'auth_port': int(os.getenv('RADIUS_AUTH_PORT', '1812')),
        'acct_port': int(os.getenv('RADIUS_ACCT_PORT', '1813')),
        'unknown_router_ip': unknown,
        'portal_url': PORTAL_URL or None,
        'walled_garden_hosts': walled_garden_hosts(),
        'router_mode': ROUTER_MODE,
    })


@app.route('/api/sessions')
@require_auth()
def list_sessions():
    query = RadiusSession.query
    if request.args.get('online') == '1':
        cutoff = utcnow() - timedelta(seconds=int(os.getenv('RADIUS_INTERIM_INTERVAL', '300')) * 2 + 60)
        query = query.filter(RadiusSession.stopped_at.is_(None), RadiusSession.updated_at >= cutoff)
    sessions = query.order_by(RadiusSession.updated_at.desc()).limit(_limit(200)).all()
    return jsonify([s.to_dict() for s in sessions])


# ---------------------------------------------------------------------------
# Dashboard users
# ---------------------------------------------------------------------------

@app.route('/api/admins')
@require_auth('admin')
def list_admins():
    return jsonify([a.to_dict() for a in Admin.query.order_by(Admin.id).all()])


@app.route('/api/admins', methods=['POST'])
@require_auth('admin')
def create_admin():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    role = data.get('role') or 'viewer'
    if not username or len(password) < 8:
        return error('Username and a password of at least 8 characters are required')
    if role not in Admin.ROLES:
        return error(f'Role must be one of {", ".join(Admin.ROLES)}')
    if Admin.query.filter_by(username=username).first():
        return error('That username is taken', 409)
    user = Admin(username=username, email=(data.get('email') or None), role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify(user.to_dict()), 201


@app.route('/api/admins/<int:admin_id>', methods=['PATCH'])
@require_auth('admin')
def update_admin(admin_id):
    user = db.session.get(Admin, admin_id)
    if not user:
        return error('User not found', 404)
    data = request.get_json(silent=True) or {}
    if user.id == g.admin.id and (data.get('is_active') is False or
                                  data.get('role', 'admin') != 'admin'):
        return error('You cannot disable or demote your own account')
    if 'role' in data:
        if data['role'] not in Admin.ROLES:
            return error('Invalid role')
        user.role = data['role']
    if 'is_active' in data:
        user.is_active = bool(data['is_active'])
    if data.get('password'):
        if len(data['password']) < 8:
            return error('Password must be at least 8 characters')
        user.set_password(data['password'])
    db.session.commit()
    return jsonify(user.to_dict())


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def _days():
    try:
        return max(1, min(int(request.args.get('days', 30)), 366))
    except ValueError:
        return 30


def _window_start(days):
    """UTC instant of local midnight at the start of the window."""
    local_today = (utcnow() + TZ_OFFSET).date()
    start_local = datetime.combine(local_today - timedelta(days=days - 1), datetime.min.time())
    return start_local - TZ_OFFSET


@app.route('/api/analytics/dashboard')
@require_auth()
def analytics_dashboard():
    days = _days()
    since = _window_start(days)
    completed = (Transaction.status == 'completed', Transaction.completed_at >= since)

    total_revenue, total_tx = (db.session.query(func.coalesce(func.sum(Transaction.amount), 0),
                                                func.count(Transaction.id))
                               .filter(*completed).one())
    by_type = (db.session.query(VoucherType.name, func.coalesce(func.sum(Transaction.amount), 0),
                                func.count(Transaction.id))
               .join(Transaction, Transaction.voucher_type_id == VoucherType.id)
               .filter(*completed).group_by(VoucherType.name).all())
    by_status = dict(db.session.query(Transaction.status, func.count(Transaction.id))
                     .filter(Transaction.created_at >= since)
                     .group_by(Transaction.status).all())
    active_vouchers = (Voucher.query
                       .filter(Voucher.status == 'active', Voucher.expires_at > utcnow()).count())
    new_customers = Customer.query.filter(Customer.created_at >= since).count()

    order = {v.name: v.duration_hours for v in VoucherType.query.all()}
    revenue_by_type = sorted(
        [{'name': n, 'revenue': float(r), 'count': c} for n, r, c in by_type],
        key=lambda x: order.get(x['name'], 0))

    return jsonify({
        'days': days,
        'total_revenue': float(total_revenue),
        'total_transactions': total_tx,
        'total_customers': Customer.query.count(),
        'new_customers': new_customers,
        'active_vouchers': active_vouchers,
        'pending_transactions': by_status.get('pending', 0),
        'failed_transactions': by_status.get('failed', 0),
        'revenue_by_type': revenue_by_type,
    })


@app.route('/api/analytics/revenue/daily')
@require_auth()
def analytics_revenue_daily():
    """One row per local day: {date, '<voucher type name>': revenue, ..., total}."""
    days = _days()
    since = _window_start(days)
    type_names = [v.name for v in VoucherType.query.order_by(VoucherType.duration_hours).all()]

    rows = (db.session.query(Transaction.completed_at, Transaction.amount, VoucherType.name)
            .join(VoucherType, Transaction.voucher_type_id == VoucherType.id)
            .filter(Transaction.status == 'completed', Transaction.completed_at >= since).all())

    local_start = (since + TZ_OFFSET).date()
    table = {}
    for i in range(days):
        day = (local_start + timedelta(days=i)).isoformat()
        table[day] = {'date': day, **{n: 0.0 for n in type_names}, 'total': 0.0, 'count': 0}
    for completed_at, amount, name in rows:
        day = (completed_at + TZ_OFFSET).date().isoformat()
        if day in table:
            table[day][name] = table[day].get(name, 0.0) + float(amount)
            table[day]['total'] += float(amount)
            table[day]['count'] += 1
    return jsonify(list(table.values()))


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Built frontend (so one process can run everything on a local PC)
# ---------------------------------------------------------------------------

FRONTEND_BUILD = os.path.abspath(os.getenv('FRONTEND_BUILD_DIR') or
                                 os.path.join(BASE_DIR, '..', 'frontend', 'build'))


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def frontend(path):
    if path.startswith('api/') or not os.path.isfile(os.path.join(FRONTEND_BUILD, 'index.html')):
        return error('Not found', 404)
    if path and os.path.isfile(os.path.join(FRONTEND_BUILD, path)):
        return send_from_directory(FRONTEND_BUILD, path)
    return send_from_directory(FRONTEND_BUILD, 'index.html')


@app.errorhandler(404)
def not_found(_):
    return error('Not found', 404)


@app.errorhandler(500)
def server_error(exc):
    db.session.rollback()
    app.logger.exception('Server error: %s', exc)
    return error('Internal server error', 500)


with app.app_context():
    try:
        db.create_all()
    except Exception as exc:  # e.g. several gunicorn workers racing on first boot
        app.logger.warning('create_all skipped: %s', exc)

if __name__ == '__main__':
    print(f' * Database: {app.config["SQLALCHEMY_DATABASE_URI"].split("@")[-1]}')
    print(f' * M-Pesa mode: {MPESA_MODE} ({MPESA_ENV})')
    print(f' * SMS mode: {SMS_MODE}')
    print(f' * RADIUS server: {"on (udp 1812/1813)" if RADIUS_ENABLED else "off"}')
    print(f' * MikroTik direct sync: {ROUTER_MODE}')
    if RADIUS_ENABLED:
        def radius_loop():
            # Import by module name so RADIUS shares the models' registry
            import billing_system_backend as backend
            import radius_server
            try:
                radius_server.run(backend.app)
            except OSError as exc:
                app.logger.error('RADIUS server could not start (%s). Is another RADIUS server '
                                 'using ports 1812/1813? Set RADIUS_ENABLED=0 to turn it off.', exc)
        threading.Thread(target=radius_loop, daemon=True).start()

    if ROUTER_MODE == 'api':
        def router_loop():
            # Removes expired codes from the router and starts codes seen in use
            import time
            while True:
                time.sleep(ROUTER_SYNC_INTERVAL)
                with app.app_context():
                    try:
                        sync_router_api()
                    except Exception as exc:
                        app.logger.warning('Router sync loop: %s', exc)
                    finally:
                        db.session.remove()
        threading.Thread(target=router_loop, daemon=True).start()

    host, port = os.getenv('HOST', '0.0.0.0'), int(os.getenv('PORT', '5000'))
    if IS_PRODUCTION:
        from waitress import serve  # production server that also runs on Windows
        print(f' * Serving on http://{host}:{port}')
        serve(app, host=host, port=port, threads=8)
    else:
        # No auto-reloader: it would start the RADIUS server twice
        app.run(host=host, port=port, debug=True, use_reloader=False)
