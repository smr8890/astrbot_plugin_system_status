from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from .utils import bytes_parts, cfg, color_for_latency, color_for_status


async def collect_network(config: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    network_data = snapshot.get("net")
    speed_data = None
    if network_data:
        upload = bytes_parts(network_data.get("tx_sec"))
        download = bytes_parts(network_data.get("rx_sec"))
        traffic_upload = bytes_parts(network_data.get("tx_bytes"))
        traffic_download = bytes_parts(network_data.get("rx_bytes"))
        speed_data = {
            "speed": {"upload": upload, "download": download},
            "traffic": {
                "upload": traffic_upload,
                "download": traffic_download,
            },
        }

    ps_test = await network_test(config)
    return {
        "speed": speed_data,
        "psTest": ps_test,
        "chart_svg": "",
    }


async def network_test(config: Any) -> list[dict[str, str]] | None:
    show = cfg(config, "psTestSites.show", "pro")
    if show is False:
        return None
    sites = cfg(config, "psTestSites.list", []) or []
    if not sites:
        return None
    timeout = float(cfg(config, "psTestSites.timeout", 5000) or 5000) / 1000
    concurrency = max(1, int(cfg(config, "psTestSites.concurNum", 5) or 5))
    semaphore = asyncio.Semaphore(concurrency)

    async def check(site: dict[str, Any]) -> dict[str, str]:
        name = str(site.get("name") or site.get("url") or "site")
        url = str(site.get("url") or "")
        if not url:
            return {
                "name": name,
                "status": "<span style='color:#F44336'>-</span>",
                "delay": "<span style='color:#F44336'>Error</span>",
            }
        async with semaphore:
            start = time.perf_counter()
            try:
                timeout_cfg = aiohttp.ClientTimeout(total=timeout)
                async with aiohttp.ClientSession(
                    timeout=timeout_cfg, trust_env=True
                ) as session:
                    async with session.get(url, allow_redirects=True) as response:
                        status = response.status
                        await response.read()
                delay = (time.perf_counter() - start) * 1000
                return {
                    "name": name,
                    "status": f"<span style='color:{color_for_status(status)}'>{status}</span>",
                    "delay": f"<span style='color:{color_for_latency(delay)}'>{delay:.0f}ms</span>",
                }
            except asyncio.TimeoutError:
                return {
                    "name": name,
                    "status": "<span style='color:#F44336'>-</span>",
                    "delay": "<span style='color:#F44336'>Timeout</span>",
                }
            except Exception:
                return {
                    "name": name,
                    "status": "<span style='color:#F44336'>-</span>",
                    "delay": "<span style='color:#F44336'>Error</span>",
                }

    return list(await asyncio.gather(*(check(site) for site in sites)))
