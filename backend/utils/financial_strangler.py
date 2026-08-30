"""
Financial Strangler Pattern Router — Phase F.

This module provides a strangler pattern wrapper for gradual migration of financial
writes from MongoDB to Postgres. Routes use this to gate writes based on the
canonical ``financial_pg_writes_enabled`` toggle, which is resolved through the
finance cutover contract.

Pattern:
1. Check the canonical write toggle value for the building
2. If true (Postgres enabled): delegate to financial_core service
3. If false (MongoDB enabled): use the existing MongoDB code path

This allows zero-downtime cutover with instant rollback via toggle.
"""

import logging
from typing import Callable, Any, TypeVar, Coroutine
from functools import wraps

from services.cutover_config_service import (
    FINANCIAL_PG_WRITES_ENABLED,
    is_cutover_feature_enabled,
)

logger = logging.getLogger(__name__)

T = TypeVar('T')


class StranglerGate:
    """Gate that routes financial writes based on toggle."""

    def __init__(self, toggle_key: str = FINANCIAL_PG_WRITES_ENABLED):
        """Generated function header.

        Function: StranglerGate.__init__
        Path: backend/utils/financial_strangler.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.toggle_key = toggle_key

    async def is_postgres_enabled(self, building_id: str) -> bool:
        """
        Check if Postgres financial writes are enabled for this building.
        """
        try:
            return await is_cutover_feature_enabled(building_id, self.toggle_key)
        except Exception as e:
            logger.warning(
                f"Failed to check {self.toggle_key} for {building_id}: {e}. "
                f"Defaulting to MongoDB (safe path)."
            )
            return False

    def gate_write(
            self,
            postgres_handler: Callable[..., Coroutine[Any, Any, T]],
            mongodb_handler: Callable[..., Coroutine[Any, Any, T]],
    ) -> Callable[..., Coroutine[Any, Any, T]]:
        """
        Decorate a financial write endpoint to gate between Postgres and MongoDB.

        Usage:
            @router.post("/payments/record")
            async def record_payment(data, current_user, building_id):
                gate = StranglerGate(FINANCIAL_PG_WRITES_ENABLED)
                return await gate.route(
                    postgres_handler=lambda: postgres_record_payment(data, current_user, building_id),
                    mongodb_handler=lambda: mongodb_record_payment(data, current_user, building_id),
                    building_id=building_id,
                )

        Parameters
        ----------
        postgres_handler : async callable
            Handler for Postgres financial_core path (Phase F implementation)
        mongodb_handler : async callable
            Handler for MongoDB fallback path (existing code)

        Returns
        -------
        callable
            Async wrapper that gates between the two paths
        """

        async def route(building_id: str):
            """Generated function header.

            Function: StranglerGate.route
            Path: backend/utils/financial_strangler.py

            Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
            """
            try:
                postgres_enabled = await self.is_postgres_enabled(building_id)
            except Exception as e:
                logger.warning(
                    f"Toggle check failed for {building_id}; falling back to MongoDB: {e}",
                    exc_info=True
                )
                postgres_enabled = False

            if postgres_enabled:
                logger.info(f"Routing financial write to Postgres (toggle enabled for {building_id})")
                return await postgres_handler()
            else:
                logger.info(f"Routing financial write to MongoDB (toggle disabled or check failed for {building_id})")
                return await mongodb_handler()

        return route

    def gate_decorator(self, building_id_param: str = "building_id"):
        """
        Decorator form of gate_write for cleaner endpoint code.

        Usage:
            gate = StranglerGate(FINANCIAL_PG_WRITES_ENABLED)

            @router.post("/levy-payments")
            @gate.decorator("building_id")
            async def record_payment(data, current_user, building_id):
                # This function receives both postgres and mongodb as kwargs
                # and can decide based on context or use gate.route() inside
                pass
        """

        def decorator(endpoint_func: Callable) -> Callable:
            """Generated function header.

            Function: StranglerGate.decorator
            Path: backend/utils/financial_strangler.py

            Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
            """
            @wraps(endpoint_func)
            async def wrapper(*args, **kwargs) -> Any:
                """Generated function header.

                Function: StranglerGate.wrapper
                Path: backend/utils/financial_strangler.py

                Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
                """
                building_id = kwargs.get(building_id_param)
                if not building_id:
                    raise ValueError(f"Missing {building_id_param} in decorator kwargs")

                # Pass toggle state to endpoint so it can decide routing
                is_postgres_enabled = await self.is_postgres_enabled(building_id)
                kwargs["use_postgres_financial"] = is_postgres_enabled
                kwargs["strangler_gate"] = self

                return await endpoint_func(*args, **kwargs)

            return wrapper

        return decorator


# Module-level instance for easy import. The canonical write toggle keeps the
# adapter aligned with the rest of the finance cutover contract.
default_gate = StranglerGate(FINANCIAL_PG_WRITES_ENABLED)


async def route_financial_write(
        operation_name: str,
        building_id: str,
        postgres_handler: Callable[..., Coroutine[Any, Any, T]],
        mongodb_handler: Callable[..., Coroutine[Any, Any, T]],
) -> T:
    """
    Route a financial write operation between Postgres and MongoDB.

    This is the simplest form for inline use in endpoints.

    Parameters
    ----------
    operation_name : str
        Name of the operation (for logging)
    building_id : str
        Building ID to check toggle for
    postgres_handler : async callable
        Handler for Postgres path
    mongodb_handler : async callable
        Handler for MongoDB path

    Returns
    -------
    T
        Result from whichever handler runs

    Example
    -------
    result = await route_financial_write(
        "record_payment",
        building_id,
        postgres_handler=lambda: financial_core.record_payment(cmd),
        mongodb_handler=lambda: db.levy_payments.insert_one(doc),
    )
    """
    if await default_gate.is_postgres_enabled(building_id):
        logger.info(f"{operation_name}: routing to Postgres (enabled for {building_id})")
        return await postgres_handler()
    else:
        logger.info(f"{operation_name}: routing to MongoDB (disabled for {building_id})")
        return await mongodb_handler()


def log_migration_event(
        operation: str,
        status: str,
        building_id: str,
        details: dict = None,
) -> None:
    """
    Log financial operation for post-migration audit.

    Useful for tracking which operations went through which path during
    the gradual rollout period.

    Parameters
    ----------
    operation : str
        Operation name (e.g., "record_payment", "create_levy")
    status : str
        Status (e.g., "postgres_routed", "mongodb_fallback", "divergence_detected")
    building_id : str
        Building ID
    details : dict
        Optional additional details to log
    """
    log_data = {
        "operation": operation,
        "status": status,
        "building_id": building_id,
    }
    if details:
        log_data.update(details)

    if "divergence" in status.lower():
        logger.warning(f"Financial operation divergence: {log_data}")
    else:
        logger.info(f"Financial operation: {log_data}")
