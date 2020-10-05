#! /usr/bin/env python3
# vim:fenc=utf-8
# Copyright © 2020 Marcin Bakowski marcin.bakowski@siriusxm.com

"""Operator Charm main library."""
# Load modules from lib directory
import json
import logging

from charmtools import apt
from charmtools import postgres as pg
from charmtools import tools
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.model import ActiveStatus, MaintenanceStatus

import setuppath  # noqa:F401


class PostgresqlCharm(CharmBase):
    """Class reprisenting this Operator charm."""

    state = StoredState()

    def __init__(self, *args):
        """Initialize charm and configure states and events to observe."""
        super().__init__(*args)
        # -- standard hook observation
        self.framework.observe(self.on.install, self.on_install)
        self.framework.observe(self.on.start, self.on_start)
        self.framework.observe(self.on.config_changed, self.on_config_changed)
        self.framework.observe(self.on.db_relation_changed, self.on_db_relation_changed)
        self.framework.observe(self.on.db_relation_joined, self.on_db_relation_changed)
        self.framework.observe(self.on.db_relation_departed, self.on_db_relation_departed)
        # -- initialize states --
        self.state.set_default(
            installed=False,
            configured=False,
            started=False,
            databases={},
            rel_db_map={},
            unit_ip_map={},
            pg_listen_port=5432,
            open_ports=[5432],
        )
        self.pg_service = pg.PGService(
            host=str(self.model.get_binding('db').network.bind_address), port=self.state.pg_listen_port
        )

    def on_install(self, event):
        """Handle install state."""
        self.unit.status = MaintenanceStatus('Installing charm software')
        apt.install('postgresql')
        self.unit.status = MaintenanceStatus('Install complete')
        logging.info('Install of software complete')
        self.state.installed = True

    def on_config_changed(self, event):
        """Handle config changed."""
        if not self.state.installed:
            logging.warning(f'Config changed called before install complete, deferring event: {event.handle}.')
            self._defer_once(event)

            return

        if self.state.started:
            # Stop if necessary for reconfig
            logging.info(f'Stopping for configuration, event handle: {event.handle}')
        # Configure the software
        logging.info('Configuring')
        if self.model.config['port'] != self.state.pg_listen_port:
            self.pg_service.configure_postgresql_server(self.model.config['port'])
            self.pg_service.restart_postgresql_server()
            self._update_listen_port()
            self._update_db_relations()
        self.state.configured = True

    def _update_listen_port(self):
        port = self.model.config['port']
        self.state.pg_listen_port = port
        self.pg_service.set_port(port)
        for p in self.state.open_ports:
            tools.close_port(p)
        self.state.open_ports = [port]
        tools.open_port(port)

    def on_start(self, event):
        """Handle start state."""
        if not self.state.configured:
            logging.warning(f'Start called before configuration complete, deferring event: {event.handle}')
            self._defer_once(event)

            return
        self.unit.status = MaintenanceStatus('Starting charm software')
        # Start software
        version = self.pg_service.get_version()
        self.unit.status = ActiveStatus(f'PostgreSQL {version} running')
        self.state.started = True
        logging.info('Started')

    def _defer_once(self, event):
        """Defer the given event, but only once."""
        notice_count = 0
        handle = str(event.handle)

        for event_path, _, _ in self.framework._storage.notices(None):
            if event_path.startswith(handle.split('[')[0]):
                notice_count += 1
                logging.debug(f'Found event: {event_path} x {notice_count}')

        if notice_count > 1:
            logging.debug(f'Not deferring {handle} notice count of {notice_count}')
        else:
            logging.debug(f'Deferring {handle} notice count of {notice_count}')
            event.defer()

    def on_db_relation_changed(self, event):
        if not self.model.unit.is_leader():
            logging.debug(f'Unit {self.model.unit.name} is not leader, skip further event processing')
            return
        if event.unit not in event.relation.data:
            return
        self._update_db_relation(event.relation, event.unit)

    def on_db_relation_departed(self, event):
        if not self.model.unit.is_leader():
            logging.debug(f'Unit {self.model.unit.name} is not leader, skip further event processing')
            return
        logging.debug(f'DATABASE DEPARTED: {event.relation} {event.relation.data} {event.relation.units}')
        data = event.relation.data[self.model.unit]
        logging.debug(f'DATABASE DEPARTED: {data}')
        database = self.state.rel_db_map.get(event.relation.id) or data.get('database')
        logging.debug(f'DATABASE DEPARTED: {database} {dict(self.state.rel_db_map)}')

        if database in self.state.databases:
            # check if database is shared between various relations/apps
            is_database_in_use = bool(
                list(
                    db for rel_id, db in self.state.rel_db_map.items() if db == database and rel_id != event.relation.id
                )
            )
            if not is_database_in_use and not event.relation.units:
                logging.debug(f'DROPPING DATABASE: {database} {is_database_in_use} {event.relation.units}')
                db_data = json.loads(self.state.databases.pop(database))
                self.pg_service.drop_pg_database(database)
                self.pg_service.drop_pg_user(db_data['user'])
                self.state.rel_db_map.pop(event.relation.id, None)

    def _update_db_relations(self):
        if not self.model.unit.is_leader():
            logging.debug(f'Unit {self.model.unit.name} is not leader, skip updating db relations')
            return

        self._update_port_in_state_databases()
        for db_relation in self.model.relations['db']:
            logging.debug(f'UPDATE RELATION: {db_relation}')
            for unit in db_relation.units:
                self._update_db_relation(db_relation, unit)

    def _update_port_in_state_databases(self):
        port = self.model.config['port']
        for database, db_data in self.state.databases.items():
            db_data = json.loads(db_data)
            db_data.update(
                {
                    'port': str(port),
                    'master': pg.build_connection_string(
                        db_data['host'], port, database, db_data['user'], db_data['password']
                    ),
                }
            )
            self.state.databases[database] = json.dumps(db_data)

    def _update_db_relation(self, relation, unit):
        data = relation.data[unit]
        logging.debug(f'DATABASE CHANGED: {data}')
        database = data.get('database')
        if not database:
            logging.debug('No database name provided, skip further event processing')
            return

        if database not in self.state.databases:
            database_credentials = self.pg_service.create_pg_database_and_user(database)
            self.state.databases[database] = json.dumps(database_credentials)
        else:
            database_credentials = json.loads(self.state.databases[database])

        self.state.unit_ip_map[unit.name] = ','.join(tools.incoming_addresses(data))
        self.state.rel_db_map[relation.id] = database

        self._set_pg_properties(relation, database_credentials)
        self._set_extra_pg_properties(relation, unit)

        logging.debug(f'DATABASE CHANGED: {dict(self.state.rel_db_map)}')
        logging.debug(f'DATABASE CHANGED: {dict(relation.data[self.model.unit])}')

    def _set_pg_properties(self, relation, database_credentials):
        logging.debug(database_credentials)
        for k, v in database_credentials.items():
            relation.data[self.model.unit][k] = v

    def _set_extra_pg_properties(self, relation, unit):
        data = relation.data[unit]
        self.state.unit_ip_map[unit.name] = ','.join(tools.incoming_addresses(data))

        allowed_units, allowed_subnets = [], []
        for unit in relation.units:
            allowed_units.append(unit.name)
            unit_ips = self.state.unit_ip_map.get(unit.name)
            if unit_ips:
                allowed_subnets.append(unit_ips)
        relation.data[self.model.unit]['allowed-units'] = ','.join(allowed_units)
        relation.data[self.model.unit]['allowed-subnets'] = ','.join(allowed_subnets)
        # TODO: create roles and extensions
        relation.data[self.model.unit]['roles'] = data.get('roles', '')
        relation.data[self.model.unit]['extensions'] = data.get('extensions', '')


if __name__ == '__main__':
    from ops.main import main

    main(PostgresqlCharm)
