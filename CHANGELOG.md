# Changelog — Casambi Cloud integration

## 1.2.0 — 2026-11-21

First public release.

### Breaking
- The integration domain is now `casambi_cloud` (was `casambi`), so it no
  longer collides with the older community `casambi` integration and can be
  listed in the Marketplace. The folder is `custom_components/casambi_cloud`
  and the service is `casambi_cloud.light_turn_on`. Moving from 1.1.x means
  removing the old entry and adding the integration again (see the README).

### Fixed
- Units now link to their network device with `via_device_id`. The deprecated
  `via_device` key logged a warning and stops working in Home Assistant 2027.8.

### Added
- `README.md`, `LICENSE` (MIT), `hacs.json` and a validation workflow
  (hassfest + HACS action) for publishing.
- `manifest.json`: `codeowners`, `documentation` and `issue_tracker`.

## 1.1.2 — 2026-11-21

### Fixed
- Dimmable luminaires could stay stuck as `onoff` (no brightness slider) when
  their `Dimmer` control was not yet known when the entity was created — for
  example a luminaire without power at Home Assistant startup. The supported
  colour modes are now recomputed on every coordinator update, so the light
  becomes `brightness` (or richer) as soon as the Cloud reports its controls.
  Light groups built on these lights get the slider automatically.
- Capabilities are only upgraded, never downgraded: a unit that is temporarily
  offline or missing keeps the modes already detected.

### Files changed
- `custom_components/casambi/light.py`
- `custom_components/casambi/manifest.json` (version 1.1.1 → 1.1.2)

## 1.1.1 — 2026-10-31

### Fixed
- A plain `light.turn_on` (no `brightness` / `brightness_pct`) no longer forces the
  luminaire to 100%. It now resumes the last non-zero level; if the light is
  already on, the current level is kept. 100% is used only when no previous
  level is known.
- Same behaviour when turning on with a colour or colour temperature but no
  brightness.

### Added
- Each light remembers its last non-zero level (updated on every coordinator
  update, on turn-off and on explicit brightness commands).
- New state attribute `last_brightness` (0–255). It is restored after a Home
  Assistant restart via `RestoreEntity`, so the resume level survives reboots.

### Unchanged
- `light.turn_on` with an explicit brightness still sends a single
  `controlUnit` message with the requested `Dimmer` value.
- Legacy `casambi.light_turn_on` service (it now also updates the remembered
  level).

### Files changed
- `custom_components/casambi/light.py`
- `custom_components/casambi/manifest.json` (version 1.1.0 → 1.1.1)

## 1.1.0
- Clean-room Casambi Cloud integration (REST + WebSocket), config flow,
  diagnostics, scenes, sensors, repairs.
