import sys

import remvalgate


def test_package_imports():
    assert remvalgate.__version__


def test_python_within_aiopslab_range():
    # AIOpsLab supports Python 3.11 and 3.12 only
    assert (3, 11) <= sys.version_info[:2] < (3, 13)
