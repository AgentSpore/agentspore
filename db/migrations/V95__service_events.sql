CREATE TABLE service_event_counts (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    event_day DATE NOT NULL,
    event TEXT NOT NULL CHECK (event IN ('form_open', 'png_download')),
    model TEXT NOT NULL CHECK (model IN ('iphone-x', 'iphone-xr', 'pixel-8')),
    lang TEXT NOT NULL CHECK (lang IN ('en', 'ru')),
    count BIGINT NOT NULL CHECK (count > 0),
    PRIMARY KEY (project_id, event_day, event, model, lang)
);
