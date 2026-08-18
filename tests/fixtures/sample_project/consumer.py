# consumer.py — imports base module
from . import base


def use_base(val):
    return base.base_helper(val) + 1
