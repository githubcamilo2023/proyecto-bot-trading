import pytest

from tradebot.integrity import (
    KEY_ENV,
    MIN_KEY_LEN,
    MissingKeyError,
    canonical,
    load_key,
    sha256_hex,
    sign,
    verify,
)

KEY = b"k" * 40


def test_load_key_requires_a_long_enough_key():
    assert load_key({KEY_ENV: "x" * MIN_KEY_LEN}) == b"x" * MIN_KEY_LEN
    for bad in ({}, {KEY_ENV: ""}, {KEY_ENV: "x" * (MIN_KEY_LEN - 1)}):
        with pytest.raises(MissingKeyError, match=KEY_ENV):
            load_key(bad)


def test_load_key_reads_the_real_environment(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "y" * 40)
    assert load_key() == b"y" * 40
    monkeypatch.delenv(KEY_ENV)
    with pytest.raises(MissingKeyError):
        load_key()


def test_canonical_json_is_order_independent():
    assert canonical({"b": 1, "a": 2}) == canonical({"a": 2, "b": 1})


def test_sha256_known_vector():
    assert sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_signature_is_bound_to_key_domain_and_payload():
    mac = sign(KEY, "dominio-a", {"x": 1})
    assert verify(KEY, "dominio-a", {"x": 1}, mac)
    assert not verify(KEY, "dominio-a", {"x": 2}, mac)  # payload alterado
    assert not verify(KEY, "dominio-b", {"x": 1}, mac)  # otro dominio
    assert not verify(b"z" * 40, "dominio-a", {"x": 1}, mac)  # otra clave
