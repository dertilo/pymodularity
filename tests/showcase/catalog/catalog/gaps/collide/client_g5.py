# G5: this import means the third-party `requests` library. A local
# folder of the same name sits right next to it (`collide/requests`),
# but the tool only resolves absolute imports through package roots, so
# it correctly treats `requests` as unresolved (third-party) here --
# no edge to the local folder, no finding.
import requests


def use():
    return requests._queue.PENDING
