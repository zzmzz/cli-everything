"""HTTP client for {{ site_name }} API."""

import json
import os
from typing import Any, Optional

import requests


class APIError(Exception):
    """Raised when the API returns a non-success status."""

    def __init__(self, code: Any, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"API error {code}: {detail}")


class HttpClient:
    """Wraps requests for the {{ site_name }} backend."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        {{ auth_param }}: Optional[str] = None,
    ):
        self.base_url = (
            base_url
            or os.environ.get("{{ env_prefix }}_BASE_URL", "{{ default_base_url }}")
        ).rstrip("/")
        self.{{ auth_param }} = {{ auth_param }} or os.environ.get("{{ env_prefix }}_{{ auth_env_suffix }}", "")
        self.session = requests.Session()
        {% if auth_type == "cookie" %}
        if self.{{ auth_param }}:
            self.session.headers["Cookie"] = self.{{ auth_param }}
        {% elif auth_type == "bearer" %}
        if self.{{ auth_param }}:
            self.session.headers["Authorization"] = f"Bearer {self.{{ auth_param }}}"
        {% endif %}
        self.session.headers["Content-Type"] = "application/json"

    def _unwrap(self, resp: requests.Response) -> Any:
        resp.raise_for_status()
        data = resp.json()
        {% if envelope_success_field and "." in envelope_success_field %}
        # Nested envelope: {{ envelope_success_field }}
        _parts = "{{ envelope_success_field }}".split(".")
        _status = data
        for _p in _parts:
            _status = _status.get(_p, {}) if isinstance(_status, dict) else {}
        if _status != {{ envelope_success_value }}:
            detail = data.get("{{ envelope_success_field.split('.')[0] }}", {}).get("detail", str(data))
            raise APIError(_status, detail)
        return data.get("{{ envelope_data_field }}", {})
        {% elif envelope_success_field %}
        code = data.get("{{ envelope_success_field }}", -1)
        if code != {{ envelope_success_value }}:
            raise APIError(code, data.get("message", data.get("msg", str(data))))
        return data.get("{{ envelope_data_field }}", {})
        {% else %}
        return data
        {% endif %}

    def post(self, path: str, payload: Optional[dict] = None) -> Any:
        url = self.base_url + path
        resp = self.session.post(url, json=payload or {})
        return self._unwrap(resp)

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        url = self.base_url + path
        resp = self.session.get(url, params=params or {})
        return self._unwrap(resp)
