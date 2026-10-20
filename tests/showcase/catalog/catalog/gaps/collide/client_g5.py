# G5: this import means the third-party `requests` library, but a local
# folder of the same name shadows it, drawing a false edge -- surfaced here
# as a false private-item finding (via attribute access) on the local
# `collide/requests` package, not the real PyPI one.
import requests


def use():
    return requests._queue.PENDING
