# Local type stubs

This directory contains the checked-in type surface for untyped dependencies. Jericho does not publish
PEP 561 metadata or stubs, so `typings/jericho/__init__.pyi` describes the subset used by Zorkinator.

Whenever code starts using another symbol, method, argument, or return value from Jericho, update the
stub in the same change. Keep it minimal and verify signatures against the installed pinned version.
Do not restore a global `ignore_missing_imports`; an untyped dependency should receive a local stub or a
narrow, module-specific exception with an explanation.
