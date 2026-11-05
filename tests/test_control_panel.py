import logging
from types import SimpleNamespace

import pytest
from flask import Flask

from locustfile import mount_control_panel

TOKEN = "s3cret-token"
LOCAL = "http://127.0.0.1:8089"


def environment(app):
    return SimpleNamespace(
        web_ui=SimpleNamespace(app=app), parsed_options=SimpleNamespace(web_port=8089)
    )


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("SWARMSCOPE_TOKEN", TOKEN)
    app = Flask(__name__)
    app.add_url_rule("/swarm", "swarm", lambda: "started", methods=["POST"])
    mount_control_panel(environment(app))
    return app


def signed_in(app):
    client = app.test_client()
    assert client.get(f"/ui?token={TOKEN}", base_url=LOCAL).status_code == 302
    return client


def test_panel_refuses_requests_without_the_token(app):
    assert app.test_client().get("/ui", base_url=LOCAL).status_code == 403


def test_panel_refuses_a_wrong_token(app):
    response = app.test_client().get("/ui?token=nope", base_url=LOCAL)

    assert response.status_code == 403
    assert "Set-Cookie" not in response.headers


def test_token_link_sets_a_strict_cookie_and_drops_the_token_from_the_url(app):
    response = app.test_client().get(f"/ui?token={TOKEN}&x=1", base_url=LOCAL)

    assert response.status_code == 302
    assert response.headers["Location"] == "/ui?x=1"
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie


def test_panel_is_served_after_signing_in(app):
    response = signed_in(app).get("/ui", base_url=LOCAL)

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert b"<title>Swarmscope</title>" in response.data
    assert f'name="swarmscope-token" content="{TOKEN}"'.encode() in response.data


def test_cross_site_post_to_swarm_is_refused_without_the_cookie(app):
    assert (
        app.test_client()
        .post("/swarm", data={"user_count": "500"}, base_url=LOCAL)
        .status_code
        == 403
    )
    assert (
        signed_in(app)
        .post("/swarm", data={"user_count": "5"}, base_url=LOCAL)
        .status_code
        == 200
    )


def test_token_in_the_query_only_signs_in_on_get(app):
    response = app.test_client().post(f"/swarm?token={TOKEN}", base_url=LOCAL)

    assert response.status_code == 403


def test_foreign_host_is_refused_even_with_the_cookie(app):
    client = signed_in(app)

    response = client.get(
        "/ui", base_url=LOCAL, headers={"Host": "attacker.example:8089"}
    )

    assert response.status_code == 403


def test_control_panel_skips_headless_runs():
    mount_control_panel(SimpleNamespace(web_ui=None))


def test_control_panel_serves_bundled_fonts(app):
    client = signed_in(app)

    for name in ("doto.woff2", "martian-mono.woff2"):
        response = client.get(f"/ui/fonts/{name}", base_url=LOCAL)
        assert response.status_code == 200
        assert response.data[:4] == b"wOF2"


def test_control_panel_refuses_paths_outside_fonts(app):
    response = signed_in(app).get("/ui/fonts/..%2Flocustfile.py", base_url=LOCAL)

    assert response.status_code == 404


def test_probe_route_returns_the_probe_result(app, monkeypatch):
    import locustfile

    monkeypatch.setattr(
        locustfile, "run_probe", lambda url: {"ok": True, "lcp": 812.0, "url": url}
    )

    response = signed_in(app).post(
        "/ui/probe", json={"url": "https://a.test/docs"}, base_url=LOCAL
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "lcp": 812.0,
        "url": "https://a.test/docs",
    }


def test_probe_route_rejects_non_http_urls(app):
    response = signed_in(app).post(
        "/ui/probe", json={"url": "file:///etc/passwd"}, base_url=LOCAL
    )

    assert response.status_code == 400


def test_without_the_app_a_token_is_generated_and_logged(monkeypatch, caplog):
    monkeypatch.delenv("SWARMSCOPE_TOKEN", raising=False)
    app = Flask(__name__)

    with caplog.at_level(logging.WARNING):
        mount_control_panel(environment(app))

    assert "http://127.0.0.1:8089/ui?token=" in caplog.text
    assert app.test_client().get("/ui", base_url=LOCAL).status_code == 403
