# Architecture and design artifacts

This folder contains the implementation-baseline architecture and Software Solution Design for version 1.0.0.

| Artifact | Purpose |
| --- | --- |
| `oem-mcp-streamlit-oci-architecture.drawio` | Editable draw.io source using official Oracle OCI Architecture Diagram Toolkit v24.2 stencil entries |
| `oem-mcp-streamlit-oci-architecture.svg` | README-ready vector render |
| `oem-mcp-streamlit-oci-architecture.png` | Portable raster render |
| `oem-mcp-streamlit-software-solution-design.docx` | Editable Software Solution Design |
| `oem-mcp-streamlit-software-solution-design.pdf` | Fixed-layout Software Solution Design render |

The diagram distinguishes required application resources from optional platform integrations. It is a reference architecture, not a Terraform plan. Open the `.drawio` file in diagrams.net/draw.io to change OCI resources, network boundaries, labels, and connectivity.

The SSD traces priorities 2–13 to the Streamlit workflows, source components, data stores, controls, deployment modes, failure behavior, tests, acceptance criteria, and known limitations. Priority 1 identity-provider integration is explicitly out of scope.

Official sources:

- [Oracle OCI Architecture Diagram Toolkit and graphics guidance](https://docs.oracle.com/en-us/iaas/Content/General/Reference/graphicsfordiagrams.htm)
- [Oracle Enterprise Manager 24ai MCP Server](https://docs.oracle.com/en/enterprise-manager/cloud-control/enterprise-manager-cloud-control/24.1/emadm/enterprise-manager-model-context-protocol-server.html)
- [Oracle OCI Generative AI OpenAI-compatible API](https://docs.oracle.com/en-us/iaas/Content/generative-ai/openai-compatible-api.htm)
