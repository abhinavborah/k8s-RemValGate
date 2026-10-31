"""Deploy AIOpsLab's Hotel Reservation application to the kind cluster. No fault is injected.

The four RevokeAuth and UserUnregistered pool problems run on it, in test-hotel-reservation
(docs/problem_inventory.md). Needs aiopslab/config.yml and a running kind cluster, so it is run by
hand and never by CI. Do not use Orchestrator.init_problem for this: it deploys the app and also
injects a fault.

    uv run python scripts/deploy_hotel_reservation.py

The constructor creates the namespace and the ConfigMaps holding the fault scripts; deploy()
applies the manifests and waits until every pod is ready.
"""

from aiopslab.service.apps.hotelres import HotelReservation

HotelReservation().deploy()
print("deploy returned")
