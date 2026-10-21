CREATE TABLE IF NOT EXISTS hazard_readings (
    drone_id SMALLINT NOT NULL,
    sequence INTEGER NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    sensors JSONB NOT NULL,
    cell_x INTEGER NOT NULL,
    cell_y INTEGER NOT NULL,
    risk DOUBLE PRECISION NOT NULL,
    risk_label TEXT NOT NULL,
    prediction_source TEXT NOT NULL,
    synthetic_prediction BOOLEAN NOT NULL,
    weather JSONB NOT NULL,
    PRIMARY KEY (drone_id, sequence, observed_at)
);

CREATE INDEX IF NOT EXISTS hazard_readings_observed_at_idx
    ON hazard_readings (observed_at DESC);

CREATE INDEX IF NOT EXISTS hazard_readings_cell_idx
    ON hazard_readings (cell_x, cell_y, observed_at DESC);

CREATE TABLE IF NOT EXISTS alert_acknowledgements (
    alert_id UUID PRIMARY KEY,
    cell_x INTEGER NOT NULL,
    cell_y INTEGER NOT NULL,
    acknowledged_by TEXT NOT NULL,
    acknowledged_at TIMESTAMPTZ NOT NULL
);
