# Replace Scratch Card

## Courier recall and replacement shipment (18.0.1.22.1)

Use the Courier Recall situation when the original DO is completed, the parcel
is with the courier, recall has been requested and the customer has not received
it. **Replacement Delivery is optional**: leave it empty to retain the original
completed DO and update its serial/print; the replacement card remains Dispatched.
If using a separate delivery, select an unvalidated outgoing DO under the
**same SO and company**; the replacement card remains Allocated until validation.
This card workflow does not create a new DO or process product returns/stock.
For shipments on another SO, do not use this option: entitlement transfer between
different SOs is outside this workflow.

The original serial becomes **On Hold — Return Pending**, cannot be redeemed and
cannot be allocated. A same-prefix replacement stays on the original DO or moves
to the optionally selected replacement DO. When a separate DO is selected it is
Allocated, becoming Dispatched when that DO is validated. The source SO still
has one entitlement for that card, rather than two. Expiry is preserved.

On the original DO or SO, open **Card Replacement History** and use
**Receive / Release for Reuse** on the pending original serial. Choose:

- **Release Cards for Reuse** only after the physical original card is back in
  the warehouse, unscratched and in good condition. A Released history row makes
  that serial available to the ordinary allocator; Next Number is not rewound.
- **Permanently Block Original Card** when scratched, damaged or never recovered.
  Enter the inspection/reason. This cannot be undone through the reuse workflow.

The replacement card is not withdrawn when the original is received. Reconcile
the products separately through the ordinary inventory return workflow. Pending
originals are selected from replacement history, not Physical Cards Received:
they no longer have an active card row on the original delivery.

Staging tests: recall before receipt, replacement DO/print, failed redemption of
original, receipt without physical confirmations, good-card reuse without cursor
reset, scratched-card blocking, repeated confirmation, stock exhaustion, wrong
SO/company/return DO, and ordinary before-handover replacement regression.

## Before parcel handover

Operations can replace one allocated/dispatched card before parcel handover.
Select the card, reason and remarks, then confirm that the parcel has not been
handed to the courier/customer. The replacement uses the same actual prefix,
preferring an eligible Released serial before advancing the pool counter.
The existing card record, entitlement, expiry and delivery state are preserved.
History on the SO and DO records the blocked original serial and replacement.
Reprint Picking Operations copy(4) and change the physical card in the parcel.

An original serial in replacement history cannot be allocated or restored.
If a missing card is later found, quarantine it: it is permanently blocked.
The shortage alternative-type policy does not apply to this workflow.
No serial remaining means no replacement: the original card and counter stay
unchanged. Cards with any redemption history, a physical return or a reuse
release cannot use this workflow. Resolve an open redemption/return first.

## Staging checks

1. Replace a card on an unvalidated DO. Same prefix, new serial; state remains Allocated.
2. Replace a card on a validated DO whose parcel is still in the warehouse.
   State remains Dispatched, card count/expiry and product movements unchanged.
3. Check the SO, DO, replacement history and printed Picking Operations copy(4).
4. Attempt to allocate/redeem the original serial: it must be unavailable.
5. Submit an old popup again: it must reject the changed original serial.
6. Open a new popup to replace the replacement: separate history is retained.
7. Missing remarks/handover confirmation, wrong delivery, redemption history,
   pending physical return and exhausted pool must reject without partial changes.
8. An ordinary sales user without Inventory access must not replace cards.
9. Check company access and confirm Released serial reuse still works normally.

Odoo integration tests are in tests/test_scratch_card_replacement.py. Local
syntax/isolated reservation checks are not a substitute for staging validation.

## Record the earlier D8002 to D8005 shell replacement

Only after version 18.0.1.21.0 is deployed and upgraded on production, paste the
following into its Odoo Python shell. This adds history/blocks D8002 only: it
does not issue another card, advance a counter or change any delivery/stock.
The timestamp/user mean when the legacy entry was recorded; the original shell
replacement time remains in the earlier SO/DO chatter note.

```python
exec("""
env.cr.rollback()
env.invalidate_all()
try:
    assert env.cr.dbname == "percyianodoo-morimotoformulas1-main-9165386", "STOP: Wrong database."
    env.cr.execute("SET LOCAL lock_timeout = '5s'")
    Card = env["motogene.scratch.picking.card"].sudo()
    History = env["motogene.scratch.card.replacement"].sudo()
    card = Card.browse(10).exists()
    assert card and card.serial_number == "D8005", "STOP: Card changed."
    assert card.sale_id.id == 15279 and card.program_id.id == 24, "STOP: Source changed."
    assert card.picking_id.id == 20746 and card.picking_id.name == "WH/OUT/23478", "STOP: DO changed."
    existing = History.search([("old_serial", "=", "D8002")])
    if existing:
        assert len(existing) == 1 and existing.card_id == card and existing.new_serial == "D8005", "STOP: History differs."
        print("Already recorded. No additional replacement issued.")
    else:
        pool = card.program_id.scratch_serial_pool_ids.filtered(lambda p: p.prefix == "D")
        assert len(pool) == 1, "STOP: Pool mismatch."
        # Lock before taking the comparison snapshot: other SOs may allocate.
        card._lock_for_replacement()
        assert card.serial_number == "D8005", "STOP: Card changed while locking."
        before = pool.next_number
        history = card._record_legacy_replacement(
            "D8002", "damaged",
            "Original D8002 was already scratched before parcel handover. Replaced with D8005 through the earlier production shell correction; see the existing SO/DO note."
        )
        pool.invalidate_recordset(["next_number"])
        assert pool.next_number == before and card.serial_number == "D8005", "STOP: Unexpected allocation."
        env.cr.commit()
        print("Legacy history recorded: D8002 blocked, D8005 retained. Counter unchanged.")
except Exception:
    env.cr.rollback()
    env.invalidate_all()
    raise
finally:
    env.cr.rollback()
    env.invalidate_all()
""")
```
