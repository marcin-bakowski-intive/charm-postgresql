import ipaddress
import random
import re
import string
import subprocess
import urllib.parse

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
        # 'allowed-units': '',
        # 'allowed-subnets': ','.join(_incoming_addresses(data)),
        # copy requested roles and extensions values,
        # they are required during connection data validation in pgsql interface _cs() method
        # 'roles': data.get('roles', ''),
        # 'extensions': data.get('extensions', ''),
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
    return ConnectionString(
        host=host,
        port=port,
        dbname=database,
        user=username,
        password=password,
    )


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
    if "egress-subnets" in relinfo:
        return [n.strip() for n in relinfo["egress-subnets"].split(",") if n.strip()]
    if "ingress-address" in relinfo:
        return [_addr_to_range(relinfo["ingress-address"])]
    if "private-address" in relinfo:
        return [_addr_to_range(relinfo["private-address"])]
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


class ConnectionString(str):
    """A libpq connection string.

    >>> c = ConnectionString(host='1.2.3.4', dbname='mydb',
    ...                      port=5432, user='anon', password='secret')
    ...
    >>> c
    'host=1.2.3.4 dbname=mydb port=5432 user=anon password=secret
    >>> ConnectionString(str(c), dbname='otherdb')
    'host=1.2.3.4 dbname=otherdb port=5432 user=anon password=secret

    Components may be accessed as attributes.

    >>> c.dbname
    'mydb'
    >>> c.host
    '1.2.3.4'
    >>> c.port
    '5432'

    The standard URI format is also accessible:

    >>> c.uri
    'postgresql://anon:secret@1.2.3.4:5432/mydb'

    """

    def __new__(self, conn_str=None, **kw):
        # Parse libpq key=value style connection string. Components
        # passed by keyword argument override. If the connection string
        # is invalid, some components may be skipped (but in practice,
        # where database and usernames don't contain whitespace,
        # quotes or backslashes, this doesn't happen).
        if conn_str is not None:
            r = re.compile(
                r"""(?x)
                               (\w+) \s* = \s*
                               (?:
                                 '((?:.|\.)*?)' |
                                 (\S*)
                               )
                               (?=(?:\s|\Z))
                           """
            )
            for key, v1, v2 in r.findall(conn_str):
                if key not in kw:
                    kw[key] = v1 or v2

        def quote(x):
            q = str(x).replace("\\", "\\\\").replace("'", "\\'")
            q = q.replace('\n', ' ')  # \n is invalid in connection strings
            if ' ' in q:
                q = "'" + q + "'"
            return q

        c = " ".join("{}={}".format(k, quote(v)) for k, v in sorted(kw.items()) if v)
        c = str.__new__(self, c)

        for k, v in kw.items():
            setattr(c, k, v)

        self._keys = set(kw.keys())

        # Construct the documented PostgreSQL URI for applications
        # that use this format. PostgreSQL docs refer to this as a
        # URI so we do do, even though it meets the requirements the
        # more specific term URL.
        fmt = ['postgresql://']
        d = {k: urllib.parse.quote(v, safe='') for k, v in kw.items() if v}
        if 'user' in d:
            if 'password' in d:
                fmt.append('{user}:{password}@')
            else:
                fmt.append('{user}@')
        if 'host' in kw:
            try:
                hostaddr = ipaddress.ip_address(kw.get('hostaddr') or kw.get('host'))
                if isinstance(hostaddr, ipaddress.IPv6Address):
                    d['hostaddr'] = '[{}]'.format(hostaddr)
                else:
                    d['hostaddr'] = str(hostaddr)
            except ValueError:
                # Not an IP address, but hopefully a resolvable name.
                d['hostaddr'] = d['host']
            del d['host']
            fmt.append('{hostaddr}')
        if 'port' in d:
            fmt.append(':{port}')
        if 'dbname' in d:
            fmt.append('/{dbname}')
        main_keys = frozenset(['user', 'password', 'dbname', 'hostaddr', 'port'])
        extra_fmt = ['{}={{{}}}'.format(extra, extra) for extra in sorted(d.keys()) if extra not in main_keys]
        if extra_fmt:
            fmt.extend(['?', '&'.join(extra_fmt)])
        c.uri = ''.join(fmt).format(**d)

        return c

    host = None
    dbname = None
    port = None
    user = None
    password = None
    uri = None

    def keys(self):
        return iter(self._keys)

    def items(self):
        return {k: self[k] for k in self.keys()}.items()

    def values(self):
        return iter(self[k] for k in self.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return super(ConnectionString, self).__getitem__(key)
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)
