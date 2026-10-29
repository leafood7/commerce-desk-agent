"""Real safe-profile publication, queued snapshot binding and logical configuration restoration."""

import pytest
import test_workbench_dispatch_docker as dispatch
import test_workbench_docker_flows as flows

pytestmark = pytest.mark.docker
container_api = flows.container_api


def test_real_configuration_release_does_not_change_queued_run(container_api):
    client, headers = container_api
    admin, support = headers("admin", "demo-b"), headers("support", "demo-b")
    original = client.get("/api/v1/runtime/config", headers=admin).json()
    owned_head = None
    worker_stopped = False

    def create(values):
        response = client.post("/api/v1/runtime/config/versions", headers=admin, json=values)
        response.raise_for_status()
        return response.json()

    def activate(row, revision):
        response = client.post(
            "/api/v1/runtime/config/versions/" + row["id"] + "/publish",
            headers=admin,
            json={"expected_revision": revision},
        )
        response.raise_for_status()
        return response.json()

    try:
        first = create(
            {"model_alias": "rules", "max_output_tokens": 128, "top_k": 1, "tone": "friendly"}
        )
        live = activate(first, original["revision"])
        owned_head = first["id"]
        dispatch.compose("stop", "run-worker")
        worker_stopped = True
        response = client.post("/api/v1/runs", headers=support, json={"order_number": "B2001"})
        response.raise_for_status()
        run = response.json()
        assert run["status"] == "queued"
        frozen = client.get("/api/v1/runs/" + run["id"] + "/configuration", headers=support).json()
        assert frozen["version_id"] == first["id"]
        second = create(
            {"model_alias": "rules", "max_output_tokens": 256, "top_k": 3, "tone": "plain"}
        )
        activate(second, live["revision"])
        owned_head = second["id"]
        dispatch.compose("start", "run-worker")
        worker_stopped = False
        done = flows.wait_run(client, support, run["id"])
        assert done["status"] == "completed" and done["output"]["answer"].startswith("您好")
        assert (
            client.get("/api/v1/runs/" + run["id"] + "/configuration", headers=support).json()
            == frozen
        )
        later = client.post("/api/v1/runs", headers=support, json={"order_number": "B2001"}).json()
        later = flows.wait_run(client, support, later["id"])
        assert not later["output"]["answer"].startswith("您好")
        assert (
            client.get("/api/v1/runs/" + later["id"] + "/configuration", headers=support).json()[
                "version_id"
            ]
            == second["id"]
        )
    finally:
        if worker_stopped:
            dispatch.compose("start", "run-worker")
        current = client.get("/api/v1/runtime/config", headers=admin).json()
        # Never replace an unrelated concurrent operator publication during cleanup.
        if owned_head and current["current"]["id"] == owned_head:
            if original["current"]["id"]:
                target = original["current"]
            else:
                target = create(
                    {
                        k: v
                        for k, v in original["current"]["values"].items()
                        if k not in {"prompt_sha256", "return_prompt_sha256"}
                    }
                )
            activate(target, current["revision"])
