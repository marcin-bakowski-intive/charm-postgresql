from functools import partial
import ipaddress
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
