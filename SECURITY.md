# Security policy

OpenMuse is **Alpha software**. Only the latest reviewed release receives fixes;
there is no security SLA. Do not give it production publishing credentials unless
you understand and accept the remaining risks.

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** option for this repository if available.
If private reporting is unavailable, open an issue requesting a private contact
without including exploit details, credentials, private articles, or logs.
Do not post keys in public issues. Revoke exposed keys immediately.

## Trust boundaries

- Pack signatures verify publisher identity/integrity, not code safety. Review
  downloaded Skills and scripts. Packs are not an untrusted-code sandbox.
- Model responses, retrieved content and memory cannot grant publishing approval.
  Default workflows are dry-run; external writes require explicit human approval.
- A lost response after an external write may require manual reconciliation.
  OpenMuse does not promise exactly-once remote delivery.
- `.env`, session history, model snapshots and local memory may contain sensitive
  data. Keep them out of version control and shared backups. Forgetting a memory
  does not erase past conversations or remote provider logs.
- The offline acceptance guard prevents accidental Python network access during
  tests. It is not a security sandbox for hostile dependencies or native code.
- Local preview is read-only, but article content is still untrusted. Do not expose
  its listening port publicly. WeChat may render HTML differently.

## Supported release checks

CI targets Python 3.10–3.12 on Linux and Python 3.12 on macOS. Windows is
experimental: cross-process locking and process-tree cancellation are not yet
covered by the release matrix. Passing CI does not certify provider availability,
generated content accuracy, or safety of third-party Packs.
