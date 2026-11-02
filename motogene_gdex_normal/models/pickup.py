"""Pickup requests with durable uncertainty to prevent accidental duplicates."""
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.modules.registry import Registry

MALAYSIA = ZoneInfo('Asia/Kuala_Lumpur')


def _today_my():
    return datetime.now(MALAYSIA).date()


class GdexPickup(models.Model):
    _name = 'motogene.gdex.pickup'
    _description = 'GDEX Pickup Arrangement'
    _order = 'pickup_date desc, id desc'

    pickup_number = fields.Char(readonly=True, copy=False, index=True)
    pickup_date = fields.Date(required=True)
    ready_time = fields.Char(required=True, default='10:00:00')
    close_time = fields.Char(required=True, default='18:00:00')
    contact_name = fields.Char()
    contact_number = fields.Char()
    transportation = fields.Selection([('Motorbike', 'Motorbike'),
                                       ('Van', 'Van'),
                                       ('OffsizeTruck', 'OffsizeTruck')], required=True, default='Van')
    is_trolley = fields.Boolean()
    remarks = fields.Char()
    shipment_type = fields.Selection([('Parcel', 'Parcel'), ('Document', 'Document')],
                                      required=True, default='Parcel')
    pieces = fields.Integer(default=1, required=True)
    weight = fields.Float(default=1.0, required=True)
    api_environment = fields.Selection([('sandbox', 'Sandbox'), ('production', 'Production')],
                                        required=True, readonly=True, default='sandbox')
    state = fields.Selection([
        ('draft', 'Draft'), ('uncertain', 'Creation Uncertain'),
        ('confirmed', 'Confirmed'), ('rejected', 'Rejected'),
        ('cancel_pending', 'Cancellation Uncertain'),
        ('cancelled', 'Cancelled'), ('external', 'Imported from GDEX'),
    ], default='draft', required=True, readonly=True, index=True)
    error_message = fields.Char(readonly=True)
    created_by_id = fields.Many2one('res.users', readonly=True)
    cancelled_by_id = fields.Many2one('res.users', readonly=True)
    cancelled_at = fields.Datetime(readonly=True)
    pickup_status = fields.Char(readonly=True)
    last_checked_at = fields.Datetime(readonly=True)

    @api.model
    def _check_operator(self):
        self.env['motogene.gdex.consignment']._check_operator()

    @api.model
    def _allow_production_mutation(self, environment):
        if environment == 'production':
            enabled = self.env['ir.config_parameter'].sudo().get_param(
                'motogene_gdex_normal.pickup_production_enabled', 'False')
            if enabled != 'True':
                raise UserError(_('Production pickup create/cancel is disabled. '
                                  'Enable it separately in GDEX settings after UAT.'))

    @api.model
    def _credentials(self, environment=None):
        return self.env['motogene.gdex.consignment']._credentials(environment=environment)

    @api.model
    def _check_time(self, text):
        try:
            datetime.strptime(text, '%H:%M:%S')
        except (TypeError, ValueError) as exc:
            raise UserError(_('Enter pickup times as HH:MM:SS (for example 10:00:00).')) from exc

    def _validate(self):
        self.ensure_one()
        if self.pickup_date <= _today_my():
            raise UserError(_('For safety, schedule new pickups from tomorrow onward (Malaysia time).'))
        self._check_time(self.ready_time)
        self._check_time(self.close_time)
        if self.ready_time >= self.close_time:
            raise UserError(_('Parcel ready time must be earlier than office closing time.'))
        if self.pieces <= 0 or self.weight <= 0:
            raise UserError(_('Pieces and weight must be greater than zero.'))
        if len(self.remarks or '') > 50:
            raise UserError(_('GDEX pickup remarks must be 50 characters or fewer.'))

    def action_create_pickup(self):
        """Reserve a pickup before HTTP; timeout never retries implicitly."""
        self.ensure_one()
        self._check_operator()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only administrators may send pickup requests in this first release.'))
        if self.state != 'draft':
            raise UserError(_('Only draft pickups can be sent to GDEX.'))
        self._validate()
        params = self.env['ir.config_parameter'].sudo()
        environment = params.get_param('motogene_gdex_normal.environment', 'sandbox')
        self._allow_production_mutation(environment)
        headers, account, endpoint, _ = self._credentials(environment=environment)
        if not account:
            raise UserError(_('Configure a GDEX Account No before requesting pickup.'))
        dbname, uid = self.env.cr.dbname, self.env.uid
        with Registry(dbname).cursor() as cr:
            other = api.Environment(cr, uid, dict(self.env.context))
            cr.execute('SELECT id FROM motogene_gdex_pickup WHERE id=%s FOR UPDATE', (self.id,))
            row = other[self._name].sudo().browse(self.id)
            if row.state != 'draft':
                raise UserError(_('Pickup has already been processed.'))
            cr.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',
                       ('gdex-pickup-%s-%s' % (environment, row.pickup_date),))
            other_pickups = other[self._name].sudo().search_count([
                ('id', '!=', row.id), ('api_environment', '=', environment),
                ('pickup_date', '=', row.pickup_date),
                ('state', 'in', ['uncertain', 'confirmed', 'cancel_pending']),
            ])
            if other_pickups:
                raise UserError(_('A pickup already exists or is uncertain for this date and environment.'))
            payload = {
                'contactName': row.contact_name or '',
                'contactNumber': row.contact_number or '',
                'pickupDate': row.pickup_date.isoformat() + 'T00:00:00+08:00',
                'officeCloseTime': row.close_time,
                'parcelReadyTime': row.ready_time,
                'transportation': row.transportation,
                'isTrolley': bool(row.is_trolley),
                'remarks': row.remarks or '',
                'shipmentType': row.shipment_type,
                'pieces': row.pieces,
                'weight': row.weight,
            }
            row.write({'state': 'uncertain', 'api_environment': environment,
                       'created_by_id': uid,
                       'error_message': 'Pickup request reserved; check portal if interrupted.'})
            cr.commit()

        state, number, error = 'uncertain', False, 'Unknown response; verify portal before retry.'
        try:
            response = requests.post(
                endpoint + '/CreatePickup', params={'accountNo': account},
                headers=headers, json=payload, timeout=(10, 35))
            data = response.json()
            if isinstance(data, dict) and response.status_code == 200 and data.get('s') == 'success' and data.get('r'):
                state, number, error = 'confirmed', str(data['r']), False
            elif (isinstance(data, dict) and response.status_code in (400, 401, 403)
                  and data.get('s') == 'fail'):
                state, error = 'rejected', str(data.get('e') or 'GDEX rejected pickup')[:500]
        except (requests.RequestException, ValueError):
            pass

        with Registry(dbname).cursor() as cr:
            other = api.Environment(cr, uid, dict(self.env.context))
            cr.execute('SELECT id FROM motogene_gdex_pickup WHERE id=%s FOR UPDATE', (self.id,))
            row = other[self._name].sudo().browse(self.id)
            if row.state != 'uncertain':
                raise UserError(_('Pickup was changed during the API request. Investigate portal.'))
            row.write({'state': state, 'pickup_number': number, 'error_message': error})
            cr.commit()
        return self._notification(_('GDEX Pickup'), number or error,
                                  'success' if state == 'confirmed' else 'warning')

    def action_cancel_pickup(self):
        self.ensure_one()
        self._check_operator()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only administrators can cancel pickup requests.'))
        if self.state not in ('confirmed', 'external') or not self.pickup_number:
            raise UserError(_('Only confirmed or imported pickup requests can be cancelled.'))
        if self.pickup_date <= _today_my():
            raise UserError(_('GDEX only permits cancellation for tomorrow onward.'))
        self._allow_production_mutation(self.api_environment)
        headers, _account, endpoint, _ = self._credentials(environment=self.api_environment)
        prior_state = self.state
        dbname, uid = self.env.cr.dbname, self.env.uid
        with Registry(dbname).cursor() as cr:
            other = api.Environment(cr, uid, dict(self.env.context))
            cr.execute('SELECT id FROM motogene_gdex_pickup WHERE id=%s FOR UPDATE', (self.id,))
            row = other[self._name].sudo().browse(self.id)
            if row.state not in ('confirmed', 'external'):
                raise UserError(_('Pickup is already cancelled or pending cancellation.'))
            row.write({'state': 'cancel_pending',
                       'error_message': 'Cancellation reserved; verify portal on timeout.'})
            cr.commit()
        state, message = 'cancel_pending', 'Cancellation uncertain; verify in myGDEX Prime.'
        try:
            response = requests.put(endpoint + '/CancelPickup', headers=headers,
                                    params={'pickupNumber': self.pickup_number}, timeout=(10, 35))
            data = response.json()
            if isinstance(data, dict) and response.status_code == 200 and data.get('s') == 'success':
                state, message = 'cancelled', 'Cancellation confirmed by GDEX.'
            elif (isinstance(data, dict) and response.status_code in (400, 401, 403)
                  and data.get('s') == 'fail'):
                state, message = prior_state, str(data.get('e') or 'Cancellation rejected')[:500]
        except (requests.RequestException, ValueError):
            pass
        with Registry(dbname).cursor() as cr:
            other = api.Environment(cr, uid, dict(self.env.context))
            cr.execute('SELECT id FROM motogene_gdex_pickup WHERE id=%s FOR UPDATE', (self.id,))
            row = other[self._name].sudo().browse(self.id)
            if row.state != 'cancel_pending':
                raise UserError(_('Pickup state changed during cancellation.'))
            changes = {'state': state, 'error_message': message}
            if state == 'cancelled':
                changes.update({'cancelled_by_id': uid, 'cancelled_at': fields.Datetime.now()})
            row.write(changes)
            cr.commit()
        return self._notification(_('GDEX Pickup Cancellation'), message,
                                  'success' if state == 'cancelled' else 'warning')

    @api.model
    def action_refresh_upcoming(self):
        self._check_operator()
        headers, account, endpoint, environment = self._credentials()
        try:
            response = requests.get(endpoint + '/GetUpcomingPickupDetail', headers=headers,
                                    timeout=(10, 25))
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise UserError(_('Unable to retrieve GDEX pickups (%s).') % type(exc).__name__) from exc
        if (response.status_code != 200 or not isinstance(data, dict)
                or data.get('s') != 'success' or not isinstance(data.get('r'), list)):
            raise UserError(_('GDEX did not confirm upcoming pickup details.'))
        total = 0
        for entry in data['r']:
            if not isinstance(entry, dict) or not entry.get('referenceNo'):
                continue
            reference = str(entry['referenceNo'])
            row = self.sudo().search([('pickup_number', '=', reference),
                                      ('api_environment', '=', environment)], limit=1)
            if row:
                # Existing pending/uncertain requests must never be silently
                # resolved by a read-only listing; preserve audit state.
                if row.state in ('confirmed', 'external'):
                    row.write({'pickup_status': str(entry.get('pickupStatus') or '')[:120],
                               'last_checked_at': fields.Datetime.now()})
            else:
                raw_date = str(entry.get('pickupDate') or '')[:10]
                try:
                    pickup_date = fields.Date.to_date(raw_date)
                except (ValueError, TypeError):
                    continue
                if not pickup_date:
                    continue
                self.sudo().create({
                    'pickup_number': reference, 'pickup_date': pickup_date,
                    'ready_time': str(entry.get('parcelReadyTime') or '10:00:00')[:8],
                    'close_time': str(entry.get('officeCloseTime') or '18:00:00')[:8],
                    'transportation': entry.get('transportationMode')
                                      if entry.get('transportationMode') in ('Van', 'Motorbike', 'OffsizeTruck')
                                      else 'Van',
                    'is_trolley': bool(entry.get('isTrolley')),
                    'shipment_type': entry.get('shipmentType')
                                     if entry.get('shipmentType') in ('Parcel', 'Document') else 'Parcel',
                    'pieces': int(entry.get('pieces') or 1),
                    'weight': float(entry.get('weight') or 1),
                    'api_environment': environment, 'state': 'external',
                    'pickup_status': str(entry.get('pickupStatus') or '')[:120],
                    'last_checked_at': fields.Datetime.now(),
                })
                total += 1
        return self._notification(_('GDEX Upcoming Pickups'),
                                  _('%s new pickups imported. Existing entries preserved.') % total, 'success')

    @api.model
    def _notification(self, title, message, typ):
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': title, 'message': message,
                           'type': typ, 'sticky': typ != 'success'}}
