"""Unit tests for {{ site_name }} core modules."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from cli_anything.{{ site_name }}.utils.http_client import HttpClient, APIError
{% for domain in domains %}
from cli_anything.{{ site_name }}.core import {{ domain.name }}
{% endfor %}


@pytest.fixture
def mock_client():
    """Create a mock HttpClient."""
    client = MagicMock(spec=HttpClient)
    client.base_url = "{{ default_base_url }}"
    return client


{% for domain in domains %}
class Test{{ domain.name | capitalize }}:
    """Tests for {{ domain.name }} module."""

    {% for endpoint in domain.endpoints %}
    def test_{{ endpoint.func_name }}(self, mock_client):
        """Test {{ endpoint.func_name }}."""
        mock_client.post.return_value = {{ endpoint.mock_response }}
        result = {{ domain.name }}.{{ endpoint.func_name }}(mock_client{{ endpoint.test_args }})
        assert result is not None
        {{ endpoint.test_assertions }}

    {% endfor %}

{% endfor %}

class TestHttpClient:
    """Tests for HttpClient."""

    def test_init_defaults(self):
        client = HttpClient()
        assert client.base_url == "{{ default_base_url }}"

    def test_init_custom(self):
        client = HttpClient(base_url="https://custom.example.com")
        assert client.base_url == "https://custom.example.com"

    @patch("requests.Session")
    def test_unwrap_success(self, mock_session_cls):
        client = HttpClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {{ success_envelope_example }}
        result = client._unwrap(mock_resp)
        assert result is not None

    @patch("requests.Session")
    def test_unwrap_error(self, mock_session_cls):
        client = HttpClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {{ error_envelope_example }}
        with pytest.raises(APIError):
            client._unwrap(mock_resp)
