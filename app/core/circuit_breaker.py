import time
import json
from typing import Callable, Any, Optional
from app.cache.redis import get_redis_client
from app.core.logger import get_logger

logger = get_logger(__name__)

class CircuitBreaker:
    """
    Distributed Circuit Breaker using Redis for shared state across workers.
    """
    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.redis_key = f"circuit_breaker:{name}"

    async def _get_state(self) -> dict:
        client = await get_redis_client()
        state = await client.hgetall(self.redis_key)
        if not state:
            return {"state": "closed", "failures": 0, "last_failure_time": 0}
        return {
            "state": state.get("state", "closed"),
            "failures": int(state.get("failures", 0)),
            "last_failure_time": float(state.get("last_failure_time", 0))
        }

    async def _set_state(self, state: str, failures: int, last_failure_time: float):
        client = await get_redis_client()
        await client.hset(self.redis_key, mapping={
            "state": state,
            "failures": failures,
            "last_failure_time": last_failure_time
        })

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        state_data = await self._get_state()
        state = state_data["state"]
        last_failure_time = state_data["last_failure_time"]
        failures = state_data["failures"]

        if state == "open":
            if time.time() - last_failure_time > self.recovery_timeout:
                logger.info(f"Circuit {self.name} half-opening...")
                state = "half-open"
                await self._set_state(state, failures, last_failure_time)
            else:
                raise RuntimeError(f"Circuit {self.name} is OPEN. Service unavailable.")

        try:
            result = await func(*args, **kwargs)
            await self._on_success(state_data)
            return result
        except Exception as e:
            await self._on_failure(state_data)
            raise e

    async def _on_success(self, state_data: dict):
        if state_data["state"] == "half-open":
            logger.info(f"Circuit {self.name} recovered. Closing.")
        await self._set_state("closed", 0, 0)

    async def _on_failure(self, state_data: dict):
        failures = state_data["failures"] + 1
        last_failure_time = time.time()
        state = state_data["state"]

        if failures >= self.failure_threshold:
            logger.error(f"Circuit {self.name} OPENING.")
            state = "open"
        
        await self._set_state(state, failures, last_failure_time)
        logger.warning(f"Circuit {self.name} failure {failures}/{self.failure_threshold}")

# Distributed Instances
llm_breaker = CircuitBreaker("llm", failure_threshold=5, recovery_timeout=60)
whatsapp_breaker = CircuitBreaker("whatsapp", failure_threshold=5, recovery_timeout=60)
db_breaker = CircuitBreaker("database", failure_threshold=3, recovery_timeout=30)
