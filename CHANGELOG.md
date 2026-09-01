# Changelog

## 1.1.2 - 2026-09-01

- Add a standard-library OCI Generative AI API-key diagnostic that validates endpoint values, protects secrets, bypasses proxies on demand, and distinguishes HTML gateway responses from OCI JSON errors.
- Correct the recommended Frankfurt API-key endpoint and document both the project-free regional API-key route and the project-based OpenAI-compatible route.
- Expand the debugging runbook for IAM, API-key secrets, model availability, proxy/egress checks, direct tests, and HTTP response interpretation.
- Strengthen the operational disclaimer for diagnostics, credentials, model output, costs, and production use.

## 1.1.1 - 2026-09-01

- Document the complete Frankfurt Generative AI API-key workflow, including project creation, regional key creation, specific-key IAM policy, protected secret storage, and validation.
- Distinguish the Generative AI project OCID, API-key OCID, and one-time API-key secret to prevent authentication and policy misconfiguration.
- Explain OCI SDK profiles, their operator-defined names, and which authentication modes use or ignore `OCI_GENAI_PROFILE`.

## 1.1.0 - 2026-08-31

- Turn the Assistant tab into an explicit NLP workflow with selectable OEM-tool/SQL strategy, editable generated arguments, separate SQL display, and confirmation before execution.
- Add OCI Generative AI answer synthesis grounded in a redacted, bounded OEM result while retaining the raw result for verification.
- Support OCI Generative AI API-key, OCI CLI session, instance-principal, and resource-principal authentication through the official OpenAI-compatible client and OCI authentication helper.
- Correct the Frankfurt configuration with a required Generative AI project OCID, current model examples, protected-secret guidance, IAM policies, deployment restart steps, and troubleshooting.
- Clarify that Capabilities is discovery-only and document end-to-end examples for incidents, down targets, recent jobs, target status, and read-only `ExecuteSql`.

## 1.0.0 - 2026-08-27

- Implement requested priorities 2–13: live operations, saved dashboards/runbooks, topology, read-only SQL, incident investigation, assistant planning, policy, exact-request approvals, multi-OEM connections, background jobs, schedules/reports/alerts, and usage/cost accounting.
- Add a shared deny-first policy, approval, safety, redaction, history, and usage path for focused executions.
- Add an editable OCI draw.io reference architecture using official OCI Architecture Diagram Toolkit v24.2 stencils, plus SVG and PNG renders.
- Add a privacy-scrubbed, accessibility-audited Software Solution Design in DOCX and PDF formats.
- Expand tests for the new modules, safety boundaries, persistence, and every Streamlit feature tab.

## 0.1.1 - 2026-08-27

- Add `location.md` with the sanitized absolute local source path and private repository URL.

## 0.1.0 - 2026-08-27

- Add the Streamlit connection, capabilities, request, metrics, relationship, history/log, and diagnostics interfaces.
- Implement OEM MCP initialization, session headers, capability discovery with pagination, tool invocation, and protocol validation.
- Add safe profile storage, redacted SQLite history, rotating logs, read-only tool gates, and SELECT-only `ExecuteSql` validation.
- Add Oracle Linux 8 standalone and hardened systemd deployment modes with configurable listener ports.
- Add smoke, diagnostic, lint, unit-test, release, and dependency-update automation.
