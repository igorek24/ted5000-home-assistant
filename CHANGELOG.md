# Changelog

## 0.1.0 — 2026-07-31

Initial release. Developed against a live TED5000 with the solar package,
2 MTUs (Grid + Solar) and 4 Spyders (23 wired circuits).

- Net / consumption / solar power, energy and cost sensors
- Per-MTU power, voltage, power factor, apparent power
- Per-circuit (Spyder) power and energy, named from the gateway config,
  skipping groups with no CT assigned
- Utility rate, projected bill, days left in billing period
- Energy dashboard support (total_increasing kWh sensors)
- Config flow with optional credentials, options for polling interval and
  per-circuit energy sensors, diagnostics
- Uses the endpoints modern TED5000 firmware actually serves, rather than
  the `LiveData.xml` endpoint the built-in integration requires

## 0.1.1 — 2026-07-31

- Added line-to-line (~240 V) voltage sensors for the gateway and each MTU,
  derived from the reported leg voltage (auto-detects whether the MTU is
  wired line-to-neutral or line-to-line)
- Existing voltage sensors relabelled "(leg)" for clarity
- Integration renamed to simply "TED5000 Pro"
