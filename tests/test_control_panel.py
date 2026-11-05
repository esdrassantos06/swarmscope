from types import SimpleNamespace

from flask import Flask

from locustfile import mount_control_panel


def test_control_panel_is_served_at_ui():
    app = Flask(__name__)
    mount_control_panel(SimpleNamespace(web_ui=SimpleNamespace(app=app)))

    response = app.test_client().get("/ui")

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert b"<title>Swarmscope</title>" in response.data


def test_control_panel_skips_headless_runs():
    mount_control_panel(SimpleNamespace(web_ui=None))


def test_control_panel_serves_bundled_fonts():
    app = Flask(__name__)
    mount_control_panel(SimpleNamespace(web_ui=SimpleNamespace(app=app)))
    client = app.test_client()

    for name in ("doto.woff2", "martian-mono.woff2"):
        response = client.get(f"/ui/fonts/{name}")
        assert response.status_code == 200
        assert response.data[:4] == b"wOF2"


def test_control_panel_refuses_paths_outside_fonts():
    app = Flask(__name__)
    mount_control_panel(SimpleNamespace(web_ui=SimpleNamespace(app=app)))

    assert app.test_client().get("/ui/fonts/..%2Flocustfile.py").status_code == 404


def test_probe_route_returns_the_probe_result(monkeypatch):
    import locustfile

    monkeypatch.setattr(
        locustfile, "run_probe", lambda url: {"ok": True, "lcp": 812.0, "url": url}
    )
    app = Flask(__name__)
    mount_control_panel(SimpleNamespace(web_ui=SimpleNamespace(app=app)))

    response = app.test_client().post("/ui/probe", json={"url": "https://a.test/docs"})

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "lcp": 812.0,
        "url": "https://a.test/docs",
    }


def test_probe_route_rejects_non_http_urls():
    app = Flask(__name__)
    mount_control_panel(SimpleNamespace(web_ui=SimpleNamespace(app=app)))

    response = app.test_client().post("/ui/probe", json={"url": "file:///etc/passwd"})

    assert response.status_code == 400
