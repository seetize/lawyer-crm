from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Callable

from app.catalog.competitors import compute_competitors
from app.catalog.db import CatalogRepository
from app.catalog.discovery import DiscoveryError, YandexCityDiscovery
from app.catalog.domain import CitySpec, CrawlSummary, DiscoveryCursor, JobStatus
from app.providers.yandex import YandexMapsProvider


logger = logging.getLogger(__name__)


class CityCatalogService:
    def __init__(
        self,
        repository: CatalogRepository,
        discovery: YandexCityDiscovery,
        detail_provider: YandexMapsProvider,
        *,
        max_pages: int = 8,
        max_partition_depth: int = 2,
        refresh_hours: int = 168,
    ) -> None:
        self.repository = repository
        self.discovery = discovery
        self.detail_provider = detail_provider
        self.max_pages = max(1, min(max_pages, 40))
        self.max_partition_depth = max(0, min(max_partition_depth, 4))
        self.refresh_hours = max(1, refresh_hours)

    async def crawl_city(
        self,
        city: CitySpec,
        categories: list[str],
        *,
        force: bool = False,
        progress: Callable[[CrawlSummary], None] | None = None,
    ) -> list[CrawlSummary]:
        city_id = await asyncio.to_thread(self.repository.ensure_city, city)
        summaries: list[CrawlSummary] = []
        for category in categories:
            category_id = await asyncio.to_thread(
                self.repository.ensure_category, category
            )
            job_id = await asyncio.to_thread(
                self.repository.prepare_job,
                city_id,
                category_id,
                city.scope,
                provider=getattr(self.discovery, "provider", "test"),
                force=force,
                refresh_hours=self.refresh_hours,
            )
            owner = f"{socket.gethostname()}:{os.getpid()}"
            claimed = await asyncio.to_thread(
                self.repository.claim_job, job_id, owner
            )
            if claimed:
                await self._run_job(job_id, category_id, city, category)
            summary = await asyncio.to_thread(self.repository.finish_job, job_id)
            summaries.append(summary)
            if progress:
                progress(summary)
        if summaries and all(summary.status == JobStatus.COMPLETED for summary in summaries):
            await asyncio.to_thread(
                self.repository.reconcile_completed_jobs,
                [summary.job_id for summary in summaries],
            )
        return summaries

    async def _run_job(
        self,
        job_id: str,
        category_id: str,
        city: CitySpec,
        category: str,
    ) -> None:
        partitions = await asyncio.to_thread(
            self.repository.pending_partitions, job_id
        )
        for partition_id, scope, saved_cursor in partitions:
            cursor = saved_cursor
            seen_hashes: set[str] = set()
            try:
                while cursor.page < self.max_pages:
                    page = await self.discovery.fetch_page(
                        city, category, scope, cursor
                    )
                    repeated = page.raw_hash in seen_hashes and bool(page.cards)
                    seen_hashes.add(page.raw_hash)
                    if repeated:
                        if scope.depth < self.max_partition_depth:
                            await asyncio.to_thread(
                                self.repository.split_partition,
                                partition_id,
                                scope.split(),
                            )
                        else:
                            await asyncio.to_thread(
                                self.repository.fail_partition,
                                partition_id,
                                "repeated_page_truncated",
                            )
                        break
                    await asyncio.to_thread(
                        self.repository.save_page,
                        job_id,
                        partition_id,
                        category_id,
                        page.cards,
                        cursor=page.cursor,
                        next_cursor=page.next_cursor,
                        total_hint=page.total_hint,
                        raw_hash=page.raw_hash,
                    )
                    if page.next_cursor is None:
                        break
                    cursor = page.next_cursor
                else:
                    if scope.depth < self.max_partition_depth:
                        await asyncio.to_thread(
                            self.repository.split_partition,
                            partition_id,
                            scope.split(),
                        )
                    else:
                        await asyncio.to_thread(
                            self.repository.fail_partition,
                            partition_id,
                            "page_cap_truncated",
                        )
            except DiscoveryError as error:
                await asyncio.to_thread(
                    self.repository.fail_partition,
                    partition_id,
                    error.code,
                    blocked=error.blocked,
                )
            except Exception:
                # Persist the checkpoint and let the next leased run retry safely.
                logger.exception("Unexpected city discovery failure")
                await asyncio.to_thread(
                    self.repository.fail_partition,
                    partition_id,
                    "unexpected_discovery_failure",
                )

        # New child partitions are processed in the same run without redoing parents.
        remaining = await asyncio.to_thread(self.repository.pending_partitions, job_id)
        if remaining and {item[0] for item in remaining} != {item[0] for item in partitions}:
            await self._run_job(job_id, category_id, city, category)

    async def enrich_pending(self, limit: int = 10) -> dict[str, int]:
        cards = await asyncio.to_thread(
            self.repository.pending_yandex_cards,
            limit,
            self.refresh_hours,
        )
        completed = failed = 0
        for card in cards:
            try:
                profile = await self.detail_provider.collect_by_id(
                    card["provider_id"]
                )
                await asyncio.to_thread(
                    self.repository.save_profile,
                    card["location_id"],
                    profile,
                )
                completed += 1
            except Exception as error:
                # A failed detail fetch must not invalidate discovery data.
                status = getattr(getattr(error, "response", None), "status_code", None)
                code = type(error).__name__
                if status is not None:
                    code = f"{code}_{status}"
                await asyncio.to_thread(
                    self.repository.record_detail_failure,
                    card["provider_id"],
                    code,
                )
                failed += 1
        return {"completed": completed, "failed": failed}

    async def rebuild_competitors(self, city_name: str) -> int:
        features = await asyncio.to_thread(
            self.repository.location_features, city_name
        )
        matches = compute_competitors(features)
        await asyncio.to_thread(
            self.repository.replace_competitors,
            matches,
            [feature["id"] for feature in features],
        )
        return len(matches)
