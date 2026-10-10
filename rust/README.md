# Rust port — INCOMPLETE / STARTING POINT ONLY

**This is not a working application.** It is a partial port that was written
without a Rust toolchain available, so it has never been compiled and may not
build as-is. The supported, working application is the Python one in the parent
directory (`../pipewire-audio-gui.py`).

## What is here

| File | Status |
| --- | --- |
| `Cargo.toml` | Complete. Dependency versions were looked up (not guessed): `gtk4` 0.11, `glib`/`gio` 0.22, `serde` 1, `serde_json` 1, `ksni` 0.3, `base64` 0.23. |
| `src/backend.rs` | Logically complete port of the tested `pw-dump` / `pw-metadata` code (tolerant of both pw-dump JSON shapes, type-coerced rates, `pw-metadata` settings parsing). Never compiled. |
| `src/config.rs` | Logically complete port of the mapping store / resolver / allowed-rates and the `flatpak-spawn --host` config writes. Never compiled. |
| `src/main.rs` | **Missing.** The GTK4 UI was not finished. |

## To finish it

1. Add a `fn is_flatpak() -> bool` (checks `/.flatpak-info`) — `config.rs` calls
   it as `crate::is_flatpak()`.
2. Write `src/main.rs` with the GTK4 UI: device dropdown, sample-rate mode
   (Auto / Fixed) with Up/Down stepping, per-device bit depth, the per-rate
   mapping table, the tray watcher, and the log view. Mirror the behaviour of
   `../pipewire-audio-gui.py`.
3. `cargo build`, then `cargo clippy`, and fix whatever the compiler reports.
   Expect several rounds: this code has never seen a compiler.

## Verified-working alternative

Use the Python app instead — it is built and tested:

```sh
python3 ../pipewire-audio-gui.py
```