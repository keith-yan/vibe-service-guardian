# Linux validation status

Vibe Service Guardian 0.8.0 targets graphical Linux distributions, with Ubuntu 22.04 LTS or newer as the primary validation baseline. The console is a loopback-only local Web UI opened in the user's default graphical browser.

## Included support

- Linux process and TCP/UDP listener collection through psutil/procfs visibility.
- Project and agent-session attribution, Docker grouping, model-runtime probes, resource/traffic/disk/sensor collection, and guarded local actions.
- Generic NVIDIA and AMD adapter discovery using driver tools when installed and Linux PCI sysfs as a model-independent fallback.
- NVIDIA telemetry through `nvidia-smi`; AMD telemetry through `amd-smi` on AMD SMI/ROCm-supported systems.
- Read-only firewall evidence from ufw, firewalld, or nftables when visible to the current unprivileged user.
- freedesktop-compatible optional `.desktop` launcher installed without sudo.

## Native acceptance

Run on each target architecture:

```sh
chmod +x scripts/Build-Portable-Linux.sh scripts/Validate-Linux.sh
./scripts/Build-Portable-Linux.sh
```

Windows cannot prove Linux ELF compatibility, desktop integration, driver telemetry, or native runtime behavior. A source build kit produced on Windows is therefore not a Linux release binary and must pass this native validation before publication.
