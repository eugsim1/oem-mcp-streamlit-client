# Security policy

## Supported version

Security fixes are applied to the latest release on `main`.

## Reporting

Do not open a public issue containing credentials, OEM endpoint internals, target names, SQL, session identifiers, or logs. Use GitHub's private vulnerability reporting feature for this repository.

## Operator responsibilities

- Keep the Streamlit listener on loopback behind an authenticated TLS reverse proxy.
- Use a dedicated least-privileged OEM account and review the live capability list.
- Keep TLS verification enabled and protect the configured CA bundle.
- Leave password values out of Git and prefer GUI entry.
- Protect the Linux account, environment file, data directory, log directory, and OEM audit records.
- Review any decision to enable mutating tools or non-SELECT SQL.

The client redacts known secret fields, but operators must still review diagnostic and history exports before sharing them.
