from functools import partial
from pathlib import Path
import random
import re
import string
from urllib.parse import quote

from charmtools import service, tools

POSTGRESQL_VERSION_PATTERN = re.compile(r'PostgreSQL (\d+\.\d+) ')
POSTGRESQL_CONF_BASE_DIR = Path('/etc/postgresql')
POSTGRESQL_CONF_JUJU_START_MARK = '# JUJU SECTION'
POSTGRESQL_CONF_JUJU_END_MARK = '# JUJU END SECTION'


class PGService:
    def __init__(self, host='localhost', user='postgres', port=5432):
        self._host = host
        self._user = user
        self._port = str(port)
        self._version = None

    def set_host(self, host):
        self._host = host

    def set_user(self, user):
        self._user = user

    def set_port(self, port):
        self._port = str(port)

    def create_pg_database_and_user(self, database, username=None):
        username = username or f'juju_{_get_random_string(16)}'
        password = _get_random_string(16)

        self._psql(f'CREATE DATABASE "{database}"')
        self._psql(f"CREATE USER \"{username}\" WITH ENCRYPTED PASSWORD '{password}'")
        self._psql(f'GRANT ALL PRIVILEGES ON DATABASE "{database}" TO "{username}"')

        return {
            'host': self._host,
            'port': self._port,
            'database': database,
            'user': username,
            'password': password,
            'master': build_connection_string(self._host, self._port, database, username, password),
        }

    def drop_pg_database(self, database):
        self._psql(f'DROP DATABASE "{database}"')

    def drop_pg_user(self, user):
        self._psql(f'DROP USER "{user}"')

    def get_version(self):
        if not self._version:
            resp = self._psql('SELECT version()').decode('utf-8')
            m = POSTGRESQL_VERSION_PATTERN.search(resp)
            if m:
                self._version = m.group(1)
        return self._version

    def configure_postgresql_server(self, port):
        self._update_postgresql_conf(port)
        self._update_pg_hba_conf()

    @staticmethod
    def restart_postgresql_server():
        service.restart('postgresql')

    def _psql(self, query):
        return tools.run('sudo', '-u', self._user, 'psql', '-p', self._port, '-c', f'{query}')

    def _update_postgresql_conf(self, port):
        config_path = self._get_pg_conf_file_path('postgresql.conf')
        juju_config_path = self._get_pg_etc_dir() / 'conf.d' / 'juju.conf'
        pg_config_lines = _extract_pg_conf_original_content(config_path)
        pg_config_lines = [line for line in pg_config_lines if not line.startswith('port =')]

        with config_path.open('w') as f:
            f.writelines(pg_config_lines)

        with juju_config_path.open('w') as f:
            _write_juju_config_section(f, ["listen_addresses = '*'\n", f'port = {port}\n'])

    def _update_pg_hba_conf(self):
        config_path = self._get_pg_conf_file_path('pg_hba.conf')
        pg_config_lines = _extract_pg_conf_original_content(config_path)

        with config_path.open('w') as f:
            f.writelines(pg_config_lines)
            _write_juju_config_section(f, ['host all all 0.0.0.0/0 md5\n'])

    def _get_pg_conf_file_path(self, name):
        return self._get_pg_etc_dir() / name

    def _get_pg_etc_dir(self):
        version = self.get_version()
        assert version
        major_version = version.split('.')[0]
        return POSTGRESQL_CONF_BASE_DIR / major_version / 'main'


def _get_random_string(length):
    letters = string.ascii_letters + string.digits
    return ''.join(random.choice(letters) for _ in range(length))


def build_connection_string(host, port, database, username, password):
    q = partial(quote, safe='')
    return f'dbname={q(database)} host={q(host)} password={q(password)} port={port} user={q(username)}'


def _extract_pg_conf_original_content(config_path):
    pg_config_lines = []
    juju_section = False
    with config_path.open() as f:
        for line in f:
            if line.startswith(POSTGRESQL_CONF_JUJU_START_MARK):
                juju_section = True
                continue
            elif line.startswith(POSTGRESQL_CONF_JUJU_END_MARK):
                juju_section = False
                continue
            if not juju_section:
                pg_config_lines.append(line)
    return pg_config_lines


def _write_juju_config_section(file_handle, lines):
    file_handle.write(f'{POSTGRESQL_CONF_JUJU_START_MARK}\n')
    file_handle.writelines(lines)
    file_handle.write(f'{POSTGRESQL_CONF_JUJU_END_MARK}\n')
