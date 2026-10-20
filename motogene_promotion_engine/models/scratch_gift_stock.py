# -*- coding: utf-8 -*-
from collections import defaultdict

from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


class ScratchGiftStockSale(models.Model):
    _inherit = 'sale.order'

    def _scratch_gift_stock(self, products):
        """Warehouse free stock less unreserved, outstanding scratch gifts.

        None means inventory is not tracked, rather than zero availability.
        Use sudo only for aggregate stock/demand reads within this SO company
        and warehouse: Sales users need balances without stock-quant access.
        """
        self.ensure_one()
        warehouse = self.warehouse_id
        if not warehouse or warehouse.company_id != self.company_id:
            raise UserError(_('Select a warehouse belonging to the quotation company.'))
        tracked = products.filtered('is_storable')
        result = {product.id: None for product in products}
        if not tracked:
            return result
        stock = tracked.sudo().with_company(self.company_id).with_context(
            allowed_company_ids=[self.company_id.id], warehouse_id=warehouse.id,
            location=False, lot_id=False, owner_id=False, package_id=False,
            from_date=False, to_date=False,
        )
        stock.invalidate_recordset(['free_qty'])
        free = {product.id: product.free_qty for product in stock}
        pending = defaultdict(float)
        lines = self.env['sale.order.line'].sudo().search([
            ('product_id', 'in', tracked.ids),
            ('order_id.company_id', '=', self.company_id.id),
            ('order_id.warehouse_id', '=', warehouse.id),
            ('order_id.state', 'in', ['draft', 'sent', 'sale']),
            ('scratch_redemption_id.state', '=', 'confirmed'),
            ('price_unit', '=', 0), ('product_uom_qty', '>', 0),
        ])
        locations = self.env['stock.location'].sudo().search([
            ('id', 'child_of', warehouse.view_location_id.id),
            ('usage', '=', 'internal'),
            ('company_id', 'in', [False, self.company_id.id]),
        ])
        for line in lines:
            product = line.product_id
            outstanding = line.product_uom._compute_quantity(
                max(0, line.product_uom_qty - line.qty_delivered), product.uom_id)
            # free_qty already excludes these physical reservations. Only
            # deduct the remaining promise, including draft reward quotations.
            reserved = sum(move.product_uom._compute_quantity(
                move.quantity, product.uom_id)
                for move in line.move_ids
                if move.state not in ('done', 'cancel')
                and move.product_id == product
                and move.company_id == self.company_id
                and move.location_id.id in locations.ids)
            pending[product.id] += max(0, outstanding - reserved)
        for product in tracked:
            result[product.id] = max(0, free[product.id] - pending[product.id])
        return result


def gift_stock_markup(order, reward_lines):
    if not order or not reward_lines:
        return False
    variants_by_line = [(line, line._gift_variants(order.company_id)) for line in reward_lines]
    products = order.env['product.product']
    for line, variants in variants_by_line:
        products |= variants
    available = order._scratch_gift_stock(products)
    rows = []
    for line, variants in variants_by_line:
        for product in variants:
            amount = available[product.id]
            enough = amount is None or float_compare(
                amount, line.quantity, precision_rounding=product.uom_id.rounding) >= 0
            status = _('Not tracked') if amount is None else (_('Available') if enough else _('Insufficient stock'))
            rows.append(Markup('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>') % (
                escape(product.display_name), escape('-' if amount is None else '%g' % amount),
                escape('%g' % line.quantity), escape(product.uom_id.name), escape(status)))
    if not rows:
        return Markup('<p>%s</p>') % escape(_('No active gift variants are configured for this company.'))
    return Markup('<p><strong>%s:</strong> %s</p><table class="table table-sm"><thead><tr>'
                  '<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>'
                  '</tr></thead><tbody>%s</tbody></table><p>%s</p>') % (
        escape(_('Warehouse')), escape(order.warehouse_id.display_name),
        escape(_('Gift / Variant')), escape(_('Available')), escape(_('Required')),
        escape(_('Unit')), escape(_('Status')), Markup('').join(rows),
        escape(_('Available excludes Odoo reservations and unreserved outstanding scratch gifts. '
                 'Balances are checked again at redemption. Untracked products have no stock limit.')))


def filter_available_variants(order, reward_line, products):
    if not order or not reward_line:
        return products
    available = order._scratch_gift_stock(products)
    return products.filtered(lambda product: available[product.id] is None or float_compare(
        available[product.id], reward_line.quantity,
        precision_rounding=product.uom_id.rounding) >= 0)


class ScratchGiftStockRedemption(models.Model):
    _inherit = 'motogene.scratch.redemption'

    gift_stock_html = fields.Html(compute='_compute_gift_stock_html', string='Gift Stock Availability')

    @api.depends('pool_id', 'target_sale_id.warehouse_id', 'gift_choice_ids.product_id')
    def _compute_gift_stock_html(self):
        for record in self:
            choices = record.pool_id.reward_product_line_ids.filtered(lambda line: line.variant_mode == 'choose')
            record.gift_stock_html = gift_stock_markup(record.target_sale_id, choices)

    def _selected_free_product_rewards(self, pool):
        rewards = super()._selected_free_product_rewards(pool)
        chosen = self.gift_choice_ids.mapped('product_id').filtered('is_storable')
        if chosen:
            # Row-version updates serialize concurrent scratch promises even
            # under Odoo's repeatable-read isolation. Product data is unchanged;
            # a competing transaction retries with a fresh stock/demand view.
            self.env.cr.execute(
                'SELECT id FROM product_product WHERE id IN %s ORDER BY id FOR UPDATE',
                [tuple(sorted(chosen.ids))])
            self.env.cr.execute(
                'UPDATE product_product SET write_date = write_date WHERE id IN %s',
                [tuple(sorted(chosen.ids))])
            availability = self.target_sale_id._scratch_gift_stock(chosen)
            required = defaultdict(float)
            for product, quantity, price in rewards:
                if product in chosen:
                    required[product.id] += quantity
            for product in chosen:
                if float_compare(availability[product.id], required[product.id],
                                 precision_rounding=product.uom_id.rounding) < 0:
                    raise UserError(_(
                        '%(product)s has only %(available)g %(unit)s available in %(warehouse)s; '
                        '%(required)g are required. Reopen redemption and select an available variant.'
                    ) % {'product': product.display_name, 'available': availability[product.id],
                         'unit': product.uom_id.name, 'warehouse': self.target_sale_id.warehouse_id.display_name,
                         'required': required[product.id]})
        return rewards


class ScratchGiftStockWizard(models.TransientModel):
    _inherit = 'motogene.scratch.redemption.wizard'

    gift_stock_html = fields.Html(compute='_compute_gift_stock_html', string='Gift Stock Availability')

    @api.depends('card_id', 'sale_id.warehouse_id', 'gift_choice_ids.product_id')
    def _compute_gift_stock_html(self):
        for wizard in self:
            choices = wizard.pool_id.reward_product_line_ids.filtered(lambda line: line.variant_mode == 'choose')
            wizard.gift_stock_html = gift_stock_markup(wizard.sale_id, choices)

    @api.depends('gift_choice_ids', 'gift_choice_ids.product_id',
                 'gift_choice_ids.reward_line_id', 'sale_id.company_id', 'sale_id.warehouse_id')
    def _compute_single_gift_variant(self):
        super()._compute_single_gift_variant()
        for wizard in self:
            if len(wizard.gift_choice_ids) == 1:
                wizard.single_allowed_variant_ids = filter_available_variants(
                    wizard.sale_id, wizard.gift_choice_ids.reward_line_id,
                    wizard.single_allowed_variant_ids)


class ScratchGiftStockChoiceWizard(models.TransientModel):
    _inherit = 'motogene.scratch.gift.choice.wizard'

    @api.depends('reward_line_id', 'wizard_id.sale_id.company_id', 'wizard_id.sale_id.warehouse_id')
    def _compute_allowed_products(self):
        super()._compute_allowed_products()
        for line in self:
            line.allowed_product_ids = filter_available_variants(
                line.wizard_id.sale_id, line.reward_line_id, line.allowed_product_ids)


class ScratchGiftStockChoice(models.Model):
    _inherit = 'motogene.scratch.gift.choice'

    @api.depends('reward_line_id', 'redemption_id.target_sale_id.company_id',
                 'redemption_id.target_sale_id.warehouse_id')
    def _compute_allowed_products(self):
        super()._compute_allowed_products()
        for line in self:
            line.allowed_product_ids = filter_available_variants(
                line.redemption_id.target_sale_id, line.reward_line_id, line.allowed_product_ids)
