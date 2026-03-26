#!/usr/bin/env python3
"""Mac System Monitor — Real-time terminal dashboard."""

import time
import psutil
import platform
import subprocess
from datetime import timedelta

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()

NERD_ICONS = True  # set False if your font doesn't support nerd icons


def bar(value: float, width: int = 20) -> Text:
    """Render a colored progress bar."""
    filled = int(value / 100 * width)
    bar_str = "█" * filled + "░" * (width - filled)
    if value >= 85:
        color = "bold red"
    elif value >= 60:
        color = "bold yellow"
    else:
        color = "bold green"
    t = Text()
    t.append(bar_str, style=color)
    t.append(f" {value:5.1f}%", style="white")
    return t


def bytes_human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def get_cpu_temp() -> str:
    """Try to get CPU temp via osx-cpu-temp or powermetrics."""
    try:
        result = subprocess.run(
            ["osx-cpu-temp"],
            capture_output=True, text=True, timeout=1
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "N/A"


def make_cpu_panel() -> Panel:
    freq = psutil.cpu_freq()
    freq_str = f"{freq.current:.0f} MHz" if freq else "N/A"
    cores = psutil.cpu_count(logical=False)
    threads = psutil.cpu_count(logical=True)
    usage = psutil.cpu_percent(percpu=True)
    overall = psutil.cpu_percent()
    temp = get_cpu_temp()

    t = Table.grid(padding=(0, 1))
    t.add_column(width=8, style="dim")
    t.add_column(width=28)

    t.add_row("Overall", bar(overall))
    t.add_row("", "")

    for i, pct in enumerate(usage):
        t.add_row(f"Core {i+1}", bar(pct))

    t.add_row("", "")
    t.add_row("Freq", Text(freq_str, style="cyan"))
    t.add_row("Cores", Text(f"{cores}C / {threads}T", style="cyan"))
    t.add_row("Temp", Text(temp, style="yellow"))

    return Panel(t, title="[bold cyan]  CPU", border_style="cyan", box=box.ROUNDED)


def make_ram_panel() -> Panel:
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()

    t = Table.grid(padding=(0, 1))
    t.add_column(width=8, style="dim")
    t.add_column(width=28)

    t.add_row("RAM", bar(vm.percent))
    t.add_row(
        "",
        Text(
            f"{bytes_human(vm.used)} / {bytes_human(vm.total)}",
            style="dim white",
        ),
    )
    t.add_row("", "")
    t.add_row("Swap", bar(swap.percent))
    t.add_row(
        "",
        Text(
            f"{bytes_human(swap.used)} / {bytes_human(swap.total)}",
            style="dim white",
        ),
    )
    t.add_row("", "")
    t.add_row("Avail", Text(bytes_human(vm.available), style="green"))
    t.add_row("Cached", Text(bytes_human(getattr(vm, "cached", 0) or 0), style="blue"))

    return Panel(t, title="[bold magenta]  RAM", border_style="magenta", box=box.ROUNDED)


def make_disk_panel() -> Panel:
    t = Table.grid(padding=(0, 1))
    t.add_column(width=12, style="dim", no_wrap=True)
    t.add_column(width=24)

    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except PermissionError:
            continue
        if usage.total == 0:
            continue
        name = part.mountpoint if len(part.mountpoint) <= 12 else "…" + part.mountpoint[-11:]
        t.add_row(name, bar(usage.percent))
        t.add_row(
            "",
            Text(f"{bytes_human(usage.used)} / {bytes_human(usage.total)}", style="dim white"),
        )

    io = psutil.disk_io_counters()
    if io:
        t.add_row("", "")
        t.add_row("Read", Text(bytes_human(io.read_bytes), style="green"))
        t.add_row("Write", Text(bytes_human(io.write_bytes), style="red"))

    return Panel(t, title="[bold yellow]  Disk", border_style="yellow", box=box.ROUNDED)


def make_network_panel(prev_net) -> tuple[Panel, object]:
    net = psutil.net_io_counters()
    interval = 1.0

    if prev_net:
        dl = (net.bytes_recv - prev_net.bytes_recv) / interval
        ul = (net.bytes_sent - prev_net.bytes_sent) / interval
    else:
        dl = ul = 0.0

    t = Table.grid(padding=(0, 1))
    t.add_column(width=10, style="dim")
    t.add_column(style="white")

    t.add_row("  Down", Text(f"{bytes_human(dl)}/s", style="bold green"))
    t.add_row("  Up", Text(f"{bytes_human(ul)}/s", style="bold red"))
    t.add_row("", "")
    t.add_row("Total ↓", Text(bytes_human(net.bytes_recv), style="green"))
    t.add_row("Total ↑", Text(bytes_human(net.bytes_sent), style="red"))
    t.add_row("Pkts ↓", Text(str(net.packets_recv), style="dim"))
    t.add_row("Pkts ↑", Text(str(net.packets_sent), style="dim"))

    return Panel(t, title="[bold green]  Network", border_style="green", box=box.ROUNDED), net


def make_battery_panel() -> Panel | None:
    batt = psutil.sensors_battery()
    if batt is None:
        return None

    pct = batt.percent
    plugged = batt.power_plugged
    secs = batt.secsleft

    if secs == psutil.POWER_TIME_UNLIMITED:
        time_str = "Charging ∞"
    elif secs == psutil.POWER_TIME_UNKNOWN:
        time_str = "Calculating…"
    else:
        time_str = str(timedelta(seconds=int(secs)))

    status_icon = "⚡" if plugged else "🔋"
    status_text = "Plugged in" if plugged else "On battery"

    t = Table.grid(padding=(0, 1))
    t.add_column(width=10, style="dim")
    t.add_column(style="white")

    t.add_row("Charge", bar(pct, width=15))
    t.add_row("Status", Text(f"{status_icon} {status_text}", style="cyan"))
    t.add_row("Time", Text(time_str, style="yellow"))

    return Panel(t, title="[bold yellow] Battery", border_style="yellow", box=box.ROUNDED)


def make_processes_panel() -> Panel:
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
        try:
            info = p.info
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x.get("cpu_percent") or 0, reverse=True)
    top = procs[:12]

    t = Table(box=box.SIMPLE, show_header=True, header_style="bold dim", padding=(0, 1))
    t.add_column("PID", width=7, style="dim")
    t.add_column("Name", width=22, no_wrap=True)
    t.add_column("CPU%", width=7)
    t.add_column("MEM%", width=7)
    t.add_column("Status", width=9)

    status_colors = {
        "running": "green",
        "sleeping": "dim",
        "idle": "dim",
        "stopped": "red",
        "zombie": "red",
    }

    for p in top:
        cpu = p.get("cpu_percent") or 0
        mem = p.get("memory_percent") or 0
        status = p.get("status", "?")
        color = status_colors.get(status, "white")
        t.add_row(
            str(p["pid"]),
            str(p["name"])[:22],
            Text(f"{cpu:.1f}", style="yellow" if cpu > 20 else "white"),
            Text(f"{mem:.1f}", style="magenta" if mem > 5 else "white"),
            Text(status, style=color),
        )

    return Panel(t, title="[bold white]  Top Processes", border_style="white", box=box.ROUNDED)


def make_header() -> Panel:
    uptime_secs = time.time() - psutil.boot_time()
    uptime_str = str(timedelta(seconds=int(uptime_secs)))
    hostname = platform.node()
    mac_ver = platform.mac_ver()[0]
    arch = platform.machine()
    ts = time.strftime("%H:%M:%S")

    t = Table.grid(expand=True)
    t.add_column(justify="left")
    t.add_column(justify="center")
    t.add_column(justify="right")

    left = Text()
    left.append(" MAC Monitor ", style="bold white on #1c1c2e")
    left.append(f"  macOS {mac_ver} ({arch})", style="dim")

    center = Text(hostname, style="bold cyan", justify="center")

    right = Text()
    right.append(f"{ts}  ", style="bold white")
    right.append(f"up {uptime_str}", style="dim")

    t.add_row(left, center, right)

    return Panel(t, style="on #0d0d1a", box=box.ROUNDED, border_style="bright_black")


def build_layout(prev_net):
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="top", size=14),
        Layout(name="middle", size=14),
        Layout(name="bottom"),
    )

    layout["top"].split_row(
        Layout(name="cpu"),
        Layout(name="ram"),
    )

    layout["middle"].split_row(
        Layout(name="disk"),
        Layout(name="net"),
    )

    net_panel, new_net = make_network_panel(prev_net)
    battery = make_battery_panel()

    layout["header"].update(make_header())
    layout["top"]["cpu"].update(make_cpu_panel())
    layout["top"]["ram"].update(make_ram_panel())
    layout["middle"]["disk"].update(make_disk_panel())
    layout["middle"]["net"].update(net_panel)

    if battery:
        layout["bottom"].split_row(
            Layout(name="procs"),
            Layout(name="battery", size=35),
        )
        layout["bottom"]["procs"].update(make_processes_panel())
        layout["bottom"]["battery"].update(battery)
    else:
        layout["bottom"].update(make_processes_panel())

    return layout, new_net


def main():
    console.clear()
    prev_net = None

    # warm up cpu percent (first call always returns 0)
    psutil.cpu_percent(percpu=True)
    time.sleep(0.1)

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        while True:
            try:
                layout, prev_net = build_layout(prev_net)
                live.update(layout)
                time.sleep(1)
            except KeyboardInterrupt:
                break

    console.print("\n[bold cyan]Monitor stopped. Goodbye! 👋[/bold cyan]")


if __name__ == "__main__":
    main()
