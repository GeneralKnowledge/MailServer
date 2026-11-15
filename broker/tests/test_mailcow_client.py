import httpx
import pytest

from broker.mailcow import MailcowClient, MailcowError


def test_internal_hostname_sets_host_header_and_sni():
    seen = {}

    def handler(req):
        seen["host"] = req.headers["host"]
        seen["sni"] = req.extensions.get("sni_hostname")
        seen["key"] = req.headers["x-api-key"]
        return httpx.Response(200, json={})

    mc = MailcowClient("https://nginx-mailcow", "k", tls_hostname="mail.example.com",
                       transport=httpx.MockTransport(handler))
    assert mc.mailbox_exists("a@example.com") is False
    assert seen == {"host": "mail.example.com", "sni": "mail.example.com", "key": "k"}


def test_redirect_is_not_followed():
    mc = MailcowClient("https://x", "k", transport=httpx.MockTransport(
        lambda r: httpx.Response(302, headers={"location": "https://evil"})))
    with pytest.raises(MailcowError):
        mc.add_mailbox(local_part="a", domain="example.com", password="p" * 12, quota_mb=1)


def test_unexpected_payload_is_unavailable():
    mc = MailcowClient("https://x", "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")))
    with pytest.raises(MailcowError) as e:
        mc.add_mailbox(local_part="a", domain="example.com", password="p" * 12, quota_mb=1)
    assert e.value.kind == "unavailable"
