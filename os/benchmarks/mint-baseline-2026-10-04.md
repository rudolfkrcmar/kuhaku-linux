# Baseline: Linux Mint 22.3 XFCE + Kūhaku (MacBook8,1, Core M-5Y31, 8 GB) — 2026-10-04

Measured after the push_status loop fix, ~15 min after boot, Claude app + Kansei open, on battery.
Source: Kansei screenshot ~/Pictures/Screenshots/Screenshot 2026-10-04 at 00.19.10.png + commands.

| What | Mint + Kūhaku |
|---|---|
| Boot to login (systemd-analyze) | 13.95 s (kernel 5.55 + userspace 8.40); earlier boot 11.0 s |
| System services running | 31 |
| User services running | 23 |
| Processes | 209–210 (106 kernel threads) |
| Memory used / available | 2.0–2.2 GB used, 5.4–5.8 GB free, cache 4.3 GB, swap 8 KB |
| Processor, near idle | 27 % (load 1.27), 2.1 GHz, package 69 °C |
| Power draw on battery | 13.1 W, 1 h 36 m left at 76 % (battery health 69 %, 636 cycles) |
| Disk | 49 GB used of 229 GB; 1859 packages installed |
| Biggest programs | Claude app ×9: 1.2 GB; web engine ×4: 509 MB; Kūhaku 205 MB; Xorg 175 MB; Kansei 166 MB (9.7 % CPU); xfwm4 98 MB |
| Temperatures | SSD 42, chipset 57, battery 37, CPU 64–69 °C |

Compare on Debian + Kūhaku under the same conditions (same apps open, on battery, ~15 min after boot).
