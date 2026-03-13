"""HTTP client for meican API.

Auth flow (captured via CDP recording):
  1. POST gateway.meican.com/graphql?op=LoginByAuthWay  →  ticket, signature, snowflakeId
  2. POST gateway.meican.com/graphql?op=ChooseAccountLogin  →  accessToken, refreshToken
  3. All REST calls carry: Authorization: bearer <token>, clientId, clientSecret, query client_id/client_secret
  4. Token refresh: POST /v2.1/oauth/token  grant_type=refresh_token
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

# App-level constants (from meican frontend JS bundles)
DEFAULT_CLIENT_ID = "Xqr8w0Uk4ciodqfPwjhav5rdxTaYepD"
DEFAULT_CLIENT_SECRET = "vD11O6xI9bG3kqYRu9OyPAHkRGxLh4E"
GATEWAY_URL = "https://gateway.meican.com/graphql"
# Gateway uses different client credentials
GATEWAY_CLIENT_ID = "WYAiIJZPc8e21UHcKHVUeVo2SpNVrni"
GATEWAY_CLIENT_SECRET = "WbRV03U0MyQzRhXrvXhyopkavkIRaBg"

TOKEN_FILE = Path.home() / ".meican" / "token.json"

LOGIN_BY_AUTH_WAY_QUERY = """mutation LoginByAuthWay($input: LoginByAuthWayInput!) {
  loginByAuthWay(input: $input) {
    ...UserCenterSsoV2LoginByAuthWayView
    __typename
  }
}
fragment UserCenterSsoV2LoginByAuthWayView on UserCenterSsoV2LoginByAuthWayView {
  data {
    ticket
    signature
    userList {
      snowflakeId
      name
      type
      __typename
    }
    __typename
  }
  __typename
}"""

CHOOSE_ACCOUNT_LOGIN_QUERY = """mutation ChooseAccountLogin($input: ChooseAccountLoginInput!) {
  chooseAccountLogin(input: $input) {
    ...TokenOutput
    __typename
  }
}
fragment TokenOutput on TokenOutput {
  token {
    accessToken
    refreshToken
    expiry
    tokenType
    __typename
  }
  __typename
}"""


class APIError(Exception):
    def __init__(self, code: Any, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"API error {code}: {detail}")


def _load_tokens() -> dict:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    return {}


def _save_tokens(tokens: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


class HttpClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        self.base_url = (
            base_url
            or os.environ.get("MEICAN_BASE_URL", "https://www.meican.com/forward/api")
        ).rstrip("/")
        self.client_id = client_id or os.environ.get("MEICAN_CLIENT_ID", DEFAULT_CLIENT_ID)
        self.client_secret = client_secret or os.environ.get("MEICAN_CLIENT_SECRET", DEFAULT_CLIENT_SECRET)
        self.session = requests.Session()
        self.session.headers["clientId"] = self.client_id
        self.session.headers["clientSecret"] = self.client_secret

        self._tokens = _load_tokens()
        if token:
            self._tokens["access_token"] = token
        elif os.environ.get("MEICAN_TOKEN"):
            self._tokens["access_token"] = os.environ["MEICAN_TOKEN"]
        self._apply_auth()

    def _apply_auth(self):
        at = self._tokens.get("access_token")
        if at:
            self.session.headers["Authorization"] = f"bearer {at}"

    @property
    def _default_params(self) -> dict:
        return {"client_id": self.client_id, "client_secret": self.client_secret}

    def login(self, email: str, password: str) -> dict:
        """Two-step GraphQL login: LoginByAuthWay → ChooseAccountLogin."""
        # Step 1: LoginByAuthWay
        resp1 = requests.post(
            GATEWAY_URL,
            params={"op": "LoginByAuthWay"},
            json={
                "operationName": "LoginByAuthWay",
                "variables": {"input": {
                    "authMethod": "EmailPasswordAuth",
                    "email": email,
                    "password": password,
                }},
                "query": LOGIN_BY_AUTH_WAY_QUERY,
            },
            headers={"clientid": GATEWAY_CLIENT_ID, "clientsecret": GATEWAY_CLIENT_SECRET},
        )
        resp1.raise_for_status()
        d1 = resp1.json()
        if d1.get("errors"):
            msg = d1["errors"][0].get("message", str(d1["errors"]))
            raise APIError("login_failed", msg)
        login_data = (d1.get("data") or {}).get("loginByAuthWay", {}).get("data", {})
        ticket = login_data.get("ticket")
        signature = login_data.get("signature")
        users = login_data.get("userList", [])
        if not ticket or not users:
            raise APIError("login_failed", f"LoginByAuthWay failed: {d1}")
        snowflake_id = users[0]["snowflakeId"]

        # Step 2: ChooseAccountLogin
        resp2 = requests.post(
            GATEWAY_URL,
            params={"op": "ChooseAccountLogin"},
            json={
                "operationName": "ChooseAccountLogin",
                "variables": {"input": {
                    "ticket": ticket,
                    "snowflakeId": snowflake_id,
                    "signature": signature,
                }},
                "query": CHOOSE_ACCOUNT_LOGIN_QUERY,
            },
            headers={
                "clientid": self.client_id,
                "clientsecret": self.client_secret,
            },
        )
        resp2.raise_for_status()
        d2 = resp2.json()
        if d2.get("errors"):
            msg = d2["errors"][0].get("message", str(d2["errors"]))
            raise APIError("login_failed", msg)
        token_data = (d2.get("data") or {}).get("chooseAccountLogin", {}).get("token", {})
        at = token_data.get("accessToken")
        rt = token_data.get("refreshToken")
        if not at:
            raise APIError("login_failed", f"ChooseAccountLogin failed: {d2}")

        self._tokens = {
            "access_token": at,
            "refresh_token": rt or "",
            "expires_at": time.time() + int(token_data.get("expiry", 3600)),
        }
        _save_tokens(self._tokens)
        self._apply_auth()
        return {"access_token": at, "user": users[0].get("name", "")}

    def refresh(self) -> dict:
        rt = self._tokens.get("refresh_token")
        if not rt:
            raise APIError("no_refresh_token", "No refresh token. Run 'login' first.")
        resp = self.session.post(
            self.base_url + "/v2.1/oauth/token",
            params=self._default_params,
            data={"grant_type": "refresh_token", "refresh_token": rt},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        if "access_token" not in data:
            raise APIError("refresh_failed", str(data))
        self._tokens = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", rt),
            "expires_at": time.time() + data.get("expires_in", 3600),
        }
        _save_tokens(self._tokens)
        self._apply_auth()
        return data

    def _ensure_auth(self) -> None:
        if not self._tokens.get("access_token"):
            return
        expires_at = self._tokens.get("expires_at", 0)
        if expires_at and time.time() > expires_at - 60:
            try:
                self.refresh()
            except Exception:
                pass

    def _unwrap(self, resp: requests.Response) -> Any:
        try:
            data = resp.json()
        except Exception:
            resp.raise_for_status()
            return resp.text
        if "resultCode" in data:
            if data["resultCode"] != "OK":
                raise APIError(data["resultCode"], data.get("resultDescription", str(data)))
            return data.get("data", data)
        if "code" in data:
            if data["code"] != 0:
                raise APIError(data["code"], data.get("msg", str(data)))
            return data.get("data", data)
        if "error" in data:
            raise APIError(data["error"], data.get("error_description", str(data)))
        return data

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        self._ensure_auth()
        merged = {**self._default_params, **(params or {})}
        return self._unwrap(self.session.get(self.base_url + path, params=merged))

    def post(self, path: str, payload: Optional[dict] = None) -> Any:
        self._ensure_auth()
        return self._unwrap(self.session.post(
            self.base_url + path, params=self._default_params, json=payload or {}))

    def post_form(self, path: str, data: Optional[dict] = None) -> Any:
        self._ensure_auth()
        return self._unwrap(self.session.post(
            self.base_url + path, params=self._default_params, data=data or {},
            headers={**dict(self.session.headers), "Content-Type": "application/x-www-form-urlencoded"}))

    def post_pay(self, payment_slip_id: str, order_resp: dict) -> Any:
        """Call the payment API. Requires signature fields from orders/add response."""
        order = order_resp.get("order", {})
        resp = self.session.post(
            "https://meican-pay-checkout-bff.meican.com/api/v2/payment-slips/pay",
            json={"paymentSlipId": payment_slip_id, "themeName": "default"},
            headers={
                **dict(self.session.headers),
                "Content-Type": "application/json",
                "x-mcco-signature": order.get("signature", ""),
                "x-mcco-timestamp": str(order.get("timestamp", "")),
                "x-mcco-merchant-id": order.get("mchId", ""),
                "x-mcco-nonce-str": order.get("nonceStr", ""),
                "x-mcco-client-id": self.client_id,
                "x-mcco-client-secret": self.client_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise APIError(data.get("code"), data.get("msg", str(data)))
        return data.get("data", data)
