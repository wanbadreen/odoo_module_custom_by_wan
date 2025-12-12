from werkzeug.urls import url_quote

from odoo import http, _
from odoo.exceptions import UserError
from odoo.http import request


class WebsiteLoyaltyPartialRedeem(http.Controller):

    @http.route("/shop/loyalty/redeem", type="http", auth="public", website=True, methods=["POST"])
    def loyalty_redeem(self, points_to_use=None, **post):
        redirect_url = post.get("redirect") or request.httprequest.referrer or "/shop/cart"

        if request.website.is_public_user():
            login_redirect = url_quote(redirect_url)
            return request.redirect(f"/web/login?redirect={login_redirect}")

        order = request.website.sale_get_order(force_create=False)
        if not order or order.partner_id != request.env.user.partner_id:
            request.session["loyalty_redeem_message"] = _("No active cart found.")
            return request.redirect(redirect_url)

        program = request.env["loyalty.program"].sudo().search([
            ("program_type", "=", "loyalty"),
            ("active", "=", True),
        ], limit=1)

        if not program:
            request.session["loyalty_redeem_message"] = _("No active loyalty program found.")
            return request.redirect(redirect_url)

        card = request.env["loyalty.card"].sudo().search([
            ("program_id", "=", program.id),
            ("partner_id", "=", order.partner_id.id),
        ], limit=1)

        if not card:
            request.session["loyalty_redeem_message"] = _("No loyalty card found for this customer.")
            return request.redirect(redirect_url)

        try:
            points = float(points_to_use or 0)
        except (TypeError, ValueError):
            points = 0.0

        if points <= 0:
            request.session["loyalty_redeem_message"] = _("Please enter a valid number of points to redeem.")
            return request.redirect(redirect_url)

        if points > (card.points or 0.0):
            request.session["loyalty_redeem_message"] = _("You cannot use more points than available.")
            return request.redirect(redirect_url)

        LoyaltyRedeemWizard = request.env["loyalty.partial.redeem.wizard"].sudo()
        wizard = LoyaltyRedeemWizard.create({
            "sale_order_id": order.id,
            "loyalty_card_id": card.id,
            "available_points": card.points,
            "points_to_use": points,
        })

        try:
            wizard.action_confirm()
        except UserError as error:
            request.session["loyalty_redeem_message"] = error.name or str(error)
        except Exception:
            request.session["loyalty_redeem_message"] = _("Something went wrong while redeeming points.")
        else:
            request.session["loyalty_redeem_message"] = _("Loyalty points redeemed successfully.")

        return request.redirect(redirect_url)
