#!/usr/bin/env python
"""
Initialize the database for a new, empty billing system.

    python init_db.py                 # tables + your admin account
    python init_db.py --starter-plans # also add 4 common plans you can edit later

The admin account is taken from ADMIN_USERNAME / ADMIN_PASSWORD if set,
otherwise you are asked for it (or a random password is printed when not
running in a terminal, e.g. during a cloud deploy). Running it again is safe.
"""

import getpass
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from billing_system_backend import app, db, Admin, VoucherType

STARTER_PLANS = [
    {'name': '3 hours', 'duration_hours': 3, 'price_ksh': 30, 'speed_mbps': 5},
    {'name': '24 hours', 'duration_hours': 24, 'price_ksh': 50, 'speed_mbps': 5},
    {'name': '1 week', 'duration_hours': 168, 'price_ksh': 250, 'speed_mbps': 5},
    {'name': '30 days', 'duration_hours': 720, 'price_ksh': 800, 'speed_mbps': 5},
]


def ask_admin_credentials():
    username = os.getenv('ADMIN_USERNAME', '').strip() or 'admin'
    password = os.getenv('ADMIN_PASSWORD', '')
    if password:
        return username, password, False
    if sys.stdin.isatty():
        entered = input(f'Admin username [{username}]: ').strip()
        username = entered or username
        while True:
            password = getpass.getpass('Admin password (8+ characters): ')
            if len(password) < 8:
                print('  Too short, try again.')
            elif password != getpass.getpass('Repeat password: '):
                print('  Passwords do not match, try again.')
            else:
                return username, password, False
    return username, secrets.token_urlsafe(9), True


def init_database(starter_plans=False):
    with app.app_context():
        print('Creating database tables...')
        db.create_all()
        print('✓ Tables ready')

        if Admin.query.filter_by(role='admin').count():
            print('✓ Admin account already exists')
        else:
            username, password, generated = ask_admin_credentials()
            admin = Admin(username=username, role='admin', is_active=True)
            admin.set_password(password)
            db.session.add(admin)
            db.session.commit()
            print(f'✓ Created admin account: {username}')
            if generated:
                print(f'  Generated password: {password}')
                print('  Sign in and change it in the dashboard (Account tab).')

        if starter_plans:
            for plan in STARTER_PLANS:
                if not VoucherType.query.filter_by(name=plan['name']).first():
                    db.session.add(VoucherType(**plan))
                    print(f"✓ Added plan {plan['name']} - KES {plan['price_ksh']}")
            db.session.commit()

        if not VoucherType.query.count():
            print('\nNo plans yet: sign in to the dashboard and add them in the Plans tab.')
        print('\n✅ Ready. Start the server with: python billing_system_backend.py')


if __name__ == '__main__':
    init_database(starter_plans='--starter-plans' in sys.argv)
