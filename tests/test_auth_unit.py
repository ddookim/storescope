import hashlib

from api.auth import _hash_key, generate_api_key


def test_hash_key_returns_sha256_hex():
    result = _hash_key("test_key")
    assert result == hashlib.sha256(b"test_key").hexdigest()
    assert len(result) == 64


def test_hash_key_deterministic():
    assert _hash_key("same") == _hash_key("same")


def test_hash_key_different_inputs_produce_different_hashes():
    assert _hash_key("key1") != _hash_key("key2")


def test_generate_api_key_has_si_prefix():
    raw, _ = generate_api_key()
    assert raw.startswith("si_")


def test_generate_api_key_hash_matches():
    raw, hashed = generate_api_key()
    assert hashed == _hash_key(raw)
    assert len(hashed) == 64


def test_generate_api_key_is_unique():
    raw1, _ = generate_api_key()
    raw2, _ = generate_api_key()
    assert raw1 != raw2
