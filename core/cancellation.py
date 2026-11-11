"""Cooperative cancellation; never terminate a Qt thread during file/network I/O."""
class OperationCancelled(Exception):
    pass


def check_cancelled(should_stop=None):
    if should_stop is not None and should_stop():
        raise OperationCancelled()
