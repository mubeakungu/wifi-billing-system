"""
MikroTik hotspot integration

Every unused or active voucher becomes a hotspot user on the router
(username = password = voucher code), with a user profile per plan that sets
the speed. Expired vouchers are removed and their sessions disconnected.

Two ways to keep the router in step:
  * pull (default): the router runs a scheduled script that downloads
    /api/router/sync.rsc every ROUTER_SYNC_INTERVAL seconds and runs it.
    Works when the backend is in the cloud and the router is behind NAT.
  * api: the backend connects to the RouterOS API (port 8728) and pushes
    changes straight away. Needs the backend to reach the router, e.g. when
    both are on the same network or connected by VPN.

The desired state passed in here is plain data:
    plans:    [{'profile': 'wb-1', 'rate_limit': '10M/10M'}]
    vouchers: [{'code': 'AB3K9PQX', 'profile': 'wb-1', 'limit_uptime': 10800}]
"""

COMMENT = 'wifi-billing'


def profile_name(plan_id):
    return f'wb-{plan_id}'


def rate_limit(speed_mbps):
    return f'{int(speed_mbps)}M/{int(speed_mbps)}M'


def ros_time(seconds):
    """RouterOS duration, e.g. 1d02:03:04 or 03:00:00."""
    seconds = max(int(seconds), 60)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    clock = f'{hours:02d}:{minutes:02d}:{secs:02d}'
    return f'{days}d{clock}' if days else clock


def _q(value):
    """Quote a value for a RouterOS script string."""
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$') + '"'


def render_sync_script(plans, vouchers):
    """RouterOS script that makes the router's hotspot users match the vouchers.

    The router downloads this into memory and runs it with :parse (no flash
    writes). One short line per voucher keeps it well under fetch's size limit.
    """
    lines = [
        '# WiFi billing sync - generated, do not edit',
        f':local cm {_q(COMMENT)}',
        ':local pr do={ :if ([:len [/ip hotspot user profile find name=$n]] = 0) do={ '
        '/ip hotspot user profile add name=$n rate-limit=$r shared-users=1 } else={ '
        '/ip hotspot user profile set [/ip hotspot user profile find name=$n] rate-limit=$r } }',
        ':local us do={ :if ([:len [/ip hotspot user find name=$c]] = 0) do={ '
        '/ip hotspot user add name=$c password=$c profile=$p limit-uptime=[:totime $t] comment=$m } }',
    ]
    for p in plans:
        lines.append(f'$pr n={_q(p["profile"])} r={_q(p["rate_limit"])}')
    for v in vouchers:
        lines.append(f'$us c={_q(v["code"])} p={_q(v["profile"])} '
                     f't={_q(ros_time(v["limit_uptime"]))} m=$cm')

    valid = ','.join(v['code'] for v in vouchers)
    lines += [
        f':local valid [:toarray {_q(valid)}]',
        ':foreach u in=[/ip hotspot user find comment=$cm] do={',
        '  :local n [/ip hotspot user get $u name]',
        '  :local keep false',
        '  :foreach c in=$valid do={ :if ($c = $n) do={ :set keep true } }',
        '  :if (!$keep) do={',
        '    /ip hotspot active remove [/ip hotspot active find user=$n]',
        '    /ip hotspot user remove $u',
        '  }',
        '}',
        '',
    ]
    return '\n'.join(lines)


def _is_ip(host):
    import ipaddress
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def render_setup_script(sync_url, interval_seconds, walled_garden_hosts):
    """One-time script the admin pastes into the router terminal (pull mode)."""
    fetch = (f':local r [/tool fetch url={_q(sync_url)} output=user as-value]; '
             ':if (($r->"status") = "finished") do={ :local f [:parse ($r->"data")]; $f }')
    lines = [
        '# WiFi billing - one-time MikroTik setup',
        '# Paste into Winbox > New Terminal. Needs RouterOS v7 and a working hotspot',
        '# (IP > Hotspot > Hotspot Setup). Safe to run again.',
        '',
        '# 1. Let customers reach the payment page before they log in',
        f'/ip hotspot walled-garden remove [find comment={_q(COMMENT)}]',
        f'/ip hotspot walled-garden ip remove [find comment={_q(COMMENT)}]',
    ]
    for host in walled_garden_hosts:
        if _is_ip(host):
            lines.append(f'/ip hotspot walled-garden ip add dst-address={host} action=accept '
                         f'comment={_q(COMMENT)}')
            continue
        lines.append(f'/ip hotspot walled-garden add dst-host={_q(host)} comment={_q(COMMENT)}')
        lines.append(f'/ip hotspot walled-garden ip add dst-host={_q(host)} action=accept '
                     f'comment={_q(COMMENT)}')
    lines += [
        '',
        '# 2. Download voucher changes from the billing system regularly',
        '/system script remove [find name="wifi-billing-sync"]',
        f'/system script add name="wifi-billing-sync" source={_q(fetch)}',
        '/system scheduler remove [find name="wifi-billing-sync"]',
        f'/system scheduler add name="wifi-billing-sync" interval={int(interval_seconds)}s '
        'on-event="/system script run wifi-billing-sync"',
        '',
        '# 3. Run it once now',
        '/system script run wifi-billing-sync',
        '',
    ]
    return '\n'.join(lines)


class RouterApiError(Exception):
    pass


def api_sync(host, username, password, port, plans, vouchers, timeout=10):
    """Push the desired state over the RouterOS API.

    Returns {'added': n, 'removed': n, 'active_codes': [...]} where
    active_codes are vouchers currently logged in (used to start unused codes).
    """
    try:
        from librouteros import connect
    except ImportError as exc:  # pragma: no cover
        raise RouterApiError('librouteros is not installed') from exc

    try:
        api = connect(host=host, username=username, password=password, port=int(port),
                      timeout=timeout)
    except Exception as exc:
        raise RouterApiError(f'Cannot connect to router {host}:{port}: {exc}') from exc

    try:
        profiles = api.path('ip', 'hotspot', 'user', 'profile')
        existing_profiles = {p.get('name'): p for p in profiles}
        for p in plans:
            current = existing_profiles.get(p['profile'])
            if current is None:
                profiles.add(**{'name': p['profile'], 'rate-limit': p['rate_limit'],
                                'shared-users': 1})
            elif current.get('rate-limit') != p['rate_limit']:
                profiles.update(**{'.id': current['.id'], 'rate-limit': p['rate_limit']})

        users = api.path('ip', 'hotspot', 'user')
        ours = {u.get('name'): u['.id'] for u in users if u.get('comment') == COMMENT}
        wanted = {v['code']: v for v in vouchers}

        added = 0
        for code, v in wanted.items():
            if code not in ours:
                users.add(**{'name': code, 'password': code, 'profile': v['profile'],
                             'limit-uptime': ros_time(v['limit_uptime']), 'comment': COMMENT})
                added += 1

        active = api.path('ip', 'hotspot', 'active')
        sessions = list(active)
        stale = [name for name in ours if name not in wanted]
        if stale:
            kick = [s['.id'] for s in sessions if s.get('user') in stale]
            if kick:
                active.remove(*kick)
            users.remove(*[ours[name] for name in stale])

        return {
            'added': added,
            'removed': len(stale),
            'active_codes': [s.get('user') for s in sessions if s.get('user') in wanted],
        }
    except RouterApiError:
        raise
    except Exception as exc:
        raise RouterApiError(f'Router sync failed: {exc}') from exc
    finally:
        try:
            api.close()
        except Exception:
            pass
