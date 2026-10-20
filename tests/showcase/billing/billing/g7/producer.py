# G7: registers itself under a string key; no import links it to its
# consumer, so this coupling stays invisible to the import graph -- no edge.
REGISTRY: dict[str, str] = {"invoice-discount": "producer.apply"}
