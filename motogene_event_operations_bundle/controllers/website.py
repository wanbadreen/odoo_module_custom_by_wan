# -*- coding: utf-8 -*-
"""Queue optional booking-owner confirmation after all attendees are created."""
from odoo.addons.motogene_event_contacts.controllers.website import (
    MotoGeneBookingController,
)


class MotoGeneOperationsBookingController(MotoGeneBookingController):

    def _create_attendees_from_registration_post(self, event, registration_data):
        registrations = super()._create_attendees_from_registration_post(
            event, registration_data,
        )
        if event.motogene_ops_booking_confirmation_enabled:
            # This route hook runs AFTER MotoGene Event Contacts has linked
            # the entire group to one completed Booking record.
            bookings = registrations.mapped("motogene_booking_id")
            if len(bookings) == 1:
                bookings.sudo().action_queue_owner_confirmation()
        return registrations
