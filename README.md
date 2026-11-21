# Casambi Cloud for Home Assistant

A Home Assistant integration for [Casambi](https://casambi.com/) lighting networks, using the official **Casambi Cloud API** (REST + WebSocket). Changes made in the Casambi app or with wall switches show up in Home Assistant in real time, and Home Assistant can control the lights and scenes.

> This project is not affiliated with or endorsed by Casambi.

## Features

- **Lights**: on/off, brightness, colour temperature, RGB and RGBW, depending on what each luminaire supports. Capabilities are picked up automatically, even when a luminaire has no power when Home Assistant starts.
- **Resume level**: a plain *turn on* brings the light back to its last brightness instead of jumping to 100%. The level is kept across restarts.
- **Scenes**: Casambi scenes become Home Assistant scenes (hidden scenes are optional).
- **Real-time updates** over the Cloud WebSocket, with REST polling as a fallback and as a health check.
- **Diagnostics entities**: unit status and overheat, Cloud connection, real-time connection, gateway, last successful update, failure counters and last error category.
- **Repairs, reauthentication and reconfiguration** through the standard Home Assistant flows, plus an optional persistent notification when the connection has problems.
- **Diagnostics download** with credentials redacted.
- An extra action, `casambi_cloud.light_turn_on`, that sets brightness and, for fixtures that support it, light distribution (vertical) in one command.

## Requirements

- Home Assistant **2026.8** or later.
- A **Casambi Cloud API key**. Request one from Casambi Support (support@casambi.com). See the [Casambi developer site](https://developer.casambi.com/). Casambi says API key availability is currently limited. See [Requesting an API key](#requesting-an-api-key) for an example email.
- A **Casambi gateway** that keeps your network connected to the Cloud. This can be a phone or tablet running the Casambi app in gateway mode, or a dedicated gateway.
- Your Casambi **site (user) account email**, plus the site password, the network password, or both.

### Requesting an API key

Send an email to **support@casambi.com**. For example:

> **Subject:** Casambi Cloud API key request
>
> Hello,
>
> I'm building an app to control Casambi lights based on my home automations, and it would be great if I could use your REST API to configure my Casambi lights.
>
> Can you please advise on what I should do to obtain the API key?
>
> Thank you,
> *Your name*

## Installation

### Marketplace (recommended)

1. In Home Assistant, go to **Settings → Marketplace**.
2. Search for **Casambi Cloud** and install it. If it is not in the catalog yet, add this repository as a custom repository first: `https://github.com/Skoy87/ha-casambi-cloud`, category *Integration*.
3. Restart Home Assistant.

### Manual

1. Download the latest release.
2. Copy `custom_components/casambi_cloud` into `config/custom_components/` so that you have `config/custom_components/casambi_cloud/manifest.json`.
3. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & services → Add integration** and search for **Casambi Cloud**.
2. Enter your email, API key and at least one password.

Options (**Settings → Devices & services → Casambi Cloud → Configure**):

| Option | Default | Meaning |
|---|---|---|
| Poll interval | 60 s | How often the state is fetched over REST |
| Startup timeout | 90 s | Time allowed to reach the Cloud at startup |
| Command timeout | 10 s | Time allowed for each light command |
| Initial / maximum reconnect delay | 5 s / 300 s | Backoff for WebSocket reconnections |
| Failures before alerting | 3 | Consecutive failures before a repair or notification |
| Network refresh interval | 3600 s | How often units and scenes are re-read |
| Persistent notifications | on | Notify on connection problems |
| Include hidden scenes | off | Also create entities for hidden Casambi scenes |

## Moving from the older `casambi` integration or from 1.1.x

This integration uses the `casambi_cloud` domain, so Home Assistant treats it as a new integration:

1. Remove the old **Casambi** entry in **Settings → Devices & services**.
2. Delete `config/custom_components/casambi` and restart.
3. Install this integration and add it as described above.
4. Check your entity IDs. If they changed, rename them back in the entity settings, or update automations, scripts, groups and dashboards. Replace the `casambi.light_turn_on` action with `casambi_cloud.light_turn_on`.

## Troubleshooting

Enable debug logging:

```yaml
logger:
  logs:
    custom_components.casambi_cloud: debug
```

When opening an issue, attach the diagnostics file (**Settings → Devices & services → Casambi Cloud → ⋮ → Download diagnostics**). Credentials are redacted.

## Credits

Payload formats follow the official [Casambi developer documentation](https://developer.casambi.com/). The [aiocasambi](https://github.com/hellqvio86/aiocasambi) library by Olof Hellqvist (MIT License) was used as a reference for message shapes. This integration is a separate implementation with no runtime dependency on it.

## License

[MIT](LICENSE)
