# OEM MCP Streamlit Client

A security-conscious Streamlit GUI for connecting to the Oracle Enterprise Manager 24ai Model Context Protocol (MCP) server, discovering the operations authorized for the signed-in Enterprise Manager account, invoking those operations, and reviewing Linux/OEM metrics and an audit-friendly local history.

The client implements Oracle's HTTP/JSON-RPC transport directly. It does not require an LLM, an external AI service, or an MCP proxy.

## What the GUI provides

- **Connection** — OEM endpoint, username, password, protocol version, request timeout, TLS verification, custom CA bundle, and reusable non-secret profiles.
- **Capabilities** — initializes MCP and retrieves all advertised tools, prompts, resources, and resource templates, including pagination and input schemas.
- **Request** — builds an input form from the selected tool's JSON Schema, requires confirmation, validates the input, invokes `tools/call`, and renders structured results.
- **Metrics** — shows the Streamlit Linux server's CPU/load, memory, disk, network, and process metrics. It also ranks authorized OEM tools for Linux targets, databases, and host-to-database relationships.
- **History & logs** — timestamped connection and execution history in SQLite, JSON downloads, and a rotating application log.
- **Diagnostics** — negotiated protocol/server details, safe configuration state, MCP ping, and redacted diagnostic export.

OEM returns operations according to the account's privileges. The GUI discovers that surface at connection time instead of assuming fixed tool names. Oracle currently documents tools as the primary server feature; prompts, resources, and resource templates may be empty, but the client requests and displays all four capability types.

## Supported OEM MCP behavior

This project targets Enterprise Manager 24ai Release 24.1.0.12 or newer:

- Endpoint: `https://OEM_HOST:OEM_HTTPS_PORT/em/api/mcp`
- Transport: JSON-RPC 2.0 in HTTP `POST` requests using `application/json`
- Authentication: Enterprise Manager username/password through HTTP Basic Authentication
- MCP protocol versions: `2025-11-25` and `2025-06-18`
- Lifecycle: `initialize`, `notifications/initialized`, discovery calls, then tool invocation

Oracle documents that the OEM authorization, validation, audit, and business-rule layers still apply. This client cannot grant additional OEM access.

## Security defaults

- The server listens on `127.0.0.1` by default. Use an authenticated TLS reverse proxy for shared access.
- The OEM password remains in Streamlit session memory. It is never saved to profiles, SQLite history, or application logs.
- TLS verification is enabled. A private CA bundle can be configured without disabling verification.
- Non-secret endpoint profiles are written with restrictive permissions.
- Tools must be advertised as/read like read-only operations by default.
- `ExecuteSql` accepts one `SELECT` or `WITH` statement by default; DDL, DML, and PL/SQL require a deliberate server-side configuration change.
- Binary MCP content is not rendered or downloaded automatically.
- The history database fingerprints usernames and sanitizes endpoints.

Streamlit is an application framework, not an identity gateway. Do not expose this listener directly to an untrusted network. Put NGINX, Apache, an OCI Load Balancer, or another gateway with TLS and user authentication in front of it.

## Prerequisites on Oracle Linux 8

- Network reachability from the Linux host to the OEM HTTPS console
- An OEM 24ai account authorized for the operations you want to use
- Git
- Python 3.9 through 3.13; Python 3.11 is recommended
- `curl`, `ss` (from `iproute`), and standard GNU utilities

Example packages:

```bash
sudo dnf install -y git python3.11 python3.11-pip curl iproute procps-ng
```

The project deliberately rejects old Python versions rather than allowing `pip` to select an obsolete Streamlit release.

## Clean clone

The repository is private. Configure a GitHub SSH key or another approved GitHub authentication method first.

```bash
export PROJECT_DIR=/u03/home/oracle/oci-go--dev-projects/oem-mcp-streamlit-client
git clone git@github.com:eugsim1/oem-mcp-streamlit-client.git "$PROJECT_DIR"
cd "$PROJECT_DIR"
git status --short
```

Do not copy `/path/to/...` examples literally. The `PROJECT_DIR` variable above is the real destination used by every command that follows.

## Deployment A — manual standalone process

This mode runs under the current Linux user and does not create a service.

### 1. Install

```bash
cd "$PROJECT_DIR"
scripts/install-manual.sh --python-bin python3.11
```

On the first run, the installer automatically creates:

```text
$PROJECT_DIR/.runtime/oem-mcp-streamlit.env
```

It is mode `0600`, ignored by Git, and preserved during later installs. Edit the endpoint and optional defaults:

```bash
vi "$PROJECT_DIR/.runtime/oem-mcp-streamlit.env"
```

Recommended values:

```dotenv
STREAMLIT_ADDRESS=127.0.0.1
STREAMLIT_PORT=8501
OEM_MCP_ENDPOINT=https://oem.example.com:7803/em/api/mcp
OEM_MCP_USERNAME=
OEM_MCP_PASSWORD=
OEM_MCP_PROTOCOL_VERSION=2025-11-25
OEM_MCP_VERIFY_TLS=true
OEM_MCP_CA_BUNDLE=
OEM_MCP_TIMEOUT_SECONDS=60
```

Leave `OEM_MCP_PASSWORD` blank to enter it in the GUI. If an unattended diagnostic requires a password, set it only in the protected runtime file and review the host's access controls.

### 2. Start on the configured port

```bash
cd "$PROJECT_DIR"
scripts/start-standalone.sh \
  --env-file "$PROJECT_DIR/.runtime/oem-mcp-streamlit.env" \
  --port 8501 \
  --address 127.0.0.1 \
  --wait-seconds 60
```

### 3. Start on another configurable port

The command-line port overrides `STREAMLIT_PORT`:

```bash
scripts/start-standalone.sh \
  --env-file "$PROJECT_DIR/.runtime/oem-mcp-streamlit.env" \
  --port 8502 \
  --address 127.0.0.1
```

Use a different PID file for each port. To bind beyond loopback, add `--allow-non-loopback` only after configuring an authenticated TLS reverse proxy and firewall.

### 4. Check, test, and stop

```bash
scripts/status-standalone.sh --port 8502
scripts/smoke-test.sh --address 127.0.0.1 --port 8502
scripts/stop-standalone.sh --port 8502
```

The standalone log is `logs/streamlit-standalone-PORT.log`; application events are in `logs/oem-mcp-streamlit.log`.

## Deployment B — systemd service

This mode installs dependencies into `/opt`, creates protected runtime directories, and configures restart-on-failure.

### 1. Install the unit and Python environment

```bash
cd "$PROJECT_DIR"
sudo scripts/install-systemd.sh \
  --service-user oracle \
  --service-group oinstall \
  --python-bin /usr/bin/python3.11 \
  --address 127.0.0.1 \
  --port 8502
```

On the first run, the installer automatically creates:

```text
/etc/oem-mcp-streamlit/oem-mcp-streamlit.env
```

The file is owned by `root:oinstall`, mode `0640`, and is preserved on reinstall. It also sets absolute runtime locations:

- data/history: `/var/lib/oem-mcp-streamlit`
- logs: `/var/log/oem-mcp-streamlit`
- virtual environment: `/opt/oem-mcp-streamlit/venv`

### 2. Configure OEM

```bash
sudo vi /etc/oem-mcp-streamlit/oem-mcp-streamlit.env
sudo grep -E '^(STREAMLIT_ADDRESS|STREAMLIT_PORT|OEM_MCP_ENDPOINT|OEM_MCP_PROTOCOL_VERSION|OEM_MCP_VERIFY_TLS)=' \
  /etc/oem-mcp-streamlit/oem-mcp-streamlit.env
```

Do not print the password. Prefer leaving `OEM_MCP_PASSWORD=` empty and entering it in the GUI.

### 3. Start and verify

```bash
sudo scripts/start-service.sh
sudo scripts/status-service.sh
scripts/smoke-test.sh --address 127.0.0.1 --port 8502
sudo journalctl -u oem-mcp-streamlit.service -n 100 --no-pager
```

### 4. Stop or restart

```bash
sudo scripts/stop-service.sh
sudo scripts/restart-service.sh
```

### 5. Change the service port

The port is rendered into the unit, so rerun the installer and restart:

```bash
sudo scripts/install-systemd.sh \
  --service-user oracle \
  --service-group oinstall \
  --python-bin /usr/bin/python3.11 \
  --address 127.0.0.1 \
  --port 8510
sudo scripts/restart-service.sh
scripts/smoke-test.sh --address 127.0.0.1 --port 8510
```

## Clean update/redeployment

### Manual mode

```bash
cd "$PROJECT_DIR"
scripts/stop-standalone.sh --port 8502
git status --short
git pull --ff-only
scripts/install-manual.sh --python-bin python3.11
scripts/start-standalone.sh \
  --env-file "$PROJECT_DIR/.runtime/oem-mcp-streamlit.env" \
  --address 127.0.0.1 \
  --port 8502
scripts/smoke-test.sh --address 127.0.0.1 --port 8502
```

### systemd mode

```bash
cd "$PROJECT_DIR"
git status --short
git pull --ff-only
sudo scripts/install-systemd.sh \
  --service-user oracle \
  --service-group oinstall \
  --python-bin /usr/bin/python3.11 \
  --address 127.0.0.1 \
  --port 8502
sudo scripts/restart-service.sh
scripts/smoke-test.sh --address 127.0.0.1 --port 8502
```

The installers preserve the environment files. Review local changes before `git pull`; do not discard work you intend to keep.

## Connection workflow in the GUI

1. Open **Connection**.
2. Enter `https://OEM_HOST:OEM_PORT/em/api/mcp`, the OEM username, and password.
3. Keep TLS verification enabled; select a custom CA bundle if OEM uses an internal CA.
4. Select protocol `2025-11-25` (or `2025-06-18` for a server that requires it).
5. Click **Connect, initialize, and discover**.
6. Review every authorized operation and schema in **Capabilities**.
7. Run an operation from **Request**, or select discovered host/database/relationship operations under **Metrics**.
8. Review timestamps, timings, and status in **History & logs**.

## Diagnostics and tests

Local, redacted diagnostics:

```bash
scripts/diagnose.sh --env-file "$PROJECT_DIR/.runtime/oem-mcp-streamlit.env"
```

End-to-end OEM initialize/discovery test (requires `OEM_MCP_USERNAME` and `OEM_MCP_PASSWORD` in the protected environment file):

```bash
scripts/diagnose.sh \
  --connect \
  --env-file "$PROJECT_DIR/.runtime/oem-mcp-streamlit.env"
```

Developer test suite:

```bash
scripts/install-manual.sh --python-bin python3.11 --with-dev
scripts/test.sh
```

The test suite uses a fake MCP transport and does not need an OEM server. The `--connect` diagnostic is the explicit real-server test.

## Troubleshooting

### `ERROR: environment file is not readable`

Run the matching installer first. Manual installation creates `$PROJECT_DIR/.runtime/oem-mcp-streamlit.env`; systemd installation creates `/etc/oem-mcp-streamlit/oem-mcp-streamlit.env`. For the systemd file, use `sudo` to inspect permissions.

### Streamlit cannot install or only very old releases are listed

The Python interpreter is too old or the configured package index is stale. Verify:

```bash
python3.11 --version
python3.11 -m pip config list
python3.11 -m pip index versions streamlit
```

Then rerun the installer with `--python-bin /usr/bin/python3.11`. Do not work around the version check by installing an obsolete Streamlit release.

### OEM connection fails

```bash
curl --fail --silent --show-error \
  --cacert /path/to/company-ca.pem \
  -o /dev/null \
  https://oem.example.com:7803/em/api/mcp
```

An HTTP authentication response still proves TLS/network reachability. Verify the OEM version, endpoint path, account privileges, proxy rules, and CA chain. Never use `curl -k` with production credentials.

### No tools, metrics, or relationships appear

The GUI only displays the surface that OEM returns for the authenticated user. Check **Capabilities** and **Diagnostics**, then have an OEM administrator review the account's target and operation privileges. Tool names may vary by OEM version, so the metrics views rank the live discovery results instead of hard-coding undocumented names.

### Service fails to start

```bash
sudo systemctl status oem-mcp-streamlit.service --no-pager -l
sudo journalctl -u oem-mcp-streamlit.service -n 200 --no-pager
sudo systemd-analyze verify /etc/systemd/system/oem-mcp-streamlit.service
sudo -u oracle /opt/oem-mcp-streamlit/venv/bin/python -m compileall -q "$PROJECT_DIR/app.py" "$PROJECT_DIR/oem_mcp_client"
```

## Deliberate limitations

- No OAuth or SSE transport is implemented because Oracle's documented OEM MCP server uses Basic Authentication and stateless HTTP `POST` messages.
- The client is not a general AI agent and does not translate natural language into operations.
- Local history supports operator troubleshooting; it is not a replacement for OEM's authoritative server-side audit records.
- Disabling TLS verification, enabling mutating tools, or enabling non-SELECT SQL weakens the safety boundary and requires an explicit operational security review.

## Sources

- [Oracle blog: Oracle Enterprise Manager MCP Server—Bringing Enterprise Manager Data to AI Agents](https://blogs.oracle.com/observability/oracle-enterprise-manager-mcp-server-bringing-enterprise-manager-data-to-ai-agents)
- [Oracle Enterprise Manager 24ai MCP Server documentation](https://docs.oracle.com/en/enterprise-manager/cloud-control/enterprise-manager-cloud-control/24.1/emadm/enterprise-manager-model-context-protocol-server.html)
- [MCP lifecycle specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [MCP tools specification (2025-06-18)](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- [MCP transport specification (2025-06-18)](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)

## License and support

MIT licensed. This is an independent client, not an Oracle product. See [DISCLAIMER.md](DISCLAIMER.md) and [SECURITY.md](SECURITY.md).
