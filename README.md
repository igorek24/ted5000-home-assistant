# TED5000 Pro for Home Assistant

Home Assistant integration for **The Energy Detective TED5000** whole-house
energy monitors — including the **solar / net-metering package** and
**Spyder submetering** (up to 32 individual circuits).

Everything is local polling of the gateway's own XML API. No cloud, no
Footprints software, no YAML.

> **Why not the built-in `ted5000` integration?**
> Home Assistant ships a legacy `ted5000` sensor platform that polls
> `/api/LiveData.xml`. Later TED5000 gateway firmware **does not serve that
> endpoint** (it returns `404`), so the built-in integration simply cannot
> work on those units — which is what sent me here. This integration uses
> the endpoints those gateways actually expose, and reads far more from them.

## Features

- **Whole-house flows** — net, consumption and solar production, each with
  live power, energy today, energy this month, and cost
- **Solar aware** — production sensors appear automatically when the
  gateway reports generation; values are exposed as positive figures
- **Per-circuit submetering** — every wired Spyder group becomes its own
  device with power and energy sensors, using the names you configured in
  TED (e.g. `Oven`, `Dryer`, `AC`, `FRIDGE/R`, `POOL L1`)
- **Per-MTU sensors** — power, voltage, power factor, apparent power
- **Billing** — utility rate, projected bill for the month, days left in
  the billing period, meter read day
- **Energy dashboard ready** — energy sensors are `total_increasing` kWh,
  so grid consumption, solar production and individual devices all plug
  straight in
- Config flow, options (polling interval, optional per-circuit energy
  sensors), diagnostics

## Installation

### HACS

HACS → ⋮ → **Custom repositories** → `https://github.com/igorek24/ted5000-home-assistant`,
type **Integration** → Download → restart Home Assistant.

### Manual

Copy `custom_components/ted5000_pro` into `config/custom_components/` and restart.

## Configuration

Settings → Devices & Services → **Add Integration** → **TED5000 Pro** →
enter the gateway's IP address (the network box, not the display).
Username/password are only needed if you enabled security in Footprints.

The integration reads your MTU and Spyder layout from the gateway, so
circuits arrive already named — and groups that aren't wired to a CT
(`UseCT = 0`) are skipped instead of cluttering HA with dead sensors.

### Options

| Option | Default | Meaning |
|---|---|---|
| Polling interval (seconds) | `10` | The gateway updates about once a second; 10 s is a good balance. 5–300 allowed. |
| Per-circuit energy sensors | on | Creates `energy today` / `energy this month` for each circuit. Turn off if you only want live power. |

## Entities

**Gateway device**

| Entity | Notes |
|---|---|
| `sensor.ted5000_net_power` / `_consumption_power` / `_solar_power` | Live watts |
| `sensor.ted5000_*_energy_today` / `_energy_this_month` | kWh, `total_increasing` |
| `sensor.ted5000_*_cost_today` / `_cost_this_month` | Dollars |
| `sensor.ted5000_projected_bill_this_month` | The gateway's own projection |
| `sensor.ted5000_utility_rate` | $/kWh from your TED rate settings |
| `sensor.ted5000_days_left_in_billing_period` | With `meter_read_day` attribute |
| `sensor.ted5000_line_voltage` | Leg voltage (line-to-neutral, ~120 V) |
| `sensor.ted5000_line_to_line_voltage` | Line-to-line (~240 V), derived from the leg voltage |

**Per MTU** (e.g. *Grid*, *Solar*): power, leg voltage, line-to-line voltage, power factor, apparent power (disabled by default).

### A note on 120 V vs 240 V

A TED MTU reports a **single** voltage, which on a US split-phase service is
one leg (line-to-neutral, ~120 V) — that is all the gateway's API exposes,
and it is what the Footprints UI reads too. This integration also publishes
a **line-to-line** figure (~240 V) derived from it: doubled when the reading
looks like a leg (< 150 V), or passed through when the MTU is wired across
both legs and already reads ~240 V. Derived sensors carry a
`derived_from_leg_voltage` attribute so it is obvious which is measured.

**Per circuit** (one device per Spyder group): power, energy today, energy this month.

## Energy dashboard

Settings → Dashboards → Energy:

- **Grid consumption** → `sensor.ted5000_consumption_energy_this_month`
- **Return to grid** (if you export) → `sensor.ted5000_solar_energy_this_month`
- **Solar production** → `sensor.ted5000_solar_energy_this_month`
- **Individual devices** → any per-circuit `energy this month` sensor

Month-to-date sensors are the better choice here: they reset once a month
on your meter read day, which `total_increasing` handles cleanly.

## Example automations

```yaml
automation:
  - alias: "Notify when the dryer finishes"
    trigger:
      - platform: numeric_state
        entity_id: sensor.dryer_power
        below: 50
        for: "00:05:00"
    condition:
      - condition: numeric_state
        entity_id: sensor.dryer_energy_today
        above: 0.5
    action:
      - action: notify.mobile_app
        data: { message: "Dryer is done." }

  - alias: "Run the pool pump on surplus solar"
    trigger:
      - platform: numeric_state
        entity_id: sensor.ted5000_net_power
        below: -1000          # exporting more than 1 kW
        for: "00:10:00"
    action:
      - action: switch.turn_on
        target: { entity_id: switch.pool_pump }
```

## How it works

| Endpoint | Used for |
|---|---|
| `SystemSettings.xml` | MTU list, Spyder groups, circuit names, `UseCT` wiring mask |
| `SystemOverview.xml` | Per-MTU power, KVA, power factor, voltage |
| `SpyderData.xml` | Per-circuit now / today / month-to-date |
| `DashData.xml?T&D&M` | Totals: `T=0` energy, `T=1` cost; `D=0` net, `D=1` consumption, `D=2` production |
| `Rate.xml` | Rate, billing period, meter read day |

Units from the gateway are scaled here: voltage is decivolts, power factor
is per-mille, energy is Wh, cost is cents, and the rate is
1/100000 dollars per kWh.

## Troubleshooting

- **Cannot connect** — browse to `http://<gateway-ip>/api/SystemOverview.xml`.
  You should get XML. If you get a login prompt, add the credentials in the
  config flow.
- **No solar sensors** — they only appear if the gateway reports generation
  (`DashData` with `D=2`). Check your TED solar/net-metering configuration.
- **A circuit is missing** — groups with no CT assigned are skipped by
  design. Assign the CT in Footprints and reload the integration.
- **Debug logging**: `logger: logs: custom_components.ted5000_pro: debug`
- **Diagnostics**: integration entry → ⋮ → Download diagnostics.

## Trademarks and disclaimer

Unofficial and unaffiliated. "The Energy Detective", "TED", "TED5000" and
"Spyder" are trademarks of their respective owner, used here only to
identify the hardware this integration works with. No vendor logo or brand
artwork is included — the icon is original artwork for this project.
