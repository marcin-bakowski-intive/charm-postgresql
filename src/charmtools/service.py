from functools import partial

from . import tools


def _service(action, service):
    return tools.run('systemctl', action, service)


start = partial(_service, 'start')
stop = partial(_service, 'stop')
restart = partial(_service, 'restart')
