from functools import partial
import subprocess


def run(*args):
    return subprocess.check_output([str(arg) for arg in args])


def _modify_port(hook_tool, start=None, end=None, protocol='tcp'):
    assert protocol in {'tcp', 'udp', 'icmp'}
    if protocol == 'icmp':
        arg = protocol
    else:
        port_range = f'{start}-{end}' if end else start
        arg = f'{port_range}/{protocol}'
    run(hook_tool, arg)


open_port = partial(_modify_port, 'open-port')
close_port = partial(_modify_port, 'close-port')
