from dataclasses import dataclass


def normalize_base_url(base_url: str) -> str:
    return base_url.strip().rstrip("/")


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
)
