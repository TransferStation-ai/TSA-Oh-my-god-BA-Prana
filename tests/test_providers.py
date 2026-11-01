import providers
from providers import (
    BUILTIN_PROVIDERS,
    custom_vault_key,
    linked_vault_key,
    make_key_hint,
    preset_vault_key,
    resolve_main_vault_key,
)


def test_vault_key_forms() -> None:
    assert preset_vault_key("Agnes AI") == "preset:Agnes AI"
    assert linked_vault_key("agnes") == "linked:agnes"
    assert custom_vault_key("https://x.test/v1/") == "custom:https://x.test/v1"


def test_resolve_main_vault_key() -> None:
    for preset in BUILTIN_PROVIDERS:
        assert resolve_main_vault_key(preset.base_url) == preset_vault_key(preset.name)
        # 带尾部斜杠也要解析到 preset
        assert resolve_main_vault_key(preset.base_url + "/") == preset_vault_key(preset.name)
    assert resolve_main_vault_key("https://x.test/v1") == "custom:https://x.test/v1"


def test_make_key_hint() -> None:
    assert make_key_hint("") == ""
    assert make_key_hint("abc") == "…"
    assert make_key_hint("sk-project-secret-1234") == "…1234"
