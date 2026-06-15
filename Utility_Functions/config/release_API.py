import os

import requests
from requests_ntlm import HttpNtlmAuth

USERNAME = str(os.getenv("API_USERNAME", "tripearltech2"))
PASSWORD = str(os.getenv("API_PASSWORD"))

# Client/auth errors — retrying will not help until config or payload is fixed.
_NON_RETRYABLE_STATUS_CODES = frozenset({400, 401, 403, 404, 405, 422})


def release_api_succeeded(result: dict) -> bool:
    """True when HTTP 200 and response body contains ``{"value": true}``."""
    if not result.get("success"):
        return False
    response = result.get("response")
    if not isinstance(response, dict):
        return False
    return response.get("value") is True


def release_api_should_retry(result: dict) -> bool:
    """Retry transient/network/server failures; stop on auth and client errors."""
    if release_api_succeeded(result):
        return False

    status_code = result.get("status_code")
    if status_code in _NON_RETRYABLE_STATUS_CODES:
        return False

    return True


def release_api_failure_message(result: dict) -> str:
    """Human-readable summary for logs."""
    status_code = result.get("status_code")
    request_error = result.get("error")
    response = result.get("response")

    if request_error:
        return f"Request error: {request_error}"

    if status_code == 401:
        return (
            f"Authentication failed (HTTP 401). "
            "Check API_USERNAME, API_PASSWORD, and PO_API/SO_API URL."
        )
    if status_code == 403:
        return f"Access denied (HTTP 403). The API user may lack permission."

    if isinstance(response, dict):
        return f"HTTP {status_code}: response value={response.get('value')!r}"

    if response:
        text = str(response).strip()
        if len(text) > 200:
            text = text[:200] + "..."
        return f"HTTP {status_code}: {text!r}"

    return f"HTTP {status_code}: empty or non-JSON response"


def trigger_oder_release(
    payload: dict,
    api_url: str ,
    timeout: int = 30,
) -> dict:
    """Trigger order release API and return full response details."""
    try:
        response = requests.post(
            api_url,
            auth=HttpNtlmAuth(USERNAME, PASSWORD),
            json=payload,
            timeout=timeout,
        )

        try:
            response_body = response.json()
        except ValueError:
            response_body = response.text

        return {
            "success": response.status_code == 200,
            "status_code": response.status_code,
            "response": response_body,
            "error": None,
        }

    except requests.exceptions.RequestException as exc:
        return {
            "success": False,
            "status_code": None,
            "response": None,
            "error": str(exc),
        }

# release_result = trigger_oder_release(payload={"pO_No": "MC/PO-25266309"}, api_url=API_URL, timeout=30)

# print(release_result.get('response'))

