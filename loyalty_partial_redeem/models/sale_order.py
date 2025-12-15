import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    has_loyalty_redeem = fields.Boolean(
        string="Has Loyalty Redemption",
        compute="_compute_has_loyalty_redeem",
        store=True,
    )
    loyalty_points_redeemed = fields.Float(string="Loyalty Points Redeemed", default=0.0)
    loyalty_card_id = fields.Many2one("loyalty.card", string="Loyalty Card")
    loyalty_redeem_reversed = fields.Boolean(string="Loyalty Redemption Reversed", default=False)

    @api.depends("order_line", "order_line.is_loyalty_redeem_line", "order_line.product_id")
    def _compute_has_loyalty_redeem(self):
        for order in self:
            order.has_loyalty_redeem = bool(order._get_loyalty_redeem_lines())

    @staticmethod
    def _is_redeem_line(line):
        default_code = (line.product_id.default_code or "").strip().lower()
        product_name = (line.product_id.name or "").strip().lower()
        return bool(
            line.is_loyalty_redeem_line
            or default_code == "loyalty point redemption"
            or product_name in {"loyalty point redemption", "loyalty points redemption"}
        )

    def _get_loyalty_redeem_lines(self):
        self.ensure_one()
        return self.order_line.filtered(self._is_redeem_line)

    def _get_redeemed_points_value(self):
        self.ensure_one()
        if self.loyalty_points_redeemed:
            return self.loyalty_points_redeemed
        history = self._get_loyalty_history_records()
        return sum(history.mapped("used"))

    @staticmethod
    def _is_reversal_history_line(history_line):
        description = (history_line.description or "").lower()
        return "reverse redemption" in description

    def _log_history_records(self, label, records):
        if not _logger.isEnabledFor(logging.DEBUG):
            return
        message = [
            (
                rec.id,
                rec.description,
                rec.used,
                rec.issued,
                rec.order_id.id,
                getattr(rec, "order_model", False),
            )
            for rec in records
        ]
        _logger.debug("Loyalty history for %s (%s): %s", self.name, label, message)

    def _get_loyalty_history_records(self):
        self.ensure_one()
        History = self.env["loyalty.history"]
        history_fields = History._fields

        def _search_history(domain, label):
            records = History.search(domain)
            if records:
                self._log_history_records(label, records)
            return records

        card_domain = [("card_id", "=", self.loyalty_card_id.id)] if self.loyalty_card_id else []
        order_model_domain = []
        if "order_model" in history_fields:
            order_model_domain = ["|", ("order_model", "=", "sale.order"), ("order_model", "=", False)]
        history_records = _search_history([
            ("order_id", "=", self.id),
        ] + order_model_domain, "primary (order_id)")

        non_reversal_history = history_records.filtered(lambda h: not self._is_reversal_history_line(h))

        if not non_reversal_history.filtered(lambda h: h.issued > 0):
            issued_domain = [
                ("issued", ">", 0),
                ("description", "ilike", f"order {self.name}"),
            ] + card_domain + order_model_domain
            history_records |= _search_history(issued_domain, "fallback issued (description)")

        if not non_reversal_history.filtered(lambda h: h.used > 0):
            used_domain = [("used", ">", 0), ("description", "ilike", self.name)] + card_domain + order_model_domain
            history_records |= _search_history(used_domain, "fallback used (description)")

        history_records = history_records.filtered(lambda h: not self._is_reversal_history_line(h))
        self._log_history_records("combined", history_records)
        return history_records

    def _find_loyalty_card_from_history(self):
        self.ensure_one()
        history = self._get_loyalty_history_records()
        card = history.filtered(lambda rec: rec.card_id)[:1].card_id
        return card

    def _has_reversal_history(self):
        self.ensure_one()
        History = self.env["loyalty.history"]
        history_fields = History._fields
        domain = [
            ("description", "ilike", "reverse redemption"),
            ("order_id", "=", self.id),
        ]
        if self.loyalty_card_id:
            domain.append(("card_id", "=", self.loyalty_card_id.id))
        if "order_model" in history_fields:
            domain = domain + ["|", ("order_model", "=", "sale.order"), ("order_model", "=", False)]
        has_reversal = bool(History.search(domain, limit=1))
        if has_reversal:
            _logger.debug("Reversal history already present for %s", self.name)
        return has_reversal

    def action_open_loyalty_redeem_wizard(self):
        self.ensure_one()
        if self._get_loyalty_redeem_lines():
            raise UserError(_("This order already has a loyalty redemption. Remove the redemption line first."))
        program = self.env["loyalty.program"].search([
            ("program_type", "=", "loyalty"),
            ("active", "=", True),
        ], limit=1)

        if not program:
            raise UserError(_("No active loyalty program found."))

        card = self.env["loyalty.card"].search([
            ("program_id", "=", program.id),
            ("partner_id", "=", self.partner_id.id),
        ], limit=1)

        if not card or card.points <= 0:
            raise UserError(_("This customer has no loyalty points."))

        return {
            "name": _("Redeem Loyalty Points"),
            "type": "ir.actions.act_window",
            "res_model": "loyalty.partial.redeem.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_sale_order_id": self.id,
                "default_loyalty_card_id": card.id,
                "default_available_points": card.points,
            }
        }

    def action_cancel(self):
        res = super().action_cancel()
        self._reverse_loyalty_points_on_cancel()
        return res

    def _reverse_loyalty_points_on_cancel(self):
        for order in self:
            redeem_lines = order._get_loyalty_redeem_lines()
            history_records = order._get_loyalty_history_records()
            if order._has_reversal_history():
                order.loyalty_redeem_reversed = True
                continue
            if order.loyalty_redeem_reversed:
                continue

            if not redeem_lines and not history_records:
                continue

            used_points = sum(history_records.mapped("used"))
            issued_points = sum(history_records.mapped("issued"))

            if used_points == 0 and issued_points == 0:
                _logger.debug("No loyalty usage or issuance detected for order %s", order.name)
                continue

            card = order.loyalty_card_id or order._find_loyalty_card_from_history()
            if not card:
                _logger.warning("Cannot reverse loyalty redemption for order %s: no card found", order.name)
                continue

            net_adjustment = used_points - issued_points
            card.points += net_adjustment

            description = _(
                "Reverse Redemption & Issued (SO Cancelled): %(order)s (Return %(returned).0f pts, Remove %(removed).0f pts)"
            ) % {
                "order": order.name,
                "returned": used_points,
                "removed": issued_points,
            }

            self.env["loyalty.history"].create({
                "card_id": card.id,
                "description": description,
                "issued": used_points,
                "used": issued_points,
                "order_id": order.id,
                "order_model": "sale.order",
            })
            order.loyalty_redeem_reversed = True
            _logger.debug(
                "Reversed loyalty points for %s: returned %s, removed %s, net %s", 
                order.name,
                used_points,
                issued_points,
                net_adjustment,
            )
            # TODO: Reverse redemption when a refund/return is confirmed, hooking into the
            # related accounting move or return validation workflow.

