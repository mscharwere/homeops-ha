# homeops-ha

Home Assistant custom component that bridges **HomeOps** ↔ Home Assistant.

## Sprint 1 — Maintenance Domain

### Sensors

| Entity | Description |
|--------|-------------|
| `sensor.homeops_maint_pick_morning` | Top-pick maintenance item for morning surface |
| `sensor.homeops_maint_pick_evening` | Top-pick maintenance item for evening surface |
| `sensor.homeops_maint_pick_weekend` | Top-pick maintenance item for weekend surface |
| `sensor.homeops_maint_overdue_count` | Count of overdue maintenance items |
| `binary_sensor.homeops_maint_due_today` | ON when overdue_count > 0 |

Pick sensors: state = item label (or `"none"`), attributes = `catalog_id`, `duration_min`, `task_type`, `requires_supply`, `urgency`, `category`.

### Buttons

One `button.homeops_maint_complete_<item>` and one `button.homeops_maint_snooze_<item>` are created dynamically for every catalog item returned by `GET /api/maintenance/counters`.

- **Complete** → `POST /api/maintenance/completions`
- **Snooze** → `POST /api/maintenance/counters/:id/snooze` (7 days)

## Installation

1. Copy `custom_components/homeops/` into your HA `custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings → Integrations → Add Integration → HomeOps**.
4. Enter your HomeOps URL (e.g. `http://192.168.30.x:4070`) and API key (`WEBHOOK_SECRET` from HomeOps `.env`).

## Configuration

| Field | Default | Description |
|-------|---------|-------------|
| HomeOps URL | — | Base URL of the HomeOps backend |
| API Key | — | `WEBHOOK_SECRET` from HomeOps `.env` |
| Poll interval | 5 min | How often to refresh counters and picks |
