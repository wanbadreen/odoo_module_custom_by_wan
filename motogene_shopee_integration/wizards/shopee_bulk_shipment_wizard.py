from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class ShopeeBulkShipmentWizard(models.TransientModel):
    _name = "motogene.shopee.bulk.shipment.wizard"
    _description = "Queue Shopee Sandbox Drop-off Shipments"

    picking_ids = fields.Many2many("stock.picking", readonly=True)
    picking_count = fields.Integer(compute="_compute_picking_count")
    note = fields.Text(readonly=True, default=lambda self: _(
        "Sandbox test only. CONFIRM queues eligible unarranged Shopee DOs for "
        "Drop-off; a separate scheduled action checks live Shopee eligibility "
        "before each shipment request. Pickup-only orders, orders needing "
        "extra drop-off details and uncertain requests are NOT arranged in bulk. "
        "Maximum 20 DO per batch. Do not confirm twice."
    ))

    @api.depends("picking_ids")
    def _compute_picking_count(self):
        for wizard in self:
            wizard.picking_count = len(wizard.picking_ids)

    def action_queue_dropoff(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Shopee bulk preparation is restricted to administrators during sandbox testing."))
        picks = self.picking_ids
        if not picks or len(picks) > 20:
            raise UserError(_("Select between 1 and 20 Delivery Orders."))
        queued, skipped = [], []
        for picking in picks.sorted("id"):
            # Never queue processed, cancelled, non-Shopee or unknown orders.
            if (not picking.motogene_shopee_is_delivery
                    or picking.state not in ("confirmed", "assigned", "waiting")
                    or picking.motogene_shopee_shipment_state != "not_sent"
                    or picking.motogene_shopee_bulk_state in ("queued", "running")
                    or picking.motogene_shopee_awb_attachment_id
                    or not picking.sale_id.motogene_shopee_order_ref
                    or not picking.sale_id.motogene_shopee_preview_id
                    or picking.sale_id.motogene_shopee_preview_id.shop_id.environment != "sandbox"
                    or picking.sale_id.company_id != picking.company_id):
                skipped.append(picking.name)
                continue
            self.env.cr.execute(
                "SELECT id FROM stock_picking WHERE id = %s FOR UPDATE NOWAIT",
                [picking.id],
            )
            picking.invalidate_recordset()
            if (picking.motogene_shopee_shipment_state != "not_sent"
                    or picking.motogene_shopee_bulk_state in ("queued", "running")):
                skipped.append(picking.name)
                continue
            picking.write({
                "motogene_shopee_bulk_state": "queued",
                "motogene_shopee_bulk_note": "Queued by %s for sandbox bulk drop-off" % self.env.user.name,
            })
            queued.append(picking.name)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Shopee Bulk Drop-off Queue"),
                "message": _("%s queued; %s skipped. Check bulk status on each DO. The scheduler processes up to 3 queued DOs per run.") % (
                    len(queued), len(skipped)
                ),
                "type": "success" if queued else "warning",
                "sticky": True,
            },
        }
