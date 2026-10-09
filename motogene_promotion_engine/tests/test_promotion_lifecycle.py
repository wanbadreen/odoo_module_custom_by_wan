from datetime import date
from unittest.mock import patch
from odoo.tests.common import TransactionCase
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestPromotionLifecycle(TransactionCase):
    def test_calendar_boundaries_and_manual_states(self):
        program = self.env["motogene.promotion.program"].new({
            "state": "active", "active": True,
            "date_start": date(2026, 10, 20), "date_end": date(2026, 11, 11),
        })
        for today, expected in [
            (date(2026, 10, 19), "scheduled"),
            (date(2026, 10, 20), "running"),
            (date(2026, 11, 11), "running"),
            (date(2026, 11, 12), "ended"),
        ]:
            with patch("odoo.fields.Date.context_today", return_value=today):
                program._compute_lifecycle_state()
                self.assertEqual(program.lifecycle_state, expected)
        program.state = "draft"
        program._compute_lifecycle_state()
        self.assertEqual(program.lifecycle_state, "draft")
        program.state = "archived"
        program._compute_lifecycle_state()
        self.assertEqual(program.lifecycle_state, "archived")
        program.state = "active"
        program.active = False
        program._compute_lifecycle_state()
        self.assertEqual(program.lifecycle_state, "archived")
