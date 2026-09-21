"""Network guard inherited by Python subprocesses of the offline test runner.

This is a test accident guard, not a sandbox for executing untrusted code.
"""
import os
import sys

if os.environ.get("OPENMUSE_EVAL_ROOT"):
    def audit(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"}:
            # Count even an attempted call swallowed by application fallback.
            # Never record addresses, URLs, credentials or request payloads.
            path = os.path.join(os.environ["OPENMUSE_EVAL_ROOT"], "network-attempts")
            descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            try:
                os.write(descriptor, (event + "\n").encode())
            finally:
                os.close(descriptor)
            raise RuntimeError("Offline acceptance forbids network access; provide a fixture.")
    sys.addaudithook(audit)
