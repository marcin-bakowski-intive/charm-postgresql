import subprocess


def run(*args):
    return subprocess.check_output([str(arg) for arg in args])
