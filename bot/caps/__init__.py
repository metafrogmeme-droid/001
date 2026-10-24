"""Shared cap rules.

CGC, RXB, T2C and HRC can each lower a cap on the same vault. Batch 3
is built RXB, then HRC, then T2C. The rule they share lives in
``strictest``: the minimum of the proposals that exist, and a source
may only lower its own.
"""
