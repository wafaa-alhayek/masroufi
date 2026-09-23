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

    # Shelf life depends on how hot it actually is. With the weather off, the
    # seeded shelf-life labels are used as-is; with it on, Open-Meteo supplies a
    # daily maximum and the Q10 model decides which list an item belongs on.
    # Open-Meteo needs no API key.
    weather_enabled: bool = False
    latitude: float = 31.5017
    longitude: float = 34.4668
    open_meteo_base_url: str = "https://api.open-meteo.com"

    # LPG burn rates in kg per hour, per burner. Averages over unknown stoves and
    # pots — exposed here so a household that knows its own cylinder can correct
    # them.
    burner_kg_per_hour: float = 0.25
    simmer_kg_per_hour: float = 0.10

    # Prices are never stored as facts, only as dated observations. Weight halves
    # every half-life, so last week's price dominates one from three months ago
    # instead of being averaged flat into it. Short by default: prices here move.
    price_half_life_days: float = 14.0
    price_stale_after_days: int = 30
    price_spread_window_days: int = 30

    confidence_threshold: float = 0.70

    # How similar two tidied vendor names must be to be treated as one vendor.
    # High on purpose: a wrong merge corrupts the household's history quietly,
    # while a missed one just leaves two rows they can merge by hand.
    vendor_match_threshold: float = 0.92

    database_url: str = "sqlite:///./masroufi.db"


settings = Settings()
