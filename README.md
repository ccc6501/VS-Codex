# VS-Codex

## Hub launcher notes
- The hub now writes a `monky.pid` file the moment it boots so repeated launches reuse the existing process instead of colliding on port `5080`.
- Use `python launch_monky.py --status` to quickly see if the hub is already running.
- Stop an existing instance cleanly with `python launch_monky.py --stop`; this sends `SIGINT` first and escalates to `SIGTERM` if needed, clearing the PID file on exit.
- Regular launches (`python launch_monky.py`, add `--headless` or `--no-gui` as desired) remove the PID file automatically when the server shuts down.
