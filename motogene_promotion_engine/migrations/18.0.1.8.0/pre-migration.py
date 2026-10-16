"""Preserve the lifecycle of serials allocated by the previous DO workflow."""


def migrate(cr, version):
    # Rebuilds may upgrade directly from a version before this model existed.
    cr.execute("SELECT to_regclass(%s)", ["motogene_scratch_picking_card"])
    if not cr.fetchone()[0]:
        return
    cr.execute("ALTER TABLE motogene_scratch_picking_card ADD COLUMN IF NOT EXISTS state varchar")
    cr.execute("""
        UPDATE motogene_scratch_picking_card card
           SET state = CASE WHEN picking.state = 'done' THEN 'sent'
                            WHEN picking.state = 'cancel' THEN 'released'
                            ELSE 'reserved' END
          FROM stock_picking picking
         WHERE card.picking_id = picking.id AND card.state IS NULL
    """)
    cr.execute("UPDATE motogene_scratch_picking_card SET state = 'reserved' WHERE state IS NULL")
    cr.execute("ALTER TABLE motogene_scratch_picking_card DROP CONSTRAINT IF EXISTS motogene_scratch_picking_card_scratch_card_serial_unique")
