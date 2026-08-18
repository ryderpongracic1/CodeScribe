# decorators.py — tests decorated + async defs
import asyncio
from functools import wraps


def my_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper


@my_decorator
def decorated_func(x):
    return x + 1


@my_decorator
async def async_decorated(y):
    # async internal comment
    await asyncio.sleep(0)
    return y * 2


async def plain_async(z):
    """Old async docstring."""
    return z - 1
