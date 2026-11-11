import redis
from fastapi import Request

from app.config import settings
from app.core.errors import AppError

redis_client = redis.Redis.from_url(
    settings.redis_url, decode_responses=True, socket_connect_timeout=1
)


def hit(key: str, limit: int, window: int) -> None:
    """Zabilježi jedan pokušaj; ako je limit prekoračen, baci 429."""
    if not settings.rate_limit_enabled:
        return
    try:
        redis_client.set(key, 0, ex=window, nx=True)  # napravi brojač sa rokom
        count = redis_client.incr(key)
        ttl = redis_client.ttl(key)
    except redis.RedisError:
        return  # ako Redis padne, API i dalje radi
    if count > limit:
        retry = max(ttl, 1)
        raise AppError(
            429,
            "TOO_MANY_REQUESTS",
            f"Previše pokušaja. Pokušaj ponovo za {retry} s",
            headers={"Retry-After": str(retry)},
        )


def rate_limit(name: str, limit: int, window: int):
    """Limit po IP adresi, koristi se kao Depends(...) na endpointu."""

    def dependency(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        hit(f"rl:{name}:{ip}", limit, window)

    return dependency