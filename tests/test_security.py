import pytest

from fastyfold.security import SESSION_MAX_AGE, Signer, hash_password, verify_password


######################################################################
# Session cookie
######################################################################
def test_signer_requires_secret_key():
    with pytest.raises(ValueError, match="secret_key"):
        Signer("")


def test_signer_round_trip():
    s = Signer("k")
    assert s.load(s.dump("nusrat", "sid-1")) == ("nusrat", "sid-1")


def test_session_ids_are_unique():
    from fastyfold.security import new_session_id
    assert len({new_session_id() for _ in range(100)}) == 100 and len(new_session_id()) >= 40


@pytest.mark.parametrize("cookie", [None, "", "garbage", "eyJ1IjoiYWRtaW4ifQ.bad.sig"])
def test_signer_rejects_missing_or_malformed(cookie):
    assert Signer("k").load(cookie) is None


def test_signer_rejects_tampered_cookie():
    s = Signer("k")
    cookie = s.dump("editor", "sid")
    assert s.load(cookie[:-2] + ("aa" if not cookie.endswith("aa") else "bb")) is None


def test_signer_rejects_cookie_signed_with_other_key():
    assert Signer("k").load(Signer("other").dump("admin", "sid")) is None


def test_signer_rejects_expired_cookie(monkeypatch):
    import itsdangerous.timed

    s = Signer("k")
    cookie = s.dump("admin", "sid")
    real = itsdangerous.timed.time.time
    monkeypatch.setattr(itsdangerous.timed.time, "time", lambda: real() + SESSION_MAX_AGE + 10)
    assert s.load(cookie) is None


######################################################################
# CSRF
######################################################################
def test_csrf_is_bound_to_the_cookie():
    s = Signer("k")
    a, b = s.dump("admin", "one"), s.dump("admin", "two")
    assert s.csrf(a) == s.csrf(a)
    assert s.csrf(a) != s.csrf(b)
    assert s.csrf_ok(a, s.csrf(a))
    assert not s.csrf_ok(b, s.csrf(a))


def test_csrf_depends_on_secret_key():
    assert Signer("k").csrf("cookie") != Signer("other").csrf("cookie")


@pytest.mark.parametrize("cookie, token", [(None, "x"), ("", "x"), ("c", None), ("c", ""), ("c", "0" * 64)])
def test_csrf_rejects_missing_or_wrong(cookie, token):
    assert not Signer("k").csrf_ok(cookie, token)


######################################################################
# Passwords
######################################################################
def test_hash_password_format():
    algo, iterations, salt, digest = hash_password("pw", iterations=1000).split("$")
    assert algo == "pbkdf2" and iterations == "1000" and len(salt) == 32 and len(digest) == 64


def test_hash_password_is_salted():
    assert hash_password("pw", iterations=1000) != hash_password("pw", iterations=1000)


def test_verify_password():
    stored = hash_password("correct horse", iterations=1000)
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)


@pytest.mark.parametrize("stored", ["", "!", "plain-text", "a$b$c"])
def test_verify_password_rejects_malformed_hash(stored):
    assert not verify_password("pw", stored)
