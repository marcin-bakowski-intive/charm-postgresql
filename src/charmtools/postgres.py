from functools import partial
import ipaddress
import random
import string
from urllib.parse import quote

from charmtools import tools


def create_pg_database_and_user(host, port, database, username=None):
    username = username or database
    password = _get_random_string(16)

    _psql(f'CREATE DATABASE {database}')
    _psql(f"CREATE USER {username} WITH ENCRYPTED PASSWORD '{password}'")
    _psql(f'GRANT ALL PRIVILEGES ON DATABASE {database} TO {username}')

    return {
        'host': host,
        'port': port,
        'database': database,
        'user': username,
        'password': password,
        'master': _build_connection_string(host, port, database, username, password),
    }


def drop_pg_database(database):
    _psql(f'DROP DATABASE {database}')


def drop_pg_user(user):
    _psql(f'DROP USER {user}')


def _psql(query):
    tools.run('sudo', '-u', 'postgres', 'psql', '-c', f'{query}')


def _get_random_string(length):
    letters = string.ascii_letters + string.digits
    return ''.join(random.choice(letters) for _ in range(length))


def _build_connection_string(host, port, database, username, password):
    q = partial(quote, safe='')
    return f'postgresql://{q(username)}:{q(password)}@{q(host)}:{port}/{q(database)}'


def incoming_addresses(relinfo):
    """Return the incoming address range(s) if present in relinfo.

    Address ranges are in CIDR format. eg. 192.168.1.0/24 or 2001::F00F/128.
    We look for information as provided by recent versions of Juju, and
    fall back to private-address if needed.

    Returns an empty list if no address information is present. An
    error is logged if this occurs, as something has gone seriously
    wrong.
    """
    # This helper could return a set, but a list with stable ordering is
    # easier to use without causing flapping.
    if 'egress-subnets' in relinfo:
        return [n.strip() for n in relinfo['egress-subnets'].split(',') if n.strip()]
    if 'ingress-address' in relinfo:
        return [_addr_to_range(relinfo['ingress-address'])]
    if 'private-address' in relinfo:
        return [_addr_to_range(relinfo['private-address'])]
    return []


def _addr_to_range(addr):
    """Convert an address to a format suitable for pg_hba.conf.

    IPv4 and IPv6 ranges are passed through unchanged, as are hostnames.
    Individual IPv4 and IPv6 addresses have a hostmask appended.
    """
    try:
        return str(ipaddress.ip_network(addr, strict=False))
    except ValueError:
        return addr
