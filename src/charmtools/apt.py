from . import tools


def install(package):
    tools.run('apt-get', '--assume-yes', 'install', package)
