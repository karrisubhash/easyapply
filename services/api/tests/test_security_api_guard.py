"""The API refuses DNS rebinding and cross-site writes itself (BrowserRequestGuard).

The Next proxy already did both, but the API listens on 127.0.0.1:8000 too and a
page anywhere could go straight to it: a request with no identity header is the
configured developer, and a hostile domain re-pointed at 127.0.0.1 is
same-origin with the API. These pin the guard at the API, beneath the proxy.
"""

from __future__ import annotations

import pytest

from app.core.config import settings


def test_a_foreign_host_is_refused_on_every_method(client):
    for method in ("get", "post"):
        response = getattr(client, method)(
            "/api/v1/profile", headers={"Host": "evil.example:8000"}
        )
        assert response.status_code == 403, method
        assert response.json()["code"] == "host_not_allowed"


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "[::1]:8000", "testserver"])
def test_loopback_hosts_are_answered(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 200


def test_a_configured_host_is_answered(client, monkeypatch):
    monkeypatch.setattr(settings, "API_ALLOWED_HOSTS", "api.easyapply.example, other.example")
    assert client.get("/health", headers={"Host": "api.easyapply.example"}).status_code == 200
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "http://127.0.0.1:3000"},  # another port on loopback is another application
    ],
)
def test_a_cross_site_write_is_refused(client, headers):
    response = client.post("/api/v1/profile", headers={"Host": "127.0.0.1:8000", **headers})
    assert response.status_code == 403
    assert response.json()["code"] == "cross_site_request"


def test_a_same_origin_write_is_accepted(client):
    response = client.post(
        "/api/v1/profile",
        headers={"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000"},
    )
    assert response.status_code == 201


def test_a_write_with_no_browser_provenance_is_accepted(client):
    """The Next proxy, curl and workers send neither Origin nor Sec-Fetch-Site."""
    assert client.post("/api/v1/profile").status_code == 201


def test_a_cross_site_read_is_not_refused_by_the_origin_rule(client):
    """GET changes nothing, and a cross-site page cannot read the answer (the
    Host rule stops the rebinding that could)."""
    client.post("/api/v1/profile")
    assert client.get("/api/v1/profile", headers={"Origin": "http://evil.example"}).status_code == 200
