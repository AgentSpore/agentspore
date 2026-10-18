CREATE TABLE family_training_attempts (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    attempt_id UUID NOT NULL,
    first_seen_day DATE NOT NULL,
    curriculum_version INTEGER NOT NULL CHECK (curriculum_version BETWEEN 1 AND 1000000),
    lang TEXT NOT NULL CHECK (lang IN (
        'en', 'ru', 'es', 'pt', 'fr', 'de', 'it', 'nl', 'pl', 'uk', 'cs', 'sk',
        'ro', 'hu', 'el', 'tr', 'ar', 'he', 'fa', 'hi', 'bn', 'ur', 'id', 'ms',
        'vi', 'th', 'ko', 'ja', 'zh-Hans', 'sw', 'tl'
    )),
    answers BOOLEAN[] NOT NULL CHECK (
        cardinality(answers) BETWEEN 0 AND 10
        AND (cardinality(answers) = 0 OR (array_ndims(answers) = 1 AND array_lower(answers, 1) = 1))
        AND array_position(answers, NULL) IS NULL
    ),
    completed BOOLEAN NOT NULL CHECK (NOT completed OR cardinality(answers) = 10),
    PRIMARY KEY (project_id, attempt_id)
);

CREATE INDEX family_training_cohort_idx
    ON family_training_attempts (project_id, curriculum_version, first_seen_day);
CREATE INDEX family_training_expiry_idx ON family_training_attempts (first_seen_day);
