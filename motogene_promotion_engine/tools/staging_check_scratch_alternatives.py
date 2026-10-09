"""Paste/run in an Odoo.sh STAGING Python shell. No commit; fixture is rolled back."""
from collections import Counter
from datetime import timedelta
from odoo import fields, Command
from odoo.exceptions import UserError

assert 'staging' in env.cr.dbname.lower(), 'STOP: Run this test in staging only.'
assert 'scratch_replacement_policy' in env['motogene.promotion.program']._fields, 'Upgrade MotoGene Promotion Engine first.'

class _RollbackScratchCheck(Exception):
    pass

try:
    with env.cr.savepoint():
        today = fields.Date.context_today(env['sale.order'])
        program = env['motogene.promotion.program'].create({
            'name': 'TEMP - alternative card rollback test', 'reward_type': 'scratch_cards',
            'minimum_amount': 100, 'scratch_extra_prefix': 'D',
            'company_id': env.company.id, 'date_start': today, 'date_end': today,
            'scratch_redemption_expiry_date': today + timedelta(days=30),
        })
        # Pick a range beyond existing printed serials without touching any real pool.
        serials = env['motogene.scratch.picking.card'].search([]).mapped('serial_number')
        largest = max([int(s[1:]) for s in serials if s and len(s) > 1 and s[1:].isdigit()] or [0])
        first = max(900001, largest + 1000)
        pools = {}
        for prefix, size in [('D', 1), ('B', 3), ('G', 3)]:
            pools[prefix] = env['motogene.scratch.serial.pool'].create({
                'program_id': program.id, 'prefix': prefix,
                'first_number': first, 'last_number': first + size - 1,
            })
        partner = env['res.partner'].create({'name': 'TEMP scratch test customer'})
        order = env['sale.order'].create({'partner_id': partner.id, 'company_id': env.company.id})
        # Test allocation in isolation. This does not confirm a real customer SO.
        order.write({'state': 'sale', 'scratch_program_id': program.id,
                     'scratch_base_cards': 3, 'scratch_total_cards': 3})
        order._allocate_scratch_cards(allow_partial=True)
        assert Counter(order.scratch_card_line_ids.mapped('prefix')) == Counter(['D'])
        choices = [{'sale_id': order.id, 'original_prefix': 'D',
                    'pool_id': pools[p].id, 'quantity': 1} for p in ['B', 'G']]
        program.scratch_replacement_policy = 'allowed'
        pools['D'].allowed_replacement_pool_ids = [Command.set(pools['B'].ids)]
        rejected = False
        try:
            with env.cr.savepoint():
                order._allocate_scratch_alternatives(choices)
        except UserError:
            rejected = True
        assert rejected, 'FAIL: restricted policy allowed G.'
        pools['D'].allowed_replacement_pool_ids = [Command.set((pools['B'] | pools['G']).ids)]
        order._allocate_scratch_alternatives(choices)
        cards = order.scratch_card_line_ids
        assert Counter(cards.mapped('prefix')) == Counter(['D', 'B', 'G'])
        assert cards.filtered('original_prefix').mapped('original_prefix') == ['D', 'D']
        ids = cards.ids
        program.scratch_replacement_policy = 'none'
        order._allocate_scratch_cards()
        assert order.scratch_card_line_ids.ids == ids
        assert order.scratch_pending_cards == 0
        print('PASS: original D retained; B/G replacements allocated; restricted policy checked; existing cards preserved after policy change; pending = 0.')
        raise _RollbackScratchCheck()
except _RollbackScratchCheck:
    env.invalidate_all(flush=False)
    print('Test rolled back. Temporary promo, customer, order and cards were not saved. No commit was issued.')
