"""Deploy AIOpsLab's Social Network application to the kind cluster. No fault is injected.

Needs aiopslab/config.yml, a running kind cluster and helm, so it is run by hand and never by CI.
Do not use Orchestrator.init_problem for this: it deploys the app and also injects a fault.

    uv run python scripts/deploy_social_network.py

AIOpsLab's own deploy() waits 300 s and raises on a slow first image pull, which is not a
failure. The health check is the kubectl wait in the README.
"""

from aiopslab.service.apps.socialnet import SocialNetwork

SocialNetwork().deploy()
print("deploy returned")
