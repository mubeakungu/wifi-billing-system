# WiFi Billing System

A blank, brand-neutral billing system for WiFi hotspots. Customers pay with M-Pesa
and get a voucher code; your router, access point controller or firewall checks the
code with this system's built-in **RADIUS server**, which applies the plan's time
limit and speed and keeps each code on one device.

It works with anything that has a hotspot / captive portal with RADIUS:
**MikroTik, Ubiquiti UniFi, TP-Link Omada, pfSense, OPNsense, OpenWrt
(CoovaChilli/openNDS), FortiGate, Cisco/Meraki, Ruckus, Aruba, Cambium, Grandstream**
and others. Home routers without hotspot support (most **Tenda**, TP-Link Archer/Deco,
ISP routers) work as access points behind a hotspot gateway, see below.

It starts empty: no sample data, no plans and one admin account you create.

```
backend/    Flask API + RADIUS server (radius_server.py), init_db.py
frontend/   React app: customer portal + admin dashboard
Dockerfile, docker-compose.yml   one-command install on a PC or VPS
render.yaml                      optional Render deploy (MikroTik only, no UDP there)
```

## How it works

```
 Customer joins WiFi ──► your router's login page ── "Buy with M-Pesa" ──► customer portal
                                │                                              │
                                │                                   M-Pesa STK push, paid
                                │                                              │
                     enters voucher code  ◄──────── code on screen + SMS ──────┘
                                │
                                ▼
            router ── RADIUS (UDP 1812/1813) ──► billing system: code valid?
                                                  ✓ time left, ✓ speed, ✓ one device
```

* A voucher's time starts when it's bought (or, for printed cash codes, on first login).
* The router disconnects the customer when the time runs out (Session-Timeout).
* Speed limits are sent the way each brand understands (Mikrotik-Rate-Limit,
  WISPr-Bandwidth, ChilliSpot). Where a device ignores them, set a limit on the guest network.
* Accounting shows who is online, on which router, and data used.

## Install

The RADIUS server uses UDP, so run the system on **a PC/mini-PC in your network**
or **a VPS** (any cloud Linux server with a public IP). Routers anywhere can use one
server, as long as they can reach it on UDP 1812/1813.

### With Docker (recommended)

```bash
cp backend/.env.example backend/.env      # then edit it, see "Settings"
docker compose up -d --build
docker compose logs app | grep -i password   # if you didn't set ADMIN_PASSWORD
```

Open `http://<server-ip>:5000/#/admin`. Open ports 5000/tcp, 1812/udp and 1813/udp
in the server's firewall.

Docker Desktop on Windows/macOS hides the routers' real IPs: every RADIUS request seems
to come from Docker's own gateway (e.g. `172.18.0.1`), so with several routers they can't
be told apart. On a Windows PC, prefer the install without Docker below. On Linux, Docker
keeps the real IPs.

### Without Docker (Windows, Linux, macOS)

Needs Python 3.10+ and Node 18+.

```bash
cd frontend && npm install && npm run build && cd ..
cd backend
python -m venv venv
venv\Scripts\activate                 # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                # Linux/macOS: cp .env.example .env  (then edit)
python init_db.py                     # creates your admin account
python billing_system_backend.py      # web on :5000 + RADIUS on udp 1812/1813
```

Keep it running (Task Scheduler on Windows, systemd on Linux). Give the PC a fixed IP.

### Settings (`backend/.env`)

```
BUSINESS_NAME=Your WiFi Name
SUPPORT_CONTACT=0712345678
FLASK_ENV=production
SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
PORTAL_URL=http://192.168.88.10:5000          # where customers buy (your server)
PUBLIC_API_URL=http://192.168.88.10:5000/api
RADIUS_PUBLIC_HOST=192.168.88.10              # address routers use to reach RADIUS
MPESA_CONSUMER_KEY=... MPESA_CONSUMER_SECRET=... MPESA_PASSKEY=... MPESA_SHORTCODE=...
```

Use your domain or public IP instead of `192.168.88.10` on a VPS.

## First steps in the dashboard

1. **Plans**: add your plans (name, hours, price, speed).
2. **Routers**: add each router, controller or firewall with its IP and a shared
   secret (leave blank to generate one), then click **Setup steps**. You get the exact
   settings for that brand with your server address and secret filled in.
3. **Account**: change your password and add staff (finance / viewer).
4. Buy a cheap test voucher on your phone and log in on the WiFi with the code.

The Routers tab shows whether the RADIUS server is running, when each router was last
heard from, and who is online. If a device sends requests before you add it, it shows
"A device at … is sending RADIUS requests" with an **Add it** button.

### Which IP to enter for a router

The address the RADIUS requests come *from*: the router's LAN IP when the server is on
the same network, or its public IP (or your ISP's range, e.g. `41.90.0.0/16`) when the
server is a VPS. For UniFi and Omada, it's the gateway or controller that sends RADIUS.

### Tenda, TP-Link home routers and other routers without a hotspot

They can't ask a billing system who may connect, so keep them as access points:

```
Internet ──► hotspot gateway (MikroTik hAP lite / OpenWrt / pfSense) ──► Tenda/TP-Link in AP mode ──► customers
```

Set the Tenda/TP-Link to Access Point mode (DHCP off), connect it to the gateway, and
add the gateway in the Routers tab.

## M-Pesa

Get keys from https://developer.safaricom.co.ke. Test with `MPESA_ENV=sandbox`, then
switch to `production` with your own shortcode and passkey
(`MPESA_TRANSACTION_TYPE=CustomerBuyGoodsOnline` for a till).

`MPESA_CALLBACK_URL` should be a public HTTPS address. Without one (e.g. a LAN install),
payments still complete: while the customer's page waits, the system asks M-Pesa for
the result directly.

Without keys, local test runs fake the payment after a few seconds. With
`FLASK_ENV=production`, missing keys disable payments, so nobody gets free vouchers.

## SMS (optional)

Codes are always shown on screen. To also SMS them, set `AT_USERNAME` and `AT_API_KEY`
from [Africa's Talking](https://africastalking.com).

## Cloud hosting without UDP (Render)

Render and similar platforms can't receive RADIUS. `render.yaml` deploys the portal and
dashboard there with RADIUS off; MikroTik routers can then use the MikroTik-only sync
(`ROUTER_MODE=pull`: the router downloads codes every 20 s; a setup script appears in the
Routers tab). Other brands need the Docker install on a VPS.

## Trying it out

```bash
cd backend && pip install -r requirements.txt
python init_db.py --starter-plans        # 4 example plans you can edit or delete
python billing_system_backend.py         # fake M-Pesa, SQLite, RADIUS on 1812/1813
cd ../frontend && npm install && npm start   # http://localhost:3000
```

Any RADIUS test tool works against it, e.g.
`echo "User-Name=CODE,User-Password=CODE" | radclient 127.0.0.1 auth <secret>`
after adding a router with address `127.0.0.1`.

## Reference

* Ports: 5000/tcp (web), 1812/udp (RADIUS auth), 1813/udp (accounting).
* RADIUS: PAP and CHAP; replies always carry Message-Authenticator; requests from
  unknown IPs or with a wrong secret are ignored.
* Public API: `/api/config`, `/api/voucher-types`, `/api/customers`,
  `/api/transactions/initiate`, `/api/transactions/<ref>/status`, `/api/mpesa/callback`,
  `/api/vouchers/validate`.
* Signed in: customers, transactions, vouchers, plans, analytics, `/api/sessions`;
  admin only: `/api/routers`, `/api/radius/status`, `/api/admins`.
