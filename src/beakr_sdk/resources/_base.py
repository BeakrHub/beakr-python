"""Re-exports of :mod:`beakr_sdk._resource`, where the resource base class lives.

It sits outside this package so the generated layer (``beakr_sdk._generated``)
can build on it without importing every resource module first.
"""

from beakr_sdk._resource import MAX_PAGE_SIZE, Resource, as_list, collect_pages

__all__ = ["MAX_PAGE_SIZE", "Resource", "as_list", "collect_pages"]
