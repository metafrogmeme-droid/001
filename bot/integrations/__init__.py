"""Adapters that hand the bot's signals to something outside it.

Every adapter here is OFF unless its own flag is set, and none of them can
reach the order path: they read ideas the risk gate has already passed and
never import the executor or the engine.
"""
