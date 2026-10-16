"""Diagnose unsupported fixture keys in the same native provider used by ssl.

This is test-only inspection of generated public certificates. It never changes
provider settings, trust stores, or the product's mandatory rejection checks.
"""
import ctypes as c
import ctypes.util
import ssl
from contextlib import ExitStack

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def native_certificate_results(chains):
    library = ctypes.util.find_library("crypto")
    assert library, "Native libcrypto is required for the unsupported-key diagnosis"
    lib = c.CDLL(library)

    def bind(name, result, arguments):
        function = getattr(lib, name)
        function.restype = result
        function.argtypes = arguments
        return function

    pointer = c.c_void_p
    version = bind("OpenSSL_version", c.c_char_p, [c.c_int])(0).decode()
    assert version == ssl.OPENSSL_VERSION, "Diagnostic provider differs from Python ssl"
    parse = bind("d2i_X509", pointer, [pointer, c.POINTER(pointer), c.c_long])
    free_certificate = bind("X509_free", None, [pointer])
    get_key = bind("X509_get_pubkey", pointer, [pointer])
    free_key = bind("EVP_PKEY_free", None, [pointer])
    verify = bind("X509_verify", c.c_int, [pointer, pointer])
    issued = bind("X509_check_issued", c.c_int, [pointer, pointer])
    key_bits = bind("EVP_PKEY_get_bits", c.c_int, [pointer])
    get_error = bind("ERR_get_error", c.c_ulong, [])

    def errors():
        result = []
        while value := get_error():
            result.append({"library": (value >> 23) & 255, "reason": value & 0x7FFFFF})
        return result

    results = {"provider": version, "cases": {}}
    for name in ("strong", "strong-ec", "weak-ec-root"):
        leaf_path, _, root_path = chains[name]
        root = x509.load_pem_x509_certificate(root_path.read_bytes())
        leaf = x509.load_pem_x509_certificate(leaf_path.read_bytes())
        # Independent provider proves the issuer and signatures are valid.
        root.verify_directly_issued_by(root)
        leaf.verify_directly_issued_by(root)
        with ExitStack() as cleanup:
            certificates = []
            for certificate in (root, leaf):
                data = certificate.public_bytes(serialization.Encoding.DER)
                buffer = c.create_string_buffer(data)
                cursor = pointer(c.addressof(buffer))
                parsed = parse(None, c.byref(cursor), len(data))
                assert parsed, "Native certificate DER parsing failed"
                cleanup.callback(free_certificate, parsed)
                certificates.append(parsed)
            native_root, native_leaf = certificates
            errors()
            key = get_key(native_root)
            row = {"key_decoded": bool(key), "key_errors": errors()}
            if key:
                cleanup.callback(free_key, key)
                row["bits"] = key_bits(key)
                row["root_signature"] = verify(native_root, key)
                row["root_errors"] = errors()
                row["leaf_signature"] = verify(native_leaf, key)
                row["leaf_errors"] = errors()
            row["check_issued"] = issued(native_root, native_leaf)
            row["issuer_errors"] = errors()
            results["cases"][name] = row
    return results


def assert_native_ec192_unsupported(chains, error):
    """Allow only proven EC192 decode rejection, never arbitrary CA/name failure."""
    assert error.verify_code == 20, "Unexpected verification failure"
    root = x509.load_pem_x509_certificate(chains["weak-ec-root"][2].read_bytes())
    key = root.public_key()
    assert isinstance(key, ec.EllipticCurvePublicKey) and isinstance(key.curve, ec.SECP192R1)
    result = native_certificate_results(chains)
    for name, bits in (("strong", 2048), ("strong-ec", 256)):
        row = result["cases"][name]
        assert row["key_decoded"] and row["bits"] == bits
        assert row["root_signature"] == row["leaf_signature"] == 1
        assert row["check_issued"] == 0
        assert not row["key_errors"] + row["root_errors"] + row["leaf_errors"] + row["issuer_errors"]
    row = result["cases"]["weak-ec-root"]
    # OpenSSL EVP_R_DECODE_ERROR (114), ERR_LIB_EVP (6),
    # X509_V_ERR_NO_ISSUER_PUBLIC_KEY (24): this root key is unsupported.
    assert not row["key_decoded"] and row["check_issued"] == 24
    assert row["key_errors"] and row["issuer_errors"]
    assert all(item == {"library": 6, "reason": 114}
               for item in row["key_errors"] + row["issuer_errors"])
    return result
