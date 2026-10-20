from pipeline.run import run_pipeline


def test_health_before_any_run(client):
    body = client.get("/api/health").get_json()
    assert body == {"status": "ok", "database": "ok", "latest_run": None}


def test_health_reports_latest_run(client):
    run_id = run_pipeline()
    body = client.get("/api/health").get_json()
    assert body["latest_run"]["id"] == run_id
    assert body["latest_run"]["status"] == "succeeded"
    assert body["latest_run"]["finished_at"].endswith("+00:00")


def test_unknown_route_uses_error_shape(client):
    resp = client.get("/api/nope")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"