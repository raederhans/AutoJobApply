"""Shared contracts that depend only on the Python standard library.

Storage, apply and career code all exchange these value objects.  Keeping them
in a leaf package lets lower layers such as ``applypilot.storage`` use them
without importing the higher-level apply or career workflows.
"""
