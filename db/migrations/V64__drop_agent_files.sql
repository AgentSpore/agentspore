-- destructive: agent_files mirror removed; runner workspace (persistent bind mount) is the sole source of truth since live-FS epic (P1-P5a). Data redundant — runner disk holds live files.
DROP TABLE IF EXISTS agent_files;
