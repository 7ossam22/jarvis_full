# Linux System Controller

The **Linux System Controller** provides comprehensive local OS automation, diagnostics, media playback control, and safe shell execution for Linux environments.

## Capabilities

- **Audio Volume & Mute** (`system_set_volume`): Controls audio sinks across PipeWire (`wpctl`), PulseAudio (`pactl`), and ALSA (`amixer`).
- **Media Playback** (`system_media_control`): Controls media playback (Spotify, VLC, browser video) via MPRIS / `playerctl` / DBus (`play`, `pause`, `next`, `previous`, `stop`).
- **Hardware Diagnostics** (`system_get_stats`): Monitors real-time CPU load averages, RAM utilization, root disk storage, and system uptime.
- **Application Launcher** (`system_launch_app`): Launches desktop applications (e.g. Spotify, VS Code, Terminal, Calculator). Web requests are routed automatically to [[Playwright Browser Control]].
- **Screen Security** (`system_lock_screen`): Locks active desktop user session via `loginctl` or `xdg-screensaver`.
- **Desktop Screenshots** (`system_take_screenshot`): Captures full display screenshots (`scrot`, `maim`, `import`) and displays them in the 3/4-screen screenshot reference viewer.
- **Safe Command Execution** (`system_run_command`): Executes non-destructive inspection shell commands with hard guardrails against destructive operations.

## Related Systems

- [[Safety Protocol & Guardrails]] for enforcing strict override confirmation on shell actions.
- [[Zen White Glassmorphic UI]] for displaying desktop screenshots in the 3/4-screen viewer.
- [[Playwright Browser Control]] for specialized browser automation.
