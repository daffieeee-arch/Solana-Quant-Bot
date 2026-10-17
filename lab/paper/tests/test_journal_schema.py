"""paper/journal_schema.json is journal.py's TABLES: the Rust live route compiles the file into its journal writer
(lab/live/src/journal.rs), so a column added here reaches it only through the file. Regenerate with
`python -m paper.journal > paper/journal_schema.json`."""

import json

from paper import journal


def test_the_schema_file_is_current():
    with open(journal.SCHEMA_FILE) as f:
        assert json.load(f) == journal.schema()
