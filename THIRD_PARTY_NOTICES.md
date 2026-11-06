# Third-party components

Kūhaku's own code is under the MIT License (see LICENSE). It ships with these
components from others, each under its own license:

| Component | Where | License |
|---|---|---|
| Inter typeface (Rasmus Andersson and the Inter Project Authors), renamed subsets "Kuhaku Name" and "Kuhaku Text" | `login/*.ttf` | SIL Open Font License 1.1 |
| CodeMirror 5.65.16 | `ide/vendor/codemirror/` | MIT |
| WirePlumber scripts and configuration (The WirePlumber project contributors) | `wireplumber/` | MIT |
| icalendar 7.3.0 | `lib/icalendar/` | BSD-2-Clause |
| recurring-ical-events 3.8.2 | `lib/recurring_ical_events/` | LGPL-3.0-or-later |
| x-wr-timezone 2.0.1 | `lib/x_wr_timezone.py` | LGPL-3.0-or-later |
| python-dateutil 2.9.0 | `lib/dateutil/` | Apache-2.0 / BSD-3-Clause |
| tzdata 2026.5 | `lib/tzdata/` | Apache-2.0 |
| click 8.5.0 | `lib/click/` | BSD-3-Clause |
| six 1.17.0 | `lib/six.py` | MIT |
| typing_extensions 4.16.0 | `lib/typing_extensions.py` | PSF-2.0 |

The full license texts of the Python libraries are in `lib/*.dist-info/`.
The OFL text for the fonts is in `login/OFL.txt`.

Hardware notes for the MacBook8,1 (sleep, speakers) follow
pmgart/macbook8-1-debian-guide; its files are not included and are fetched separately.

## The install image

The install image (`kuhaku-amd64.iso`) is a Debian 13 system with Kūhaku added. It contains Debian packages under their own licenses (GPL, LGPL, MIT, BSD and others; the text of each is in `/usr/share/doc/<package>/copyright` inside the system), firmware from Debian's non-free-firmware section under its redistributable licenses, and touchegg (GPL-3.0, github.com/JoseExposito/touchegg).

**Source code.** The source of every Debian package in the image is available from Debian at https://deb.debian.org/debian and, for the exact versions, https://snapshot.debian.org. Kūhaku's own source is this repository. If you would like the complete corresponding source of any GPL-licensed part on a medium, open an issue in this repository and it will be provided at no more than the cost of making the copy; this offer is valid for three years after the release that contained it.

Software that is **not** in the image: Claude Code and the Claude desktop app are downloaded from Anthropic on the installed computer and are covered by Anthropic's own terms.
