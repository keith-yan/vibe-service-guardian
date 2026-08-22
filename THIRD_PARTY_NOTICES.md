# Third-party notices

Source and portable builds are licensed under the project MIT license. Portable build scripts run `scripts/Collect-ThirdPartyLicenses.py` against the exact build environment and place complete upstream texts in `THIRD_PARTY_LICENSES/`, a checksum manifest in `THIRD_PARTY_LICENSES/MANIFEST.json`, and an SPDX 2.3 inventory in `SBOM.spdx.json`. A build fails if any required license text cannot be located.

## CPython 3.x

- Project: <https://www.python.org/>
- License: Python Software Foundation License and the additional historical terms contained in CPython's `LICENSE.txt`
- Use: embedded runtime in PyInstaller portable executables

The precise Python version and complete composite license are collected from the interpreter used for each build.

## psutil 7.2.2

- Project: <https://github.com/giampaolo/psutil>
- License: BSD 3-Clause
- Use: runtime process/system inspection, Windows network/service inspection, and macOS process metrics

Copyright (c) 2009, Jay Loden, Dave Daeschler, Giampaolo Rodola. Redistribution and use in source and binary forms, with or without modification, are permitted under the conditions stated in the upstream BSD 3-Clause license. The portable build contains the complete text copied from the installed `psutil` distribution.

## PyInstaller 6.22.2

- Project: <https://github.com/pyinstaller/pyinstaller>
- License: GPL-2.0-or-later with a special exception for distributing bundled applications
- Use: build tool and bundled bootloader/runtime support

Portable builds contain the exact installed PyInstaller `COPYING.txt`, including the bootloader exception applicable to bundled applications.

## Design references

System Informer, DevServer MCP, RustNet, Paperclip documentation, SystemManager, Windows-MCP, Hermes Agent, OpenCode, Aider, Gemini CLI and Goose were reviewed as public design references. No source code from those repositories is copied into Vibe Service Guardian.
