from odoo import http, _
from odoo.exceptions import UserError
from odoo.http import request


class LoyaltyRedeemController(http.Controller):
    @http.route('/shop/redeem_points', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def redeem_points(self, **post):
        order = request.website.sale_get_order()
        if not order:
            request.session['loyalty_message'] = ('danger', _('No active cart found.'))
            return request.redirect('/shop/cart')

        try:
            points_to_use = float(post.get('points_to_use') or 0.0)
        except (TypeError, ValueError):
            points_to_use = 0.0

        try:
            rm_per_point = float(post.get('rm_per_point') or 0.01)
        except (TypeError, ValueError):
            rm_per_point = 0.01

        program = request.env["loyalty.program"].sudo().search([
            ("program_type", "=", "loyalty"),
            ("active", "=", True),
        ], limit=1)

        card = request.env['loyalty.card'].sudo().search([
            ('program_id', '=', program.id),
            ('partner_id', '=', request.env.user.partner_id.id),
        ], limit=1) if program else False

        try:
            amount = order.sudo().apply_loyalty_redemption(
                card=card,
                points=points_to_use,
                rm_per_point=rm_per_point,
                context_description=_(
                    "Redeem %(points).0f loyalty points (website)",
                    points=points_to_use,
                ),
            )
            request.session['loyalty_message'] = (
                'success',
                _(\
                    "Redeemed %(points).0f points for a RM %(amount).2f discount.",
                    points=points_to_use,
                    amount=amount,
                ),
            )
        except UserError as exc:
            request.session['loyalty_message'] = ('danger', exc.name or str(exc))

        return request.redirect('/shop/cart')
