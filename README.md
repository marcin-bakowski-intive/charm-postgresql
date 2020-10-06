# postgresql Charm

Overview
--------

PostgreSQL server charm.

Quickstart
----------

Build charm:
```
$ tox -e charm
```

Deploy charm on existing juju model:
```
$ juju deploy ./postgresql.charm
```

Add `db` relation between posgresql and django juju apps:
```
$ juju relate django-app posgresql
```

Contact
-------
 - Author: Marcin Bąkowski <marcin.bakowski@siriusxm.com>
 - Bug Tracker: [here](https://discourse.juju.is/c/charming)
