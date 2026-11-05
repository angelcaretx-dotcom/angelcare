"""
Return legs for website round trips (ADR 0032).

A rider asks for the ride back on the same booking. When staff schedule
the outbound trip from that request, the return leg is created with it:
home-bound route, same passenger, payer and service. It is a draft for
dispatch to adjust, like any other trip.
"""

from datetime import timedelta

from django.db import transaction

from audit.services import record_change
from transportation.models import ReturnRide

from .models import Trip, TripLeg, TripStatus

# Will-call and wait-and-return have no fixed time: an estimate goes on
# the board and dispatch moves it once the rider calls.
ESTIMATE_AFTER_APPOINTMENT = timedelta(hours=1)
ESTIMATE_AFTER_PICKUP = timedelta(hours=2)


def needs_return_leg(trip: Trip) -> bool:
    return bool(
        trip.trip_request_id
        and trip.trip_request.return_ride != ReturnRide.NONE
        and trip.leg != TripLeg.RETURN
        and not Trip.objects.filter(outbound_trip_id=trip.pk).exists()
    )


def estimated_return_pickup(outbound: Trip):
    if outbound.appointment_datetime:
        return outbound.appointment_datetime + ESTIMATE_AFTER_APPOINTMENT
    return outbound.scheduled_pickup_datetime + ESTIMATE_AFTER_PICKUP


def build_return_leg(outbound: Trip) -> Trip:
    """The unsaved return leg for an outbound trip made from a round-trip request."""
    request = outbound.trip_request
    choice = request.return_ride
    waits = choice == ReturnRide.WAIT
    if choice == ReturnRide.SCHEDULED and request.return_datetime:
        pickup_at = request.return_datetime
    else:
        pickup_at = estimated_return_pickup(outbound)
    return Trip(
        passenger_id=outbound.passenger_id,
        source=outbound.source,
        subcontract_partner=outbound.subcontract_partner,
        payer_id=outbound.payer_id,
        prior_authorization_number=outbound.prior_authorization_number,
        leg=TripLeg.RETURN,
        outbound_trip=outbound,
        will_call=choice == ReturnRide.WILL_CALL,
        driver_waits=waits,
        service_type=outbound.service_type,
        pickup_address=outbound.dropoff_address,
        dropoff_address=outbound.pickup_address,
        # The waiting driver is the outbound driver.
        driver_id=outbound.driver_id if waits else None,
        vehicle_id=outbound.vehicle_id if waits else None,
        scheduled_pickup_datetime=pickup_at,
        status=TripStatus.SCHEDULED,
    )


@transaction.atomic
def create_return_leg(outbound: Trip, *, actor) -> Trip:
    """Validate and save the return leg; raises ValidationError if it can't be."""
    trip = build_return_leg(outbound)
    trip.full_clean()
    trip.save()
    record_change(
        actor=actor,
        action="created",
        resource_type="Trip",
        resource_id=trip.pk,
        after={"leg": TripLeg.RETURN, "outbound_trip": str(outbound.pk), "return_ride": outbound.trip_request.return_ride},
        source="admin",
    )
    return trip
