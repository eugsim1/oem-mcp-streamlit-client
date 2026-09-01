from __future__ import annotations

import json

import pytest

from scripts.oci_genai_diagnostic import (
    API_KEY_PATH,
    PROJECT_PATH,
    ConfigurationError,
    DiagnosticConfig,
    build_request,
    is_html_response,
    load_env_file,
    validate_config,
)


def config(**overrides: object) -> DiagnosticConfig:
    values = {
        "endpoint": f"https://inference.generativeai.eu-frankfurt-1.oci.oraclecloud.com{API_KEY_PATH}",
        "model": "openai.gpt-oss-120b",
        "auth_mode": "api_key",
        "project_ocid": "",
        "api_key": "secret-value",
        "timeout_seconds": 120,
    }
    values.update(overrides)
    return DiagnosticConfig(**values)


def request_headers(request: object) -> dict[str, str]:
    return {name.lower(): value for name, value in request.header_items()}


def test_regional_api_key_endpoint_does_not_send_project_header() -> None:
    validated = validate_config(config(project_ocid="ocid1.generativeaiproject.oc1.eu-frankfurt-1.example"))
    request = build_request(validated)

    assert request.full_url.endswith(f"{API_KEY_PATH}/responses")
    assert "openai-project" not in request_headers(request)
    assert json.loads(request.data) == {
        "model": "openai.gpt-oss-120b",
        "input": "Reply with exactly OCI_GENAI_OK",
    }


def test_project_endpoint_requires_and_sends_project_ocid() -> None:
    project_ocid = "ocid1.generativeaiproject.oc1.eu-frankfurt-1.example"
    validated = validate_config(
        config(
            endpoint=f"https://inference.generativeai.eu-frankfurt-1.oci.oraclecloud.com{PROJECT_PATH}",
            project_ocid=project_ocid,
        )
    )

    assert request_headers(build_request(validated))["openai-project"] == project_ocid

    with pytest.raises(ConfigurationError, match="requires OCI_GENAI_PROJECT_OCID"):
        validate_config(
            config(
                endpoint=f"https://inference.generativeai.eu-frankfurt-1.oci.oraclecloud.com{PROJECT_PATH}",
                project_ocid="",
            )
        )


def test_markdown_endpoint_is_rejected_before_network_access() -> None:
    endpoint = "[https://inference.generativeai.eu-frankfurt-1.oci.oraclecloud.com](https://example.invalid)"
    with pytest.raises(ConfigurationError, match="Markdown"):
        validate_config(config(endpoint=endpoint))


def test_api_key_ocid_is_rejected_as_secret() -> None:
    with pytest.raises(ConfigurationError, match="one-time generated secret"):
        validate_config(config(api_key="ocid1.generativeaiapikey.oc1.eu-frankfurt-1.example"))


def test_non_oci_hostname_is_rejected_before_secret_is_sent() -> None:
    with pytest.raises(ConfigurationError, match="OCI Generative AI inference hostname"):
        validate_config(config(endpoint=f"https://example.invalid{API_KEY_PATH}"))


def test_env_file_parser_does_not_execute_shell(tmp_path: object) -> None:
    env_file = tmp_path / "diagnostic.env"
    env_file.write_text(
        "# comment\nexport OCI_GENAI_MODEL='openai.gpt-oss-120b'\nVALUE=$(must_not_execute)\n",
        encoding="utf-8",
    )

    values = load_env_file(env_file)

    assert values["OCI_GENAI_MODEL"] == "openai.gpt-oss-120b"
    assert values["VALUE"] == "$(must_not_execute)"


def test_html_detection_uses_content_type_or_body() -> None:
    from scripts.oci_genai_diagnostic import HttpResult

    assert is_html_response(HttpResult(400, "text/html", "", ""))
    assert is_html_response(HttpResult(400, "", "<html><body>Bad Request</body></html>", ""))
    assert not is_html_response(HttpResult(404, "application/json", '{"code":"404"}', ""))
