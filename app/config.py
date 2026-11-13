from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    redis_url: str
    secret_key: str
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""    
    access_token_expire_minutes: int = 30

    model_config = SettingsConfigDict(env_file=".env")
    rate_limit_enabled: bool = True    


settings = Settings()
