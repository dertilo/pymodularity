# G7: looks a handler up by an env-var-provided string key, never by
# import -- no edge to producer.py.
import os


def dispatch():
    handler_name = os.environ.get("BILLING_HANDLER", "invoice-discount")
    return handler_name
