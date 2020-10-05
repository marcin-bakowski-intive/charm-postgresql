import inspect
import logging
import os
from pathlib import Path
import re
import shutil
from unittest import mock

from charmtools import postgres
from ops.testing import Harness
import pytest
import yaml

from .base import create_db_relation


@pytest.fixture
def charm_class():
    from charm import PostgresqlCharm

    return PostgresqlCharm


@pytest.fixture
def charm_dir(charm_class):
    return Path(inspect.getfile(charm_class)).parent.parent


@pytest.fixture
def pg_unit_ip():
    return '10.216.12.252'


@pytest.fixture
def model_network(pg_unit_ip):
    network = re.sub(r'\.\d+$', '', pg_unit_ip)
    return {
        'bind-addresses': [
            {
                'mac-address': '00:00:00:00:00:00',
                'interface-name': 'eth0',
                'addresses': [{'hostname': 'unit-test', 'value': pg_unit_ip, 'cidr': f'{network}.0/24'}],
            }
        ],
        'egress-subnets': [f'{pg_unit_ip}/32'],
        'ingress-addresses': [pg_unit_ip],
    }


@pytest.fixture
def db_rel_request():
    return {
        'egress-subnets': '10.216.12.86/32',
        'ingress-address': '10.216.12.86',
        'private-address': '10.216.12.86',
        'database': 'fermi_dev_db',
    }


@pytest.fixture
def postgresql_package_installed(fake_process):
    cmd = ['apt-get', '--assume-yes', 'install', 'postgresql']
    fake_process.register_subprocess(cmd)
    return cmd


@pytest.fixture
def pg_version():
    return '10.14'


@pytest.fixture
def pg_version_resp(pg_version):
    return f"""
             version
--------------------------------
 PostgreSQL {pg_version} (Ubuntu {pg_version}-0ubuntu0.18.04.1) on x86_64-pc-linux-gnu
(1 row)
""".encode()


@pytest.fixture
def pg_main_dir():
    fixtures_dir = Path(os.path.dirname(__file__)) / 'fixtures'
    old_value = postgres.POSTGRESQL_CONF_BASE_DIR
    postgres.POSTGRESQL_CONF_BASE_DIR = fixtures_dir
    pg_main_dir = fixtures_dir / '10' / 'main'
    (pg_main_dir / 'conf.d').mkdir(mode=0o644, exist_ok=True)
    shutil.copyfile(pg_main_dir / 'postgresql.conf.tpl', pg_main_dir / 'postgresql.conf')
    shutil.copyfile(pg_main_dir / 'pg_hba.conf.tpl', pg_main_dir / 'pg_hba.conf')
    shutil.rmtree(pg_main_dir / 'conf.d' / 'juju.conf', ignore_errors=True)
    yield pg_main_dir
    postgres.POSTGRESQL_CONF_BASE_DIR = old_value


@pytest.fixture
def harness(charm_class, charm_dir, model_network):
    harness = Harness(charm_class)
    config_yaml = charm_dir / 'config.yaml'
    logging.debug(f'checking for {config_yaml}')
    if config_yaml.exists():
        logging.debug(f'loading defaults from {config_yaml}')
        harness.disable_hooks()
        data = yaml.safe_load(config_yaml.open())
        defaults = {key: record.get('default') for key, record in data['options'].items()}
        harness.update_config(defaults)
        harness.enable_hooks()
    harness.set_leader(True)
    with mock.patch.object(harness._backend, 'network_get', return_value=model_network):
        yield harness


@pytest.fixture
def app_name():
    return 'fermi'


@pytest.fixture
def unit_name(app_name):
    return f'{app_name}/0'


@pytest.fixture
def app(harness, app_name):
    return harness.model.get_app(app_name)


@pytest.fixture
def unit(harness, unit_name):
    return harness.model.get_unit(unit_name)


@pytest.fixture
def db_relation(harness, app, unit, db_rel_request):
    return create_db_relation(harness, app.name, unit.name, db_rel_request)
