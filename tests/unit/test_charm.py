from unittest import mock

import pytest

from .base import create_db_relation


@pytest.fixture(autouse=True)
def mock_pg_version(fake_process, pg_version_resp):
    _register_query(fake_process, 'SELECT version()', stdout=pg_version_resp)


@pytest.fixture
def random_string():
    random_str = 'HH88buR4'
    with mock.patch('charmtools.postgres._get_random_string', return_value=random_str):
        yield random_str


def assert_pg_configs(pg_main_dir, port):
    postgresql_conf = _read_content(pg_main_dir / 'postgresql.conf')
    pg_hba_conf = _read_content(pg_main_dir / 'pg_hba.conf')
    juju_conf = _read_content(pg_main_dir / 'conf.d' / 'juju.conf')
    assert 'port = ' not in postgresql_conf
    assert 'host all all 0.0.0.0/0 md5' in pg_hba_conf
    assert "listen_addresses = '*'" in juju_conf
    assert f'port = {port}' in juju_conf


def assert_db_relation_data(rel_data, database, app_units, egress_subnets, random_string, pg_unit_ip, port=5432):
    assert rel_data['host'] == pg_unit_ip
    assert rel_data['port'] == str(port)
    assert rel_data['database'] == database
    assert rel_data['user'] == f'juju_{random_string}'
    assert rel_data['password'] == random_string
    assert rel_data['master'] == (
        f'dbname={database} host={pg_unit_ip} password={random_string} port={port} user=juju_{random_string}'
    )
    assert rel_data['allowed-units'] == ','.join(app_units)
    assert rel_data['allowed-subnets'] == ','.join(egress_subnets)


def test_create_charm(harness):
    harness.begin()

    assert not harness.charm.state.installed
    assert not harness.charm.state.configured
    assert not harness.charm.state.started
    assert harness.charm.state.databases == {}
    assert harness.charm.state.rel_db_map == {}
    assert harness.charm.state.unit_ip_map == {}
    assert harness.charm.state.pg_listen_port == 5432
    assert harness.charm.state.open_ports == [5432]


def test_install(harness, fake_process, postgresql_package_installed):
    harness.begin()
    harness.charm.on.install.emit()

    assert harness.charm.state.installed
    assert fake_process.call_count(postgresql_package_installed) == 1


def test_config_changed(
    harness, db_relation, unit, pg_unit_ip, db_rel_request, pg_version_resp, fake_process, random_string, pg_main_dir
):
    curr_port, new_port = 5432, 5555
    database = db_rel_request['database']
    _register_query(fake_process, 'SELECT version()', stdout=pg_version_resp, port=str(new_port))
    _mock_pg_database_and_user_psql_calls(fake_process, database, random_string, new_port)
    harness.begin()
    harness.charm.state.installed = True

    def _run_test(curr_port, new_port):
        fake_process.register_subprocess(['close-port', f'{curr_port}/tcp'])
        fake_process.register_subprocess(['open-port', f'{new_port}/tcp'])
        fake_process.register_subprocess(['systemctl', 'restart', 'postgresql'])

        harness.update_config({'port': new_port})

        assert harness.charm.state.configured
        assert not harness.charm.state.started
        assert_pg_configs(pg_main_dir, new_port)
        rel_data = harness.model.get_relation(db_relation.name, db_relation.id).data[harness.model.unit]
        assert_db_relation_data(
            rel_data, database, [unit.name], [db_rel_request['egress-subnets']], random_string, pg_unit_ip, new_port
        )
        return new_port, curr_port

    # change port: 5432 -> 5555
    curr_port, new_port = _run_test(curr_port, new_port)
    # change port to default: 5555 -> 5432
    _run_test(curr_port, new_port)


def test_start(harness, pg_version):
    """Test start PostgreSQL."""
    harness.begin()
    harness.charm.state.configured = True
    harness.charm.on.start.emit()

    assert harness.charm.state.started
    assert harness.charm.unit.status.name == 'active'
    assert harness.charm.unit.status.message == f'PostgreSQL {pg_version} running'


def test_db_relation_joined(harness, app, unit, db_rel_request):
    # db relation joined doesn't provide database
    db_rel_request.pop('database')
    relation = create_db_relation(harness, app.name, unit.name, db_rel_request)
    harness.begin()

    harness.charm.on.db_relation_joined.emit(relation, app, unit)

    rel_data = harness.model.get_relation(relation.name, relation.id).data[harness.model.unit]
    # relation data is empty until database is provided
    assert dict(rel_data) == {}


def test_db_relation_changed(harness, db_relation, app, unit, pg_unit_ip, db_rel_request, fake_process, random_string):
    database = db_rel_request['database']
    egress = db_rel_request['egress-subnets']
    create_db_call, create_user_call, grant_user_call = _mock_pg_database_and_user_psql_calls(
        fake_process, database, random_string
    )
    harness.begin()

    harness.charm.on.db_relation_changed.emit(db_relation, app, unit)

    rel_data = harness.model.get_relation(db_relation.name, db_relation.id).data[harness.model.unit]
    assert_db_relation_data(rel_data, database, [unit.name], [egress], random_string, pg_unit_ip)
    assert database in harness.charm.state.databases
    assert fake_process.call_count(create_db_call) == 1
    assert fake_process.call_count(create_user_call) == 1
    assert fake_process.call_count(grant_user_call) == 1

    # add another unit to existing db relation
    app_unit_1 = f'{app.name}/1'
    unit_1 = harness.model.get_unit(app_unit_1)
    harness.add_relation_unit(db_relation.id, app_unit_1)
    harness.update_relation_data(db_relation.id, app_unit_1, db_rel_request)

    harness.charm.on.db_relation_changed.emit(db_relation, app, unit_1)

    db_relation = harness.model.get_relation(db_relation.name, db_relation.id)
    rel_data = db_relation.data[harness.model.unit]
    assert_db_relation_data(
        rel_data, database, [unit.name for unit in db_relation.units], [egress, egress], random_string, pg_unit_ip
    )
    # assert there was no new SQL queries to create db/user
    assert fake_process.call_count(create_db_call) == 1
    assert fake_process.call_count(create_user_call) == 1
    assert fake_process.call_count(grant_user_call) == 1


def test_db_relation_changed_unit_is_not_leader(harness, db_relation, app, unit):
    harness.set_leader(False)
    harness.begin()

    harness.charm.on.db_relation_changed.emit(db_relation, app, unit)

    rel_data = harness.model.get_relation(db_relation.name, db_relation.id).data[harness.model.unit]
    # only postgresql leader sets relation data (creates database and user)
    assert dict(rel_data) == {}


def test_db_relation_departed(harness, db_relation, app, unit, pg_unit_ip, db_rel_request, fake_process, random_string):
    database = db_rel_request['database']
    egress = db_rel_request['egress-subnets']
    _mock_pg_database_and_user_psql_calls(fake_process, database, random_string)
    harness.begin()

    # create db relation first
    harness.charm.on.db_relation_changed.emit(db_relation, app, unit)

    relation = harness.model.get_relation(db_relation.name, db_relation.id)
    rel_data = relation.data[harness.model.unit]
    assert_db_relation_data(rel_data, database, [unit.name], [egress], random_string, pg_unit_ip)

    # remove unit and emit db-relation-departed event
    relation.units.remove(unit)
    drop_db_call = _register_query(fake_process, f'DROP DATABASE "{database}"')
    drop_user_call = _register_query(fake_process, f'DROP USER "juju_{random_string}"')
    harness.charm.on.db_relation_departed.emit(relation, app, unit)

    assert database not in harness.charm.state.databases
    assert fake_process.call_count(drop_db_call) == 1
    assert fake_process.call_count(drop_user_call) == 1


def _mock_pg_database_and_user_psql_calls(fake_process, database, random_string, port=5432):
    return [
        _register_query(fake_process, f'CREATE DATABASE "{database}"', port=str(port)),
        _register_query(
            fake_process,
            f'CREATE USER "juju_{random_string}" WITH ENCRYPTED PASSWORD \'{random_string}\'',
            port=str(port),
        ),
        _register_query(
            fake_process, f'GRANT ALL PRIVILEGES ON DATABASE "{database}" TO "juju_{random_string}"', port=str(port)
        ),
    ]


def _register_query(fake_process, query, stdout=None, port='5432'):
    cmd = ['sudo', '-u', 'postgres', 'psql', '-p', port, '-c', f'{query}']
    fake_process.register_subprocess(cmd, stdout=stdout)
    return cmd


def _read_content(path):
    with path.open() as f:
        return f.read()
