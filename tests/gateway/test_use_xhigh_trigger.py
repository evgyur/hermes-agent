from gateway.run import GatewayRunner


def test_use_xhigh_trigger_accepts_plain_prefixes():
    assert GatewayRunner._is_use_xhigh_gpt55_trigger("use xhigh fix this")
    assert GatewayRunner._is_use_xhigh_gpt55_trigger("use xhigh reasoning: fix this")
    assert GatewayRunner._is_use_xhigh_gpt55_trigger("use xhigh reasoning — fix this")
    assert GatewayRunner._is_use_xhigh_gpt55_trigger("xhigh reasoning gpt fix this")
    assert GatewayRunner._is_use_xhigh_gpt55_trigger("xhigh gpt-5.5: fix this")
    assert GatewayRunner._is_use_xhigh_gpt55_trigger("Xhigh reasoning gpt не доступен?")


def test_use_xhigh_trigger_accepts_telegram_sender_and_context_wrappers():
    assert GatewayRunner._is_use_xhigh_gpt55_trigger('[Evgeny "Chip"] use xhigh fix it')
    assert GatewayRunner._is_use_xhigh_gpt55_trigger(
        "[Earlier context]\nhello\n\n[New message]\n[Evgeny \"Chip\"] use xhigh reasoning fix it"
    )


def test_use_xhigh_trigger_rejects_incidental_mentions():
    assert not GatewayRunner._is_use_xhigh_gpt55_trigger("please explain how to use xhigh")
    assert not GatewayRunner._is_use_xhigh_gpt55_trigger("I might use xhigh reasoning later")
    assert not GatewayRunner._is_use_xhigh_gpt55_trigger("use high reasoning")


def test_xhigh_runtime_config_is_exact():
    assert GatewayRunner._gpt55_xhigh_reasoning_config() == {"enabled": True, "effort": "xhigh"}
