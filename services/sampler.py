from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import psutil


HISTORY_KEYS = (
    "cpu",
    "ram_active",
    "net_up",
    "net_down",
    "disk_read",
    "disk_write",
)

# Match yenai-plugin's 12-hour Redis history TTL.
HISTORY_TTL_SECONDS = 12 * 60 * 60


class ResourceSampler:
    """Collects lightweight system snapshots and keeps chart history."""

    def __init__(
        self,
        *,
        interval: int = 60,
        history_size: int = 60,
        logger=None,
    ) -> None:
        self.interval = max(5, int(interval or 60))
        self.history_size = max(2, int(history_size or 60))
        self.logger = logger

        self.latest: dict[str, Any] | None = None
        self.history: dict[str, list[list[float]]] = {key: [] for key in HISTORY_KEYS}
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._persist_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._sample_count = 0
        self._last_net: dict[str, Any] = {}
        self._last_disk: dict[str, Any] = {}
        self._last_time: float | None = None
        self._stopping = False

    def set_persist_callback(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        self._persist_cb = callback

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="system-status-sampler")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._persist()

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.sample(wait=0.5 if self._sample_count == 0 else 0.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.logger:
                    self.logger.exception("系统状态采样失败: %s", exc)
            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                raise

    async def sample(self, *, wait: float = 0.0) -> dict[str, Any]:
        async with self._lock:
            if wait:
                await asyncio.sleep(wait)
            snapshot = await asyncio.to_thread(self._sample_sync)
            self.latest = snapshot
            self._append_history(snapshot)
            self._sample_count += 1
            if self._persist_cb and self._sample_count % 5 == 0:
                await self._persist()
            return snapshot

    async def get_snapshot(self, *, max_age: float = 120.0) -> dict[str, Any]:
        if self.latest is not None:
            age = time.time() - float(self.latest.get("ts", 0))
            if age <= max_age:
                return self.latest
        return await self.sample(wait=0.5)

    def _sample_sync(self) -> dict[str, Any]:
        now = time.time()
        if self._sample_count == 0:
            cpu_percent = psutil.cpu_percent(interval=0.2)
        else:
            cpu_percent = psutil.cpu_percent(interval=None)

        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()

        net_data = None
        try:
            counters = psutil.net_io_counters(pernic=True)
            iface, current = self._pick_network_interface(counters)
            previous = self._last_net.get(iface)
            elapsed = (
                now - self._last_time
                if self._last_time is not None and self._last_time > 0
                else 0.0
            )
            rx_sec = tx_sec = 0.0
            if previous is not None and elapsed > 0:
                rx_sec = max(
                    0.0, (current.bytes_recv - previous.bytes_recv) / elapsed
                )
                tx_sec = max(
                    0.0, (current.bytes_sent - previous.bytes_sent) / elapsed
                )
            net_data = {
                "iface": iface,
                "rx_sec": rx_sec,
                "tx_sec": tx_sec,
                "rx_bytes": current.bytes_recv,
                "tx_bytes": current.bytes_sent,
            }
            self._last_net = {iface: current}
        except Exception:
            net_data = None

        disk_io: list[dict[str, Any]] = []
        disk_read_sec = 0.0
        disk_write_sec = 0.0
        try:
            disk_counters = psutil.disk_io_counters(perdisk=True) or {}
            elapsed = (
                now - self._last_time
                if self._last_time is not None and self._last_time > 0
                else 0.0
            )
            whole_disk_pattern = re.compile(
                r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)$"
            )
            candidate_counters = {
                name: counter
                for name, counter in disk_counters.items()
                if not name.startswith(("loop", "ram", "zram"))
            }
            physical = {
                name: counter
                for name, counter in candidate_counters.items()
                if whole_disk_pattern.match(name)
            }
            if physical:
                candidate_counters = physical
            for name, current in candidate_counters.items():
                previous = self._last_disk.get(name)
                read_sec = write_sec = 0.0
                if previous is not None and elapsed > 0:
                    read_sec = max(
                        0.0, (current.read_bytes - previous.read_bytes) / elapsed
                    )
                    write_sec = max(
                        0.0, (current.write_bytes - previous.write_bytes) / elapsed
                    )
                if current.read_bytes or current.write_bytes:
                    disk_io.append(
                        {
                            "name": name,
                            "rIO_sec": read_sec,
                            "wIO_sec": write_sec,
                        }
                    )
                disk_read_sec += read_sec
                disk_write_sec += write_sec
            self._last_disk = dict(candidate_counters)
            disk_io.sort(key=lambda item: item["rIO_sec"] + item["wIO_sec"], reverse=True)
        except Exception:
            disk_io = []

        self._last_time = now
        return {
            "ts": now,
            "cpu": max(0.0, min(100.0, float(cpu_percent or 0.0))),
            "ram_percent": float(memory.percent),
            "ram_used": memory.used,
            "ram_total": memory.total,
            "ram_available": memory.available,
            "ram_buffers": getattr(memory, "buffers", 0),
            "ram_cached": getattr(memory, "cached", 0),
            "ram_active": getattr(memory, "active", memory.used),
            "swap_percent": float(swap.percent),
            "swap_used": swap.used,
            "swap_total": swap.total,
            "swap_free": swap.free,
            "net": net_data,
            "disk_io": disk_io,
            "disk_read_sec": disk_read_sec,
            "disk_write_sec": disk_write_sec,
        }

    @staticmethod
    def _pick_network_interface(counters: dict[str, Any]) -> tuple[str, Any]:
        candidates = [
            (name, counter)
            for name, counter in counters.items()
            if name != "lo" and not name.startswith(("docker", "br-", "veth"))
        ]
        if not candidates:
            candidates = list(counters.items())
        if not candidates:
            raise RuntimeError("no network interface")
        return max(candidates, key=lambda item: item[1].bytes_sent + item[1].bytes_recv)

    def _append_history(self, snapshot: dict[str, Any]) -> None:
        ts = round(float(snapshot.get("ts", time.time())) * 1000, 1)
        values = {
            "cpu": snapshot.get("cpu", 0.0),
            "ram_active": snapshot.get("ram_active", snapshot.get("ram_used", 0.0)),
            "net_up": (snapshot.get("net") or {}).get("tx_sec", 0.0),
            "net_down": (snapshot.get("net") or {}).get("rx_sec", 0.0),
            "disk_read": snapshot.get("disk_read_sec", 0.0),
            "disk_write": snapshot.get("disk_write_sec", 0.0),
        }
        cutoff_ms = (time.time() - HISTORY_TTL_SECONDS) * 1000
        for key, value in values.items():
            series = self.history.setdefault(key, [])
            series.append([ts, round(float(value or 0.0), 3)])
            if len(series) > self.history_size:
                del series[: len(series) - self.history_size]
            if series and series[0][0] < cutoff_ms:
                series[:] = [point for point in series if point[0] >= cutoff_ms]

    def load_history(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        cutoff_ms = (time.time() - HISTORY_TTL_SECONDS) * 1000
        for key in HISTORY_KEYS:
            series = data.get(key)
            if not isinstance(series, list):
                continue
            cleaned: list[list[float]] = []
            for point in series[-self.history_size :]:
                if (
                    isinstance(point, (list, tuple))
                    and len(point) >= 2
                    and isinstance(point[0], (int, float))
                    and isinstance(point[1], (int, float))
                    and point[0] >= cutoff_ms
                ):
                    cleaned.append([float(point[0]), float(point[1])])
            self.history[key] = cleaned

    def export_history(self) -> dict[str, list[list[float]]]:
        return {key: [list(point) for point in series] for key, series in self.history.items()}

    async def _persist(self) -> None:
        if not self._persist_cb:
            return
        try:
            await self._persist_cb(self.export_history())
        except Exception as exc:
            if self.logger:
                self.logger.warning("保存系统状态监控历史失败: %s", exc)
