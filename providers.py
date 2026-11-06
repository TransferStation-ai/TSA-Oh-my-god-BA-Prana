from dataclasses import dataclass


def normalize_base_url(base_url: str) -> str:
    return base_url.strip().rstrip("/")


def preset_vault_key(preset_name: str) -> str:
    return f"preset:{preset_name}"


def linked_vault_key(provider_id: str) -> str:
    return f"linked:{provider_id}"


def custom_vault_key(base_url: str) -> str:
    return f"custom:{normalize_base_url(base_url)}"


def resolve_main_vault_key(base_url: str) -> str:
    for provider in BUILTIN_PROVIDERS:
        if provider.matches(base_url):
            return preset_vault_key(provider.name)
    return custom_vault_key(base_url)


def make_key_hint(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 4:
        return "…"
    return f"…{api_key[-4:]}"


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    base_url: str
    image_model: str
    video_model: str

    def matches(self, base_url: str) -> bool:
        return normalize_base_url(base_url) == self.base_url


BUILTIN_PROVIDERS = (
    ProviderPreset(
        name="Agnes AI",
        base_url="https://apihub.agnes-ai.com/v1",
        image_model="agnes-image-2.0-flash",
        video_model="agnes-video-2.5",
    ),
    ProviderPreset(
        name="OpenAI（GPT 官方）",
        base_url="https://api.openai.com/v1",
        image_model="gpt-image-1",
        video_model="sora-2",
    ),
    ProviderPreset(
        name="kimi（kimi官方）",
        base_url="https://api.moonshot.cn/v1",
        image_model="kimi-k3.1-pro",
        video_model="kimi-k3.1-pro",
    ),
)
