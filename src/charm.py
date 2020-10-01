#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# vim:fenc=utf-8
# Copyright © 2020 Marcin Bakowski marcin.bakowski@siriusxm.com

"""Operator Charm main library."""
# Load modules from lib directory
import json
import logging

import setuppath  # noqa:F401
from charmtools import apt
from charmtools import postgres as pg
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.model import ActiveStatus, MaintenanceStatus


class PostgresqlCharm(CharmBase):
    """Class reprisenting this Operator charm."""

    state = StoredState()

    def __init__(self, *args):
        """Initialize charm and configure states and events to observe."""
        super().__init__(*args)
        # -- standard hook observation
        self.framework.observe(self.on.install, self.on_install)
        self.framework.observe(self.on.start, self.on_start)
        self.framework.observe(self.on.db_relation_changed, self.on_db_relation_changed)
        self.framework.observe(self.on.db_relation_joined, self.on_db_relation_changed)
        self.framework.observe(self.on.db_relation_departed, self.on_db_relation_departed)
        # -- initialize states --
        self.state.set_default(
            installed=False, configured=False, started=False, databases={}, rel_db_map={}, unit_ip_map={}
        )

    def on_install(self, event):
        """Handle install state."""
        self.unit.status = MaintenanceStatus("Installing charm software")
        apt.install('postgresql')
        self.unit.status = MaintenanceStatus("Install complete")
        logging.info("Install of software complete")
        self.state.installed = True

    def on_start(self, event):
        """Handle start state."""
        if not self.state.configured:
            logging.warning("Start called before configuration complete, deferring event: {}".format(event.handle))
            self._defer_once(event)

            return
        self.unit.status = MaintenanceStatus("Starting charm software")
        # Start software
        self.unit.status = ActiveStatus("Unit is ready")
        self.state.started = True
        logging.info("Started")

    def _defer_once(self, event):
        """Defer the given event, but only once."""
        notice_count = 0
        handle = str(event.handle)

        for event_path, _, _ in self.framework._storage.notices(None):
            if event_path.startswith(handle.split('[')[0]):
                notice_count += 1
                logging.debug("Found event: {} x {}".format(event_path, notice_count))

        if notice_count > 1:
            logging.debug("Not deferring {} notice count of {}".format(handle, notice_count))
        else:
            logging.debug("Deferring {} notice count of {}".format(handle, notice_count))
            event.defer()

    def on_db_relation_changed(self, event):
        if event.unit not in event.relation.data:
            return

        data = event.relation.data[event.unit]
        logging.debug(f'DATABASE CHANGED: {data}')
        database = data.get('database')
        if not database:
            return

        if database not in self.state.databases:
            network = self.model.get_binding(event.relation).network
            host = str(network.bind_address)
            database_credentials = pg.create_pg_database_and_user(host, '5432', database)
            self.state.databases[database] = json.dumps(database_credentials)
        else:
            database_credentials = json.loads(self.state.databases[database])

        self.state.unit_ip_map[event.unit.name] = ','.join(pg.incoming_addresses(data))
        self.state.rel_db_map[event.relation.id] = database

        self._set_pg_properties(event, database_credentials)
        self._set_extra_pg_properties(event)

        logging.debug(f'DATABASE CHANGED: {self.state.rel_db_map}')
        logging.debug(f'DATABASE CHANGED: {dict(event.relation.data[self.model.unit])}')

    def _set_pg_properties(self, event, database_credentials):
        logging.debug(database_credentials)
        for k, v in database_credentials.items():
            event.relation.data[self.model.unit][k] = v

    def _set_extra_pg_properties(self, event):
        data = event.relation.data[event.unit]
        self.state.unit_ip_map[event.unit.name] = ','.join(pg.incoming_addresses(data))

        allowed_units, allowed_subnets = [], []
        for unit in event.relation.units:
            allowed_units.append(unit.name)
            unit_ips = self.state.unit_ip_map.get(unit.name)
            if unit_ips:
                allowed_subnets.append(unit_ips)
        event.relation.data[self.model.unit]['allowed-units'] = ','.join(allowed_units)
        event.relation.data[self.model.unit]['allowed-subnets'] = ','.join(allowed_subnets)
        # TODO: create roles and extensions
        event.relation.data[self.model.unit]['roles'] = data.get('roles', '')
        event.relation.data[self.model.unit]['extensions'] = data.get('extensions', '')

    def on_db_relation_departed(self, event):
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
                pg.drop_pg_database(database)
                pg.drop_pg_user(db_data['user'])
                self.state.rel_db_map.pop(event.relation.id, None)


if __name__ == "__main__":
    from ops.main import main

    main(PostgresqlCharm)
