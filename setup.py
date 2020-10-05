from pathlib import Path

from setuptools import find_packages, setup

package = 'charm-postgresql'
version = '0.1'

setup(
    name=package,
    version=version,
    description='Juju PostgreSQL charm',
    packages=find_packages('src'),
    package_dir={'': 'src'},
    py_modules=[path.stem for path in Path('src').glob('*.py')],
)
