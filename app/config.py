from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    classifier_backend: str = "mock"

    typesafe_api_key: str = ""
    typesafe_model: str = "jev-latest"
    typesafe_base_url: str = "https://api.typesafe.ai"

    # Translate non-Latin notes to English before classifying. Off by default:
    # turn it on only if Jev measurably struggles with Arabic notes, since it
    # adds a second provider to the path and a call per unique note.
    translate_notes: bool = False
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_base_url: str = "https://openrouter.ai"

    confidence_threshold: float = 0.70

    # How similar two tidied vendor names must be to be treated as one vendor.
    # High on purpose: a wrong merge corrupts the household's history quietly,
    # while a missed one just leaves two rows they can merge by hand.
    vendor_match_threshold: float = 0.92

    database_url: str = "sqlite:///./masroufi.db"


settings = Settings()
