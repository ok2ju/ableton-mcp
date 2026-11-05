"""AbletonMCP Remote Script. Live loads this package and calls create_instance()."""


def create_instance(c_instance):
    # Imported here so bridge.py and ops.py stay importable outside Live (tests).
    from .surface import AbletonMCP

    return AbletonMCP(c_instance)
