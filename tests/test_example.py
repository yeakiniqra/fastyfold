"""Smoke tests for the demo app in examples/shop."""

import importlib
import os
import re
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def shop():
    tmp = tempfile.mkdtemp()
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp}/test.db"
    os.environ["SECRET_KEY"] = "test-secret"
    sys.modules.pop("examples.shop.app", None)
    return importlib.import_module("examples.shop.app")


@pytest.fixture(scope="module")
def client(shop):
    with TestClient(shop.app, follow_redirects=False) as c:
        yield c


def login(client, user="admin", password="admin"):
    r = client.post("/admin/login", data={"username": user, "password": password, "next": "/admin/order"})
    assert r.status_code == 303, r.text
    return r


def csrf(client, path="/admin/"):
    page = client.get(path).text
    return re.search(r'name="csrf-token" content="([0-9a-f]+)"', page).group(1)


def test_requires_login(client):
    client.cookies.clear()
    r = client.get("/admin/")
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/login")


def test_wrong_password(client):
    client.cookies.clear()
    r = client.post("/admin/login", data={"username": "admin", "password": "nope"})
    assert r.status_code == 401 and "password didn&#39;t match" in r.text


def test_login_redirect_stays_inside_admin(client):
    client.cookies.clear()
    r = client.post("/admin/login", data={"username": "admin", "password": "admin", "next": "https://evil.example"})
    assert r.headers["location"] == "/admin/"


def test_pages_render(client):
    login(client)
    for path in ["/admin/", "/admin/order", "/admin/order?view=board", "/admin/order?view=split", "/admin/order?view=table&f_status=paid&q=a",
                 "/admin/customer", "/admin/product", "/admin/order/new", "/admin/order/1", "/admin/order/1/preview", "/admin/_log"]:
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:500])


def test_search_json(client):
    login(client)
    data = client.get("/admin/_search", params={"q": "nusrat"}).json()
    titles = [g["title"] for g in data["groups"]]
    assert "Records" in titles
    assert any("Nusrat" in it["label"] for g in data["groups"] for it in g["items"])


def test_post_without_csrf_is_rejected(client):
    login(client)
    r = client.post("/admin/order/1", data={"items": "2"})
    assert r.status_code == 403


def test_create_validates_then_saves_and_logs(client, shop):
    login(client)
    token = csrf(client)
    bad = client.post("/admin/order/new", data={"_csrf": token, "customer_id": "1", "items": "x", "total": "-5", "status": "paid"})
    assert bad.status_code == 422 and "Enter a valid whole number" in bad.text

    neg = client.post("/admin/order/new", data={"_csrf": token, "customer_id": "1", "items": "2", "total": "-5", "status": "paid", "carrier": "RedX"})
    assert neg.status_code == 422 and "negative" in neg.text

    ok = client.post("/admin/order/new", data={"_csrf": token, "customer_id": "1", "items": "2", "total": "19.50", "status": "paid", "carrier": "RedX"})
    assert ok.status_code == 303
    log = client.get("/admin/_log").text
    assert "added" in log


def test_edit_move_action_delete(client):
    login(client)
    token = csrf(client)
    r = client.post("/admin/order/2", data={"_csrf": token, "customer_id": "2", "items": "3", "total": "10", "status": "pending",
                                            "carrier": "DHL Express", "_continue": "1"})
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/order/2")
    assert 'value="DHL Express"' in client.get("/admin/order/2").text

    r = client.post("/admin/order/2/move", data={"value": "shipped"}, headers={"X-CSRF-Token": token})
    assert r.status_code == 204

    r = client.post("/admin/order/action", data={"_csrf": token, "action": "refund", "ids": ["2", "3"]})
    assert r.status_code == 303 and "Refund" in r.headers["location"]

    r = client.post("/admin/order/3/delete", data={"_csrf": token})
    assert r.status_code == 303
    assert client.get("/admin/order/3").status_code == 404


def test_permissions(client):
    client.cookies.clear()
    login(client, "farhana", "farhana")
    assert client.get("/admin/staff").status_code == 403
    assert "Staffs" not in client.get("/admin/").text
    labels = [it["label"] for g in client.get("/admin/_search").json()["groups"] for it in g["items"]]
    assert not any("staff" in label.lower() for label in labels)
