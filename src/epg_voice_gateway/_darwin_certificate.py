# SPDX-License-Identifier: MIT
# Copyright (c) 2026 victron-venus
"""Read certificate public-key sizes with Apple's native Security framework.

This module does not evaluate trust. Its caller must pass every certificate in
the chain already verified by TLS, including the selected trust anchor.
"""

from __future__ import annotations

import ctypes
import sys
from contextlib import ExitStack
from functools import lru_cache


class _Native:
    def __init__(self) -> None:
        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        pointer = ctypes.c_void_p
        self.cf.CFDataCreate.argtypes = [pointer, pointer, ctypes.c_long]
        self.cf.CFDataCreate.restype = pointer
        self.cf.CFRelease.argtypes = [pointer]
        self.cf.CFRelease.restype = None
        self.cf.CFDictionaryGetValue.argtypes = [pointer, pointer]
        self.cf.CFDictionaryGetValue.restype = pointer
        self.cf.CFGetTypeID.argtypes = [pointer]
        self.cf.CFGetTypeID.restype = ctypes.c_ulong
        for name in ("CFDictionaryGetTypeID", "CFStringGetTypeID", "CFNumberGetTypeID"):
            function = getattr(self.cf, name)
            function.argtypes = []
            function.restype = ctypes.c_ulong
        self.cf.CFEqual.argtypes = [pointer, pointer]
        self.cf.CFEqual.restype = ctypes.c_ubyte
        self.cf.CFNumberGetValue.argtypes = [pointer, ctypes.c_long, pointer]
        self.cf.CFNumberGetValue.restype = ctypes.c_ubyte
        self.security.SecCertificateCreateWithData.argtypes = [pointer, pointer]
        self.security.SecCertificateCreateWithData.restype = pointer
        self.security.SecCertificateCopyKey.argtypes = [pointer]
        self.security.SecCertificateCopyKey.restype = pointer
        self.security.SecKeyCopyAttributes.argtypes = [pointer]
        self.security.SecKeyCopyAttributes.restype = pointer
        self.key_type = self._constant("kSecAttrKeyType")
        self.key_bits = self._constant("kSecAttrKeySizeInBits")
        self.rsa = self._constant("kSecAttrKeyTypeRSA")
        self.ec = self._constant("kSecAttrKeyTypeECSECPrimeRandom")

    def _constant(self, name: str) -> int:
        value = ctypes.c_void_p.in_dll(self.security, name).value
        if not value:
            raise OSError("Security framework key metadata constants are unavailable")
        return value

    def own(self, stack: ExitStack, value: int | None) -> int:
        if not value:
            raise OSError("Security framework could not read certificate key metadata")
        stack.callback(self.cf.CFRelease, value)
        return value

    def meets_minimum(self, attributes: int) -> bool:
        if self.cf.CFGetTypeID(attributes) != self.cf.CFDictionaryGetTypeID():
            raise OSError("Security framework returned invalid key attributes")
        key_type = self.cf.CFDictionaryGetValue(attributes, self.key_type)
        key_bits = self.cf.CFDictionaryGetValue(attributes, self.key_bits)
        if not key_type or not key_bits:
            raise OSError("Security framework returned incomplete key attributes")
        if (
            self.cf.CFGetTypeID(key_type) != self.cf.CFStringGetTypeID()
            or self.cf.CFGetTypeID(key_bits) != self.cf.CFNumberGetTypeID()
        ):
            raise OSError("Security framework returned invalid key attribute types")
        bits = ctypes.c_int64()
        # kCFNumberSInt64Type = 4: conversion must be exact, not merely writable.
        if not self.cf.CFNumberGetValue(key_bits, 4, ctypes.byref(bits)) or bits.value <= 0:
            raise OSError("Security framework returned an invalid key size")
        if self.cf.CFEqual(key_type, self.rsa):
            return bits.value >= 2048
        if self.cf.CFEqual(key_type, self.ec):
            return bits.value >= 224
        return False


@lru_cache(maxsize=1)
def _native() -> _Native:
    if sys.platform != "darwin":
        raise OSError("Native certificate key inspection requires macOS")
    return _Native()


def certificate_key_meets_minimum(der: bytes) -> bool:
    """Check RSA >= 2048 or EC >= 224 bits without changing certificate trust.

    Unknown or undersized keys return False. Malformed certificates raise
    ValueError; unavailable native metadata raises OSError. No input is logged.
    """
    if not isinstance(der, bytes) or not der:
        raise ValueError("Certificate must be nonempty DER bytes")
    native = _native()
    with ExitStack() as stack:
        buffer = ctypes.create_string_buffer(der)
        data = native.own(stack, native.cf.CFDataCreate(None, buffer, len(der)))
        certificate = native.security.SecCertificateCreateWithData(None, data)
        if not certificate:
            raise ValueError("Certificate is not valid DER")
        native.own(stack, certificate)
        key = native.own(stack, native.security.SecCertificateCopyKey(certificate))
        attributes = native.own(stack, native.security.SecKeyCopyAttributes(key))
        return native.meets_minimum(attributes)
