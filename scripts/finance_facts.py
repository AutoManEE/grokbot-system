#!/usr/bin/env python3
"""finance_facts.py: money math in code, so assistants and workers only quote numbers. Zero AI; reads nothing.

The finance bot's delegated tasks never ask a model to compute money. flow.py calls calc() with the task's
money_inputs and hands the resulting FACTS to the S-suite; the assistant's report may only contain money numbers
that are task inputs or these facts (checked in code by flow.money_numbers_ok).
"""


def calc(unit_price, quantity, fee_rate=0.0, budget=None):
    """Subtotal, fees and total for a purchase-like item, rounded to cents; remaining budget when one is given."""
    subtotal = unit_price * quantity
    fees = subtotal * fee_rate
    out = dict(subtotal=round(subtotal, 2), fees=round(fees, 2), total=round(subtotal + fees, 2))
    if budget is not None:
        out["remaining_budget"] = round(budget - subtotal - fees, 2)
    return out
