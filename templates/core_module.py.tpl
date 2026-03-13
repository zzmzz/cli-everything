"""{{ domain_name | capitalize }} API module."""

from __future__ import annotations

from typing import Any, Optional

from ..utils.http_client import HttpClient

PREFIX = "{{ domain_prefix }}"

{% for endpoint in endpoints %}

def {{ endpoint.func_name }}(
    client: HttpClient,
    {% for param in endpoint.params %}
    {{ param.name }}: {{ param.python_type }} = {{ param.default }},
    {% endfor %}
) -> {{ endpoint.return_type }}:
    """{{ endpoint.description }}"""
    {% if endpoint.method == "POST" %}
    payload: dict[str, Any] = {}
    {% for param in endpoint.params %}
    {% if param.required %}
    payload["{{ param.api_key }}"] = {{ param.name }}
    {% else %}
    if {{ param.name }} is not None:
        payload["{{ param.api_key }}"] = {{ param.name }}
    {% endif %}
    {% endfor %}
    {% if endpoint.is_list %}
    body = client.post("{{ endpoint.path }}", payload)
    items = body.get("list") or body.get("items") or body.get("records") or []
    return {"total": body.get("total", len(items)), "items": items}
    {% else %}
    return client.post("{{ endpoint.path }}", payload)
    {% endif %}
    {% elif endpoint.method == "GET" %}
    params: dict[str, Any] = {}
    {% for param in endpoint.params %}
    if {{ param.name }} is not None:
        params["{{ param.api_key }}"] = {{ param.name }}
    {% endfor %}
    return client.get("{{ endpoint.path }}", params)
    {% endif %}
{% endfor %}
