import subprocess


def run(*args):
    subprocess.check_output([str(arg) for arg in args])
