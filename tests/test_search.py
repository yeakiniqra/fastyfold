from .factories import author, post


def search(client, q=""):
    return {g["title"]: g["items"] for g in client.get("/admin/_search", params={"q": q}).json()["groups"]}


def labels(items):
    return [it["label"] for it in items]


def test_empty_query_lists_models_and_actions(admin_client):
    groups = search(admin_client)
    assert "Records" not in groups
    assert labels(groups["Models"]) == ["Posts", "Tags", "Authors", "Secrets", "Tickets", "Seats", "Reports", "Raw"]
    assert {"New post", "New author", "Toggle dark mode", "Keyboard shortcuts", "Audit log"} <= set(labels(groups["Actions"]))


def test_model_items(admin_client):
    posts = search(admin_client)["Models"][0]
    assert posts == {"label": "Posts", "sub": "Go to posts", "url": "/admin/post", "icon": "article", "keys": ["G", "P"]}
    assert search(admin_client)["Models"][1]["keys"] is None


def test_records(admin_client, db):
    db.add(post(title="Jamdani scarf", author=author(name="Nusrat")))
    (item,) = search(admin_client, "jamdani")["Records"]
    assert item == {"label": "Jamdani scarf", "sub": "Post #1", "url": "/admin/post/1", "icon": "article"}


def test_records_use_subtitle(admin_client, db):
    db.add(author(name="Nusrat Jahan", email="nusrat@example.com"))
    (item,) = search(admin_client, "nusrat j")["Records"]
    assert item["sub"] == "nusrat@example.com"


def test_records_search_every_model(admin_client, db):
    db.add(post(title="Needle post"), author(name="Needle person"))
    assert set(labels(search(admin_client, "needle")["Records"])) == {"Needle post", "Needle person"}


def test_records_are_capped(admin_client, db):
    db.add(*[post(title=f"Match {i}", author=author(name=f"Match author {i}")) for i in range(5)])
    db.add(*[author(name=f"Match solo {i}") for i in range(5)])
    records = search(admin_client, "match")["Records"]
    assert len([r for r in records if r["url"].startswith("/admin/post/")]) == 3, "3 per model"
    assert len(records) == 6


def test_query_filters_models_and_actions(admin_client):
    groups = search(admin_client, "auth")
    assert labels(groups["Models"]) == ["Authors"]
    assert labels(search(admin_client, "new po")["Actions"]) == ["New post"]
    assert labels(search(admin_client, "dark")["Actions"]) == ["Toggle dark mode"]


def test_commands(admin_client):
    actions = {it["label"]: it for it in search(admin_client)["Actions"]}
    assert actions["Toggle dark mode"]["cmd"] == "theme" and actions["Toggle dark mode"]["url"] is None
    assert actions["Keyboard shortcuts"]["cmd"] == "keys"
    assert actions["Audit log"]["url"] == "/admin/_log"


def test_no_matches(admin_client):
    assert search(admin_client, "zzzz") == {}
