# Payment service contract

This is a synthetic payment ledger, not a financial production system.
Amounts are positive integer cents. PAYMENT.POST calls PAYSTORE for balances
and receipts. PAYREPORT reads balances. The public result is `OK^balance`.
Invalid IDs, accounts and amounts return `ERROR:ID`, `ERROR:ACCOUNT`, or
`ERROR:AMOUNT`, respectively, without writes.

## Stored receipt schema

`^RPAY("receipt",id)` stores `account^amount^resulting_balance`.
A receipt captures the balance immediately after that payment. Later payments
can change the live balance without changing earlier receipts. `PAYSTORE.FIND`
returns an empty string for a missing receipt. Read the storage layer before
changing persistence. The database observable includes balances and receipts.

## Verification cases

The Python runner loads all routines into a private runtime directory. Each case
names a routine and extrinsic entry, string arguments, seed globals, expected
stdout, and the complete expected observable global state. Runtime errors, void
runs, restarts, wrong output, and unexpected global changes fail the case.
Every case is executed and rolled back through `clean_state()`.

Example of running an additional case suite:

```sh
python3 verify.py --cases feature-cases.json
```

## Extension ideas

Make POST safe to retry by payment ID; add a minimum-balance policy for refunds;
add an account statement query; add range-based balance reporting.
These are tasks for the coding agent, not features already implemented here.
